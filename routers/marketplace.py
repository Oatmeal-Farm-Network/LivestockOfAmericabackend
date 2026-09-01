# routers/marketplace.py
# Farm-to-Restaurant Marketplace API
# Mount: app.include_router(marketplace_router, prefix="/api/marketplace")

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from database import blank_to_none
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam
from database import get_db, engine
from auth import get_current_user
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
import os

from image_service import ensure_images_for_catalog
from routers.translation import translate_fields, translate_list
from routers.notifications import notify_business

marketplace_router = APIRouter()


def _notify_standing_order_event(
    db: Session,
    standing_order_id: int,
    recipient_side: str,  # 'farm' or 'buyer'
    notification_type: str,
    title: str,
    body: str | None = None,
):
    """Fan a notification out to one side of a standing order.
    Recipient side is the BUSINESS being notified, not the initiator."""
    row = db.execute(text("""
        SELECT BuyerBusinessID, FarmBusinessID FROM RestaurantStandingOrders
        WHERE StandingOrderID = :id
    """), {"id": standing_order_id}).fetchone()
    if not row:
        return
    recipient_bid = row.FarmBusinessID if recipient_side == 'farm' else row.BuyerBusinessID
    if not recipient_bid:
        return
    link_path = '/farm/standing-orders' if recipient_side == 'farm' else '/restaurant/standing-orders'
    notify_business(
        db, business_id=recipient_bid,
        type=notification_type, title=title, body=body, link_path=link_path,
        entity_type='standing_order', entity_id=standing_order_id,
    )

# ── Auto-create MarketplaceProducts table ────────────────────────────────────
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='MarketplaceProducts')
        BEGIN
            CREATE TABLE MarketplaceProducts (
                ProductID          INT IDENTITY(1,1) PRIMARY KEY,
                BusinessID         INT NOT NULL,
                Title              VARCHAR(500) NOT NULL,
                Description        TEXT,
                CategoryName       VARCHAR(200),
                UnitPrice          DECIMAL(10,2) NOT NULL DEFAULT 0,
                WholesalePrice     DECIMAL(10,2),
                UnitLabel          VARCHAR(50) DEFAULT 'each',
                QuantityAvailable  DECIMAL(10,2) DEFAULT 0,
                MinOrderQuantity   DECIMAL(10,2) DEFAULT 1,
                ImageURL           VARCHAR(1000),
                Tags               VARCHAR(500),
                IsActive           BIT DEFAULT 1,
                IsFeatured         BIT DEFAULT 0,
                IsOrganic          BIT DEFAULT 0,
                Weight             DECIMAL(10,2),
                WeightUnit         VARCHAR(20),
                Color              VARCHAR(200),
                Size               VARCHAR(200),
                Material           VARCHAR(200),
                SKU                VARCHAR(100),
                DeliveryOptions    VARCHAR(200) DEFAULT 'pickup',
                CreatedAt          DATETIME DEFAULT GETDATE(),
                UpdatedAt          DATETIME DEFAULT GETDATE()
            )
        END
    """))

# ── Auto-create RestaurantSavedFarms table ───────────────────────────────────
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='RestaurantSavedFarms')
        BEGIN
            CREATE TABLE RestaurantSavedFarms (
                SavedID          INT IDENTITY(1,1) PRIMARY KEY,
                BuyerBusinessID  INT NOT NULL,
                FarmBusinessID   INT NOT NULL,
                AddedByPeopleID  INT NULL,
                Notes            NVARCHAR(500) NULL,
                CreatedAt        DATETIME NOT NULL DEFAULT GETDATE(),
                CONSTRAINT UQ_RestaurantSavedFarm UNIQUE (BuyerBusinessID, FarmBusinessID)
            )
        END
    """))

# ── Auto-create LivestockSavedItems table ────────────────────────────────────
# "My Animals" bookmarks: the animals, studs, and ranches a person has saved.
# An animal or stud row carries AnimalID; a ranch row carries BusinessID — the
# same ids /for-sale/{slug}, /studs/{slug} and /ranches/list/{slug} hand out.
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='LivestockSavedItems')
        BEGIN
            CREATE TABLE LivestockSavedItems (
                SavedID     INT IDENTITY(1,1) PRIMARY KEY,
                PeopleID    INT NOT NULL,
                ItemType    VARCHAR(20) NOT NULL,   -- 'animal' | 'stud' | 'ranch'
                AnimalID    INT NULL,               -- set for 'animal' / 'stud'
                BusinessID  INT NULL,               -- set for 'ranch'
                CreatedAt   DATETIME NOT NULL DEFAULT GETDATE()
            )
            -- Filtered, because SQL Server treats NULLs as equal in a unique
            -- index: an unfiltered constraint would let a person save only one
            -- ranch (every ranch row shares AnimalID = NULL), and vice versa.
            CREATE UNIQUE INDEX UQ_LivestockSaved_Animal
                ON LivestockSavedItems (PeopleID, ItemType, AnimalID)
                WHERE AnimalID IS NOT NULL
            CREATE UNIQUE INDEX UQ_LivestockSaved_Ranch
                ON LivestockSavedItems (PeopleID, ItemType, BusinessID)
                WHERE BusinessID IS NOT NULL
        END
    """))

# ── Auto-create RestaurantStandingOrders table ───────────────────────────────
# Recurring orders. ListingType + ListingSourceID identify a row in Produce/MeatInventory/ProcessedFood/SFProducts.
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='RestaurantStandingOrders')
        BEGIN
            CREATE TABLE RestaurantStandingOrders (
                StandingOrderID   INT IDENTITY(1,1) PRIMARY KEY,
                BuyerBusinessID   INT NOT NULL,
                FarmBusinessID    INT NOT NULL,
                ListingType       VARCHAR(20) NOT NULL,   -- 'produce' | 'meat' | 'processed_food' | 'sf'
                ListingSourceID   INT NOT NULL,
                ProductTitle      NVARCHAR(500) NULL,     -- snapshot for display when source row is gone
                Quantity          DECIMAL(10,2) NOT NULL DEFAULT 1,
                UnitLabel         NVARCHAR(50) NULL,
                Frequency         VARCHAR(20) NOT NULL DEFAULT 'weekly', -- weekly | biweekly | monthly
                DayOfWeek         INT NULL,               -- 0=Sun..6=Sat
                NextDeliveryDate  DATE NULL,
                Status            VARCHAR(20) NOT NULL DEFAULT 'active', -- active | paused | cancelled
                Notes             NVARCHAR(500) NULL,
                CreatedByPeopleID INT NULL,
                CreatedAt         DATETIME NOT NULL DEFAULT GETDATE(),
                UpdatedAt         DATETIME NOT NULL DEFAULT GETDATE()
            )
        END
    """))

# ── Add LastReminderSentFor column to RestaurantStandingOrders (idempotent) ──
# Tracks which NextDeliveryDate we've already sent a pre-delivery reminder for,
# so an hourly cron can run freely without sending duplicate reminders.
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (
            SELECT 1 FROM sys.columns
            WHERE Name = 'LastReminderSentFor' AND Object_ID = Object_ID('RestaurantStandingOrders')
        )
        BEGIN
            ALTER TABLE RestaurantStandingOrders ADD LastReminderSentFor DATE NULL
        END
    """))

# ── Auto-create RestaurantDigestSubscriptions table ──────────────────────────
# "Available this week" weekly email digest opt-in (per restaurant business).
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='RestaurantDigestSubscriptions')
        BEGIN
            CREATE TABLE RestaurantDigestSubscriptions (
                SubscriptionID    INT IDENTITY(1,1) PRIMARY KEY,
                BuyerBusinessID   INT NOT NULL UNIQUE,
                Email             NVARCHAR(320) NOT NULL,
                Frequency         VARCHAR(20) NOT NULL DEFAULT 'weekly',
                SavedFarmsOnly    BIT NOT NULL DEFAULT 0,  -- if 1, digest only includes saved farms
                Status            VARCHAR(20) NOT NULL DEFAULT 'active',
                LastSentAt        DATETIME NULL,
                CreatedAt         DATETIME NOT NULL DEFAULT GETDATE(),
                UpdatedAt         DATETIME NOT NULL DEFAULT GETDATE()
            )
        END
    """))

# ── Auto-create StandingOrderFulfillments table ──────────────────────────────
# One row per confirmed delivery of a recurring standing order.
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='StandingOrderFulfillments')
        BEGIN
            CREATE TABLE StandingOrderFulfillments (
                FulfillmentID      INT IDENTITY(1,1) PRIMARY KEY,
                StandingOrderID    INT NOT NULL,
                DeliveredAt        DATETIME NOT NULL DEFAULT GETDATE(),
                DeliveredQuantity  FLOAT NULL,  -- may differ from the standing order's usual quantity
                RecordedByPeopleID INT NULL,
                Notes              NVARCHAR(1000) NULL
            )
        END
    """))

# ── Auto-create CronJobRuns table ────────────────────────────────────────────
# One row per scheduled-job invocation (digest runner, etc.) — feeds the admin tracking page.
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='CronJobRuns')
        BEGIN
            CREATE TABLE CronJobRuns (
                CronRunID         INT IDENTITY(1,1) PRIMARY KEY,
                JobName           VARCHAR(100) NOT NULL,
                StartedAt         DATETIME NOT NULL DEFAULT GETDATE(),
                CompletedAt       DATETIME NULL,
                Status            VARCHAR(20) NOT NULL DEFAULT 'running',  -- running | success | partial | error
                ItemsProcessed    INT NOT NULL DEFAULT 0,
                ItemsSucceeded    INT NOT NULL DEFAULT 0,
                ItemsFailed       INT NOT NULL DEFAULT 0,
                Notes             NVARCHAR(MAX) NULL
            )
        END
    """))

# ── Auto-create MarketplacePriceTiers table ──────────────────────────────────
with engine.begin() as _conn:
    _conn.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='MarketplacePriceTiers')
        CREATE TABLE MarketplacePriceTiers (
            TierID      INT IDENTITY(1,1) PRIMARY KEY,
            ListingType NVARCHAR(20) NOT NULL,
            SourceID    INT NOT NULL,
            MinQty      DECIMAL(10,2) NOT NULL,
            TierPrice   DECIMAL(10,2) NOT NULL,
            CreatedAt   DATETIME NOT NULL DEFAULT GETDATE()
        )
    """))

# ── Add partial-fulfillment columns to MarketplaceOrderItems (idempotent) ────
with engine.begin() as _conn:
    for _col, _ddl in [
        ('PartialFulfillmentOk', 'BIT NOT NULL DEFAULT 0'),
        ('FulfilledQuantity',    'DECIMAL(10,2) NULL'),
    ]:
        _conn.execute(text(f"""
            IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE Name='{_col}' AND Object_ID=Object_ID('MarketplaceOrderItems'))
                ALTER TABLE MarketplaceOrderItems ADD {_col} {_ddl}
        """))

# ── Add restaurant-profile columns to Business table (nullable, idempotent) ──
with engine.begin() as _conn:
    for col, ddl in [
        ('Cuisine',          "NVARCHAR(200) NULL"),
        ('HeadChef',         "NVARCHAR(200) NULL"),
        ('SeatingCapacity',  "INT NULL"),
        ('RestaurantHours',  "NVARCHAR(500) NULL"),
        ('YearOpened',       "INT NULL"),
        ('SourcingPhilosophy', "NVARCHAR(MAX) NULL"),
    ]:
        _conn.execute(text(f"""
            IF NOT EXISTS (
                SELECT 1 FROM sys.columns
                WHERE Name = '{col}' AND Object_ID = Object_ID('Business')
            )
            BEGIN
                ALTER TABLE Business ADD {col} {ddl}
            END
        """))


# ─────────────────────────────────────────────
# PYDANTIC MODELS
# ─────────────────────────────────────────────

class CartItem(BaseModel):
    ListingID: str
    Quantity:  float

class PlaceOrderRequest(BaseModel):
    BuyerPeopleID:          int
    BuyerBusinessID:        Optional[int]  = None
    DeliveryMethod:         str            = "pickup"
    DeliveryAddress:        Optional[str]  = None
    DeliveryNotes:          Optional[str]  = None
    RequestedDeliveryDate:  Optional[date] = None
    PartialFulfillmentOk:   bool           = False
    items:                  List[CartItem]

class SellerActionRequest(BaseModel):
    SellerStatus:          str
    RejectionReason:       Optional[str]  = None
    EstimatedDeliveryDate: Optional[date] = None
    FulfilledQuantity:     Optional[float] = None

class ShipItemRequest(BaseModel):
    TrackingNumber: Optional[str] = None
    EstimatedDeliveryDate: Optional[date] = None


# ─────────────────────────────────────────────
# CATALOG  (public — no auth required)
# Unions across Produce, MeatInventory, ProcessedFood
# ListingID format: P{id}, M{id}, F{id} encoded as
# product_type + source_id for round-tripping
# ─────────────────────────────────────────────

@marketplace_router.get("/catalog")
def get_catalog(
    background_tasks:        BackgroundTasks,
    product_type:            Optional[str]   = Query(None),
    organic:                 Optional[bool]  = Query(None),
    search:                  Optional[str]   = Query(None),
    sort:                    str             = Query("newest"),
    available_within_days:   Optional[int]   = Query(None),
    min_quantity:            Optional[float] = Query(None),
    state:                   Optional[str]   = Query(None),
    lang:                    str             = Query("en"),
    db:                      Session         = Depends(get_db),
):
    """
    Browse all active listings from Produce, MeatInventory, and ProcessedFood tables.
    Returns a unified list with a synthetic ListingID: type prefix + source row ID.
    """
    results = []

    search_val = f"%{search.strip()}%" if search and search.strip() else None
    state_val  = state.strip().upper() if state and state.strip() else None

    # ── PRODUCE ──────────────────────────────────────────────────────────────
    if product_type in (None, "all", "produce"):
        where = ["p.ShowProduce = 1", "p.Quantity > 0"]
        params = {}

        if organic:
            where.append("p.IsOrganic = 1")
        if search_val:
            where.append("(i.IngredientName LIKE :search OR p.Notes LIKE :search)")
            params["search"] = search_val
        where.append("(p.ExpirationDate IS NULL OR p.ExpirationDate >= CAST(GETDATE() AS DATE))")
        if available_within_days is not None:
            where.append("(p.AvailableDate IS NULL OR p.AvailableDate <= DATEADD(DAY, :avail_days, GETDATE()))")
            params["avail_days"] = available_within_days
        if min_quantity is not None:
            where.append("p.Quantity >= :min_quantity")
            params["min_quantity"] = min_quantity
        if state_val:
            where.append("a.AddressState = :state_val")
            params["state_val"] = state_val

        rows = db.execute(text(f"""
            SELECT
                p.ProduceID         AS SourceID,
                'produce'           AS ProductType,
                p.BusinessID,
                p.IngredientID,
                i.IngredientName    AS Title,
                p.Notes             AS Description,
                NULL                AS CategoryName,
                p.RetailPrice       AS UnitPrice,
                p.WholesalePrice,
                'unit'              AS UnitLabel,
                p.Quantity          AS QuantityAvailable,
                p.IsOrganic,
                p.IsLocal,
                p.AvailableDate,
                p.ExpirationDate,
                i.IngredientImage   AS ImageURL,
                b.BusinessName      AS SellerName,
                b.PickupAvailable,
                b.ShippingAvailable,
                b.DeliveryRadius,
                a.AddressCity       AS SellerCity,
                a.AddressState      AS SellerState
            FROM Produce p
            JOIN Ingredients i ON p.IngredientID = i.IngredientID
            JOIN Business b ON p.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE {" AND ".join(where)}
        """), params).fetchall()

        for r in rows:
            m = dict(r._mapping)
            m["ListingID"]   = f"P{m['SourceID']}"
            m["UnitPrice"]   = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
            m["WholesalePrice"] = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
            m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
            m["IsOrganic"]   = bool(m["IsOrganic"])
            m["IsLocal"]     = bool(m["IsLocal"])
            m["IsFeatured"]  = False
            results.append(m)

    # ── MEAT ─────────────────────────────────────────────────────────────────
    if product_type in (None, "all", "meat"):
        where = ["m.ShowMeat = 1", "m.Quantity > 0"]
        params = {}

        if search_val:
            where.append("(i.IngredientName LIKE :search OR ic.IngredientCut LIKE :search)")
            params["search"] = search_val
        # Meat has no IsOrganic — skip that filter
        # Meat has no ExpirationDate — skip that filter
        if available_within_days is not None:
            where.append("(m.AvailableDate IS NULL OR m.AvailableDate <= DATEADD(DAY, :avail_days, GETDATE()))")
            params["avail_days"] = available_within_days
        if min_quantity is not None:
            where.append("m.Quantity >= :min_quantity")
            params["min_quantity"] = min_quantity
        if state_val:
            where.append("a.AddressState = :state_val")
            params["state_val"] = state_val

        rows = db.execute(text(f"""
            SELECT
                m.MeatInventoryID   AS SourceID,
                'meat'              AS ProductType,
                m.BusinessID,
                m.IngredientID,
                i.IngredientName + ' - ' + ISNULL(ic.IngredientCut, '') AS Title,
                NULL                AS Description,
                ic.IngredientCut    AS CategoryName,
                m.RetailPrice       AS UnitPrice,
                m.WholesalePrice,
                m.WeightUnit        AS UnitLabel,
                m.Quantity          AS QuantityAvailable,
                0                   AS IsOrganic,
                1                   AS IsLocal,
                m.AvailableDate,
                NULL                AS ExpirationDate,
                i.IngredientImage   AS ImageURL,
                b.BusinessName      AS SellerName,
                b.PickupAvailable,
                b.ShippingAvailable,
                b.DeliveryRadius,
                a.AddressCity       AS SellerCity,
                a.AddressState      AS SellerState
            FROM MeatInventory m
            JOIN Ingredients i ON m.IngredientID = i.IngredientID
            LEFT JOIN Cut ic ON m.IngredientCutID = ic.IngredientCutID
            JOIN Business b ON m.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE {" AND ".join(where)}
        """), params).fetchall()

        for r in rows:
            m = dict(r._mapping)
            m["ListingID"]   = f"M{m['SourceID']}"
            m["UnitPrice"]   = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
            m["WholesalePrice"] = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
            m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
            m["IsOrganic"]   = False
            m["IsLocal"]     = True
            m["IsFeatured"]  = False
            results.append(m)

    # ── PROCESSED FOOD ────────────────────────────────────────────────────────
    if product_type in (None, "all", "processed_food"):
        where = ["f.ShowProcessedFood = 1", "f.Quantity > 0"]
        params = {}

        if search_val:
            where.append("(f.Name LIKE :search OR f.Description LIKE :search)")
            params["search"] = search_val
        # ProcessedFood has no IsOrganic or ExpirationDate
        if available_within_days is not None:
            where.append("(f.AvailableDate IS NULL OR f.AvailableDate <= DATEADD(DAY, :avail_days, GETDATE()))")
            params["avail_days"] = available_within_days
        if min_quantity is not None:
            where.append("f.Quantity >= :min_quantity")
            params["min_quantity"] = min_quantity
        if state_val:
            where.append("a.AddressState = :state_val")
            params["state_val"] = state_val

        rows = db.execute(text(f"""
            SELECT
                f.ProcessedFoodID   AS SourceID,
                'processed_food'    AS ProductType,
                f.BusinessID,
                NULL                AS IngredientID,
                f.Name              AS Title,
                f.Description,
                NULL                AS CategoryName,
                f.RetailPrice       AS UnitPrice,
                f.WholesalePrice,
                'each'              AS UnitLabel,
                f.Quantity          AS QuantityAvailable,
                0                   AS IsOrganic,
                1                   AS IsLocal,
                f.AvailableDate,
                NULL                AS ExpirationDate,
                f.ImageURL,
                b.BusinessName      AS SellerName,
                b.PickupAvailable,
                b.ShippingAvailable,
                b.DeliveryRadius,
                a.AddressCity       AS SellerCity,
                a.AddressState      AS SellerState
            FROM ProcessedFood f
            JOIN Business b ON f.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE {" AND ".join(where)}
        """), params).fetchall()

        for r in rows:
            m = dict(r._mapping)
            m["ListingID"]   = f"F{m['SourceID']}"
            m["UnitPrice"]   = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
            m["WholesalePrice"] = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
            m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
            m["IsOrganic"]   = False
            m["IsLocal"]     = True
            m["IsFeatured"]  = False
            results.append(m)

    # ── PRODUCTS (physical goods — SFProducts) ───────────────────────────────
    if product_type in (None, "all", "product"):
        where_p = [
            "pr.Publishproduct = 1",
            "pr.ProdForSale = 1",
            "pr.ProdQuantityAvailable > 0",
        ]
        params_p = {}
        if search_val:
            where_p.append("(pr.prodName LIKE :search OR pr.prodShortDescription LIKE :search OR sc.CatName LIKE :search)")
            params_p["search"] = search_val
        # SFProducts has no AvailableDate column — NULL treated as "always available", so no avail filter
        if min_quantity is not None:
            where_p.append("pr.ProdQuantityAvailable >= :min_quantity")
            params_p["min_quantity"] = min_quantity
        if state_val:
            where_p.append("a.AddressState = :state_val")
            params_p["state_val"] = state_val
        rows = db.execute(text(f"""
            SELECT pr.ProdID AS SourceID, 'product' AS ProductType, pr.BusinessID,
                   NULL AS IngredientID, pr.prodName AS Title,
                   pr.prodShortDescription AS Description,
                   sc.CatName AS CategoryName,
                   pr.prodPrice AS UnitPrice, pr.SalePrice AS WholesalePrice,
                   'each' AS UnitLabel,
                   CAST(pr.ProdQuantityAvailable AS DECIMAL(10,2)) AS QuantityAvailable,
                   0 AS IsOrganic, 1 AS IsLocal, NULL AS AvailableDate, NULL AS ExpirationDate,
                   COALESCE(pp.ProductImage1, pr.prodImageSmallPath) AS ImageURL,
                   b.BusinessName AS SellerName,
                   b.PickupAvailable,
                   b.ShippingAvailable,
                   b.DeliveryRadius,
                   a.AddressCity AS SellerCity, a.AddressState AS SellerState,
                   pr.prodSaleIsActive AS IsFeatured
            FROM SFProducts pr
            JOIN Business b ON pr.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            LEFT JOIN productsphotos pp ON pp.ID = pr.ProdID
            LEFT JOIN sfcategories sc ON sc.CatID = pr.prodCategoryId
            WHERE {" AND ".join(where_p)}
        """), params_p).fetchall()
        for r in rows:
            m = dict(r._mapping)
            m["ListingID"]         = f"G{m['SourceID']}"
            m["UnitPrice"]         = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
            m["WholesalePrice"]    = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
            m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
            m["IsOrganic"]         = False
            m["IsLocal"]           = True
            m["IsFeatured"]        = bool(m["IsFeatured"])
            results.append(m)

    # ── SERVICES (cart-eligible only — Price2 decimal set, BusinessID present,
    #    not contact-for-price) ────────────────────────────────────────────────
    if product_type in (None, "all", "service"):
        where_s = [
            "s.ServiceAvailable = 1",
            "s.BusinessID IS NOT NULL",
            "s.Price2 IS NOT NULL",
            "s.Price2 > 0",
            "(s.ServiceContactForPrice IS NULL OR s.ServiceContactForPrice = 0)",
        ]
        params_s = {}
        if search_val:
            where_s.append("(s.ServiceTitle LIKE :search OR s.ServicesDescription LIKE :search)")
            params_s["search"] = search_val
        if state_val:
            where_s.append("a.AddressState = :state_val")
            params_s["state_val"] = state_val
        rows = db.execute(text(f"""
            SELECT s.ServicesID AS SourceID, 'service' AS ProductType, s.BusinessID,
                   NULL AS IngredientID, s.ServiceTitle AS Title,
                   s.ServicesDescription AS Description,
                   NULL AS CategoryName,
                   s.Price2 AS UnitPrice, NULL AS WholesalePrice,
                   'booking' AS UnitLabel,
                   CAST(9999 AS DECIMAL(10,2)) AS QuantityAvailable,
                   0 AS IsOrganic, 1 AS IsLocal, s.ServiceStartDate AS AvailableDate,
                   s.ServiceEndDate AS ExpirationDate,
                   s.Photo1 AS ImageURL,
                   b.BusinessName AS SellerName,
                   b.PickupAvailable,
                   b.ShippingAvailable,
                   b.DeliveryRadius,
                   a.AddressCity AS SellerCity, a.AddressState AS SellerState
            FROM Services s
            JOIN Business b ON s.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE {" AND ".join(where_s)}
        """), params_s).fetchall()
        for r in rows:
            m = dict(r._mapping)
            m["ListingID"]         = f"S{m['SourceID']}"
            m["UnitPrice"]         = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
            m["WholesalePrice"]    = None
            m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 9999.0
            m["IsOrganic"]         = False
            m["IsLocal"]           = True
            m["IsFeatured"]        = False
            results.append(m)

    # ── Sort combined results ─────────────────────────────────────────────────
    if sort == "price_asc":
        results.sort(key=lambda x: x["UnitPrice"])
    elif sort == "price_desc":
        results.sort(key=lambda x: x["UnitPrice"], reverse=True)
    elif sort == "name_asc":
        results.sort(key=lambda x: (x["Title"] or "").lower())
    else:  # newest — sort by SourceID desc as proxy
        results.sort(key=lambda x: x["SourceID"], reverse=True)

    # ── Fire background image generation for any missing images ──────────────
    items_needing_images = [r for r in results if not r.get("ImageURL") and r.get("IngredientID")]
    if items_needing_images:
        from database import get_db as get_db_factory
        background_tasks.add_task(ensure_images_for_catalog, items_needing_images, get_db_factory)

    # ── Attach volume price tiers to each listing ─────────────────────────────
    if results:
        _type_to_ids: dict = {}
        for _item in results:
            _type_to_ids.setdefault(_item["ProductType"], []).append(_item["SourceID"])
        _tier_index: dict = {}
        for _pt, _ids in _type_to_ids.items():
            _safe_ids = ",".join(str(int(i)) for i in _ids)
            _tier_rows = db.execute(text(f"""
                SELECT SourceID, MinQty, TierPrice FROM MarketplacePriceTiers
                WHERE ListingType = :lt AND SourceID IN ({_safe_ids}) ORDER BY MinQty ASC
            """), {"lt": _pt}).fetchall()
            for _t in _tier_rows:
                _key = f"{_pt}:{_t.SourceID}"
                _tier_index.setdefault(_key, []).append({"MinQty": float(_t.MinQty), "TierPrice": float(_t.TierPrice)})
        for _item in results:
            _item["PriceTiers"] = _tier_index.get(f"{_item['ProductType']}:{_item['SourceID']}", [])

    return translate_list(results, ["Description"], lang, db)


@marketplace_router.get("/catalog/{listing_id}")
def get_listing(listing_id: str, lang: str = "en", db: Session = Depends(get_db)):
    """
    Single listing detail. listing_id format: P{id} | M{id} | F{id}
    """
    prefix = listing_id[0].upper()
    try:
        source_id = int(listing_id[1:])
    except ValueError:
        raise HTTPException(400, "Invalid listing ID format")

    if prefix == "P":
        row = db.execute(text("""
            SELECT
                p.ProduceID AS SourceID, 'produce' AS ProductType, p.BusinessID,
                i.IngredientName AS Title, p.Notes AS Description,
                p.RetailPrice AS UnitPrice, p.WholesalePrice,
                'unit' AS UnitLabel, p.Quantity AS QuantityAvailable,
                p.IsOrganic, p.IsLocal, p.AvailableDate, p.ExpirationDate,
                i.IngredientImage AS ImageURL,
                b.BusinessName AS SellerName,
                a.AddressCity AS SellerCity, a.AddressState AS SellerState
            FROM Produce p
            JOIN Ingredients i ON p.IngredientID = i.IngredientID
            JOIN Business b ON p.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE p.ProduceID = :sid AND p.ShowProduce = 1
        """), {"sid": source_id}).fetchone()

    elif prefix == "M":
        row = db.execute(text("""
            SELECT
                m.MeatInventoryID AS SourceID, 'meat' AS ProductType, m.BusinessID,
                i.IngredientName + ' - ' + ISNULL(ic.IngredientCut, '') AS Title,
                NULL AS Description,
                m.RetailPrice AS UnitPrice, m.WholesalePrice,
                m.WeightUnit AS UnitLabel, m.Quantity AS QuantityAvailable,
                0 AS IsOrganic, 1 AS IsLocal, m.AvailableDate, NULL AS ExpirationDate,
                i.IngredientImage AS ImageURL,
                b.BusinessName AS SellerName,
                a.AddressCity AS SellerCity, a.AddressState AS SellerState
            FROM MeatInventory m
            JOIN Ingredients i ON m.IngredientID = i.IngredientID
            LEFT JOIN Cut ic ON m.IngredientCutID = ic.IngredientCutID
            JOIN Business b ON m.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE m.MeatInventoryID = :sid AND m.ShowMeat = 1
        """), {"sid": source_id}).fetchone()

    elif prefix == "F":
        row = db.execute(text("""
            SELECT
                f.ProcessedFoodID AS SourceID, 'processed_food' AS ProductType, f.BusinessID,
                f.Name AS Title, f.Description,
                f.RetailPrice AS UnitPrice, f.WholesalePrice,
                'each' AS UnitLabel, f.Quantity AS QuantityAvailable,
                0 AS IsOrganic, 1 AS IsLocal, f.AvailableDate, NULL AS ExpirationDate,
                f.ImageURL,
                b.BusinessName AS SellerName,
                a.AddressCity AS SellerCity, a.AddressState AS SellerState
            FROM ProcessedFood f
            JOIN Business b ON f.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE f.ProcessedFoodID = :sid AND f.ShowProcessedFood = 1
        """), {"sid": source_id}).fetchone()

    elif prefix == "G":
        row = db.execute(text("""
            SELECT pr.ProdID AS SourceID, 'product' AS ProductType, pr.BusinessID,
                   pr.prodName AS Title, pr.prodDescription AS Description,
                   pr.prodPrice AS UnitPrice, pr.SalePrice AS WholesalePrice,
                   'each' AS UnitLabel,
                   CAST(pr.ProdQuantityAvailable AS DECIMAL(10,2)) AS QuantityAvailable,
                   0 AS IsOrganic, 1 AS IsLocal, NULL AS AvailableDate, NULL AS ExpirationDate,
                   COALESCE(pp.ProductImage1, pr.prodImageSmallPath) AS ImageURL,
                   b.BusinessName AS SellerName,
                   a.AddressCity AS SellerCity, a.AddressState AS SellerState
            FROM SFProducts pr
            JOIN Business b ON pr.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            LEFT JOIN productsphotos pp ON pp.ID = pr.ProdID
            WHERE pr.ProdID = :sid AND pr.Publishproduct = 1
        """), {"sid": source_id}).fetchone()

    elif prefix == "S":
        row = db.execute(text("""
            SELECT s.ServicesID AS SourceID, 'service' AS ProductType, s.BusinessID,
                   s.ServiceTitle AS Title, s.ServicesDescription AS Description,
                   s.Price2 AS UnitPrice, NULL AS WholesalePrice,
                   'booking' AS UnitLabel,
                   CAST(9999 AS DECIMAL(10,2)) AS QuantityAvailable,
                   0 AS IsOrganic, 1 AS IsLocal, s.ServiceStartDate AS AvailableDate,
                   s.ServiceEndDate AS ExpirationDate,
                   s.Photo1 AS ImageURL,
                   b.BusinessName AS SellerName,
                   a.AddressCity AS SellerCity, a.AddressState AS SellerState
            FROM Services s
            JOIN Business b ON s.BusinessID = b.BusinessID
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            WHERE s.ServicesID = :sid AND s.ServiceAvailable = 1
        """), {"sid": source_id}).fetchone()
    else:
        raise HTTPException(400, "Invalid listing type prefix")

    if not row:
        raise HTTPException(404, "Listing not found")

    listing = dict(row._mapping)
    listing["ListingID"]   = listing_id
    listing["UnitPrice"]   = float(listing["UnitPrice"]) if listing["UnitPrice"] else 0.0
    listing["WholesalePrice"] = float(listing["WholesalePrice"]) if listing["WholesalePrice"] else None
    listing["QuantityAvailable"] = float(listing["QuantityAvailable"]) if listing["QuantityAvailable"] else 0.0
    listing["IsOrganic"]   = bool(listing.get("IsOrganic", False))
    listing["IsLocal"]     = bool(listing.get("IsLocal", True))
    listing["IsFeatured"]  = False
    listing["reviews"]     = []
    listing["relatedListings"] = []

    return translate_fields(listing, ["Description"], lang, db)


# ─────────────────────────────────────────────
# ORDERS  (buyer)
# ─────────────────────────────────────────────

@marketplace_router.post("/orders")
def place_order(req: PlaceOrderRequest, db: Session = Depends(get_db)):
    if not req.items:
        raise HTTPException(400, "No items in order")

    order_items = []
    subtotal = 0.0

    for item in req.items:
        # Parse synthetic ListingID
        prefix = str(item.ListingID)[0].upper() if isinstance(item.ListingID, str) else None
        source_id = int(str(item.ListingID)[1:]) if prefix else item.ListingID

        if prefix == "P":
            listing = db.execute(text("""
                SELECT p.ProduceID AS ListingID, p.BusinessID, i.IngredientName AS Title,
                       'produce' AS ProductType, p.RetailPrice AS UnitPrice,
                       p.Quantity AS QuantityAvailable,
                       b.BusinessName AS SellerName, pe.PeopleEmail AS SellerEmail
                FROM Produce p
                JOIN Ingredients i ON p.IngredientID = i.IngredientID
                JOIN Business b ON p.BusinessID = b.BusinessID
                JOIN People pe ON b.Contact1PeopleID = pe.PeopleID
                WHERE p.ProduceID = :sid AND p.ShowProduce = 1
            """), {"sid": source_id}).fetchone()
        elif prefix == "M":
            listing = db.execute(text("""
                SELECT m.MeatInventoryID AS ListingID, m.BusinessID,
                       i.IngredientName + ' - ' + ISNULL(ic.IngredientCut,'') AS Title,
                       'meat' AS ProductType, m.RetailPrice AS UnitPrice,
                       m.Quantity AS QuantityAvailable,
                       b.BusinessName AS SellerName, pe.PeopleEmail AS SellerEmail
                FROM MeatInventory m
                JOIN Ingredients i ON m.IngredientID = i.IngredientID
                LEFT JOIN Cut ic ON m.IngredientCutID = ic.IngredientCutID
                JOIN Business b ON m.BusinessID = b.BusinessID
                JOIN People pe ON b.Contact1PeopleID = pe.PeopleID
                WHERE m.MeatInventoryID = :sid AND m.ShowMeat = 1
            """), {"sid": source_id}).fetchone()
        elif prefix == "F":
            listing = db.execute(text("""
                SELECT f.ProcessedFoodID AS ListingID, f.BusinessID, f.Name AS Title,
                       'processed_food' AS ProductType, f.RetailPrice AS UnitPrice,
                       f.Quantity AS QuantityAvailable,
                       b.BusinessName AS SellerName, pe.PeopleEmail AS SellerEmail
                FROM ProcessedFood f
                JOIN Business b ON f.BusinessID = b.BusinessID
                JOIN People pe ON b.Contact1PeopleID = pe.PeopleID
                WHERE f.ProcessedFoodID = :sid AND f.ShowProcessedFood = 1
            """), {"sid": source_id}).fetchone()
        elif prefix == "G":
            listing = db.execute(text("""
                SELECT pr.ProdID AS ListingID, pr.BusinessID, pr.prodName AS Title,
                       'product' AS ProductType, pr.prodPrice AS UnitPrice,
                       CAST(pr.ProdQuantityAvailable AS DECIMAL(10,2)) AS QuantityAvailable,
                       b.BusinessName AS SellerName, pe.PeopleEmail AS SellerEmail
                FROM SFProducts pr
                JOIN Business b ON pr.BusinessID = b.BusinessID
                JOIN People pe ON b.Contact1PeopleID = pe.PeopleID
                WHERE pr.ProdID = :sid AND pr.Publishproduct = 1 AND pr.ProdForSale = 1
            """), {"sid": source_id}).fetchone()
        elif prefix == "S":
            listing = db.execute(text("""
                SELECT s.ServicesID AS ListingID, s.BusinessID, s.ServiceTitle AS Title,
                       'service' AS ProductType, s.Price2 AS UnitPrice,
                       CAST(9999 AS DECIMAL(10,2)) AS QuantityAvailable,
                       b.BusinessName AS SellerName, pe.PeopleEmail AS SellerEmail
                FROM Services s
                JOIN Business b ON s.BusinessID = b.BusinessID
                JOIN People pe ON b.Contact1PeopleID = pe.PeopleID
                WHERE s.ServicesID = :sid
                  AND s.ServiceAvailable = 1
                  AND s.Price2 IS NOT NULL AND s.Price2 > 0
                  AND (s.ServiceContactForPrice IS NULL OR s.ServiceContactForPrice = 0)
            """), {"sid": source_id}).fetchone()
        else:
            raise HTTPException(400, f"Invalid listing ID: {item.ListingID}")

        if not listing:
            raise HTTPException(404, f"Listing {item.ListingID} not found or inactive")

        l = dict(listing._mapping)
        qty = float(item.Quantity)

        # Services are bookings — no inventory check. Others support partial fulfillment.
        avail = float(l["QuantityAvailable"]) if l["QuantityAvailable"] is not None else 0.0
        if prefix != "S" and qty > avail:
            if req.PartialFulfillmentOk and avail > 0:
                qty = avail
            else:
                raise HTTPException(400, f"Only {l['QuantityAvailable']} available for '{l['Title']}'")

        unit_price = float(l["UnitPrice"])
        # Apply volume tier pricing if configured for this listing
        _tier_type = {"P": "produce", "M": "meat", "F": "processed_food", "G": "product", "S": "service"}.get(prefix)
        if _tier_type:
            _tiers = db.execute(text("""
                SELECT MinQty, TierPrice FROM MarketplacePriceTiers
                WHERE ListingType = :lt AND SourceID = :sid ORDER BY MinQty DESC
            """), {"lt": _tier_type, "sid": source_id}).fetchall()
            for _t in _tiers:
                if qty >= float(_t.MinQty):
                    unit_price = float(_t.TierPrice)
                    break
        line_total   = round(unit_price * qty, 2)
        platform_cut = round(line_total * 0.025, 2)
        seller_payout = round(line_total - platform_cut, 2)
        subtotal += line_total

        order_items.append({
            "listing":        l,
            "source_id":      source_id,
            "prefix":         prefix,
            "quantity":       qty,
            "unit_price":     unit_price,
            "line_total":     line_total,
            "seller_payout":  seller_payout,
            "partial_ok":     req.PartialFulfillmentOk,
        })

    platform_fee = round(subtotal * 0.025, 2)
    total_amount = round(subtotal + platform_fee, 2)

    buyer = db.execute(text("""
        SELECT PeopleFirstName + ' ' + PeopleLastName AS FullName, PeopleEmail
        FROM People WHERE PeopleID = :pid
    """), {"pid": req.BuyerPeopleID}).fetchone()
    if not buyer:
        raise HTTPException(404, "Buyer not found")

    import random, string
    order_number = "OFN-" + "".join(random.choices(string.digits, k=8))

    db.execute(text("""
        INSERT INTO MarketplaceOrders (
            OrderNumber, BuyerPeopleID, BuyerBusinessID,
            BuyerName, BuyerEmail,
            DeliveryMethod, DeliveryAddress, DeliveryNotes,
            RequestedDeliveryDate,
            Subtotal, PlatformFee, TaxAmount, DeliveryFee, TotalAmount,
            PaymentStatus, OrderStatus, CreatedAt, UpdatedAt
        ) VALUES (
            :order_number, :buyer_pid, :buyer_bid,
            :buyer_name, :buyer_email,
            :delivery_method, :delivery_address, :delivery_notes,
            :requested_date,
            :subtotal, :platform_fee, 0, 0, :total_amount,
            'pending', 'pending', GETDATE(), GETDATE()
        )
    """), {
        "order_number":     order_number,
        "buyer_pid":        req.BuyerPeopleID,
        "buyer_bid":        req.BuyerBusinessID,
        "buyer_name":       buyer[0],
        "buyer_email":      buyer[1],
        "delivery_method":  req.DeliveryMethod,
        "delivery_address": req.DeliveryAddress,
        "delivery_notes":   req.DeliveryNotes,
        "requested_date":   req.RequestedDeliveryDate,
        "subtotal":         subtotal,
        "platform_fee":     platform_fee,
        "total_amount":     total_amount,
    })

    order_id = db.execute(text("SELECT SCOPE_IDENTITY()")).scalar()

    for oi in order_items:
        l = oi["listing"]
        db.execute(text("""
            INSERT INTO MarketplaceOrderItems (
                OrderID, ListingID, SellerBusinessID,
                ProductTitle, ProductType, SellerName,
                Quantity, UnitPrice, LineTotal, SellerPayout, PlatformFee,
                SellerStatus, PartialFulfillmentOk, CreatedAt, UpdatedAt
            ) VALUES (
                :order_id, :listing_id, :seller_bid,
                :title, :product_type, :seller_name,
                :quantity, :unit_price, :line_total, :seller_payout, :platform_fee,
                'pending', :partial_ok, GETDATE(), GETDATE()
            )
        """), {
            "order_id":     order_id,
            "listing_id":   f"{oi['prefix']}{oi['source_id']}",
            "seller_bid":   l["BusinessID"],
            "title":        l["Title"],
            "product_type": l["ProductType"],
            "seller_name":  l["SellerName"],
            "quantity":     oi["quantity"],
            "unit_price":   oi["unit_price"],
            "line_total":   oi["line_total"],
            "seller_payout": oi["seller_payout"],
            "platform_fee": round(oi["line_total"] * 0.025, 2),
            "partial_ok":   1 if oi["partial_ok"] else 0,
        })

        # Decrement inventory in source table
        if oi["prefix"] == "P":
            db.execute(text("UPDATE Produce SET Quantity = Quantity - :qty WHERE ProduceID = :sid"),
                       {"qty": oi["quantity"], "sid": oi["source_id"]})
        elif oi["prefix"] == "M":
            db.execute(text("UPDATE MeatInventory SET Quantity = Quantity - :qty WHERE MeatInventoryID = :sid"),
                       {"qty": oi["quantity"], "sid": oi["source_id"]})
        elif oi["prefix"] == "F":
            db.execute(text("UPDATE ProcessedFood SET Quantity = Quantity - :qty WHERE ProcessedFoodID = :sid"),
                       {"qty": oi["quantity"], "sid": oi["source_id"]})
        elif oi["prefix"] == "G":
            db.execute(text("UPDATE SFProducts SET ProdQuantityAvailable = ProdQuantityAvailable - :qty WHERE ProdID = :sid"),
                       {"qty": oi["quantity"], "sid": oi["source_id"]})

    db.commit()

    try:
        from marketplace_emails import send_order_placed_buyer, send_order_placed_seller
        send_order_placed_buyer(order_id, db)
    except Exception as e:
        print(f"[marketplace] Email send failed: {e}")

    thank_you_codes = []
    try:
        from routers.event_promo_codes import issue_marketplace_thank_you_codes
        thank_you_codes = issue_marketplace_thank_you_codes(db, order_id)
        if thank_you_codes:
            try:
                from event_emails import send_marketplace_thank_you_promo
                send_marketplace_thank_you_promo(buyer[1], buyer[0], thank_you_codes)
            except Exception as e:
                print(f"[marketplace] Thank-you promo email failed: {e}")
    except Exception as e:
        print(f"[marketplace] Thank-you promo code issuance failed: {e}")

    return {
        "OrderID":     order_id,
        "OrderNumber": order_number,
        "TotalAmount": total_amount,
        "ThankYouCodes": thank_you_codes,
        "message":     "Order placed successfully",
    }


@marketplace_router.get("/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.execute(text("SELECT * FROM MarketplaceOrders WHERE OrderID = :oid"), {"oid": order_id}).fetchone()
    if not order:
        raise HTTPException(404, "Order not found")
    result = dict(order._mapping)
    for field in ["Subtotal", "PlatformFee", "TaxAmount", "DeliveryFee", "TotalAmount"]:
        if result.get(field) is not None:
            result[field] = float(result[field])

    items = db.execute(text("""
        SELECT oi.*, b.BusinessName
        FROM MarketplaceOrderItems oi
        LEFT JOIN Business b ON oi.SellerBusinessID = b.BusinessID
        WHERE oi.OrderID = :oid ORDER BY oi.OrderItemID
    """), {"oid": order_id}).fetchall()

    result["items"] = []
    for i in items:
        row = dict(i._mapping)
        for f in ["UnitPrice", "LineTotal", "SellerPayout", "PlatformFee"]:
            if row.get(f) is not None:
                row[f] = float(row[f])

        # Fetch image from source table
        listing_id = row.get("ListingID", "")
        image_url = None
        try:
            prefix = str(listing_id)[0].upper()
            source_id = int(str(listing_id)[1:])
            if prefix == "P":
                img = db.execute(text("""
                    SELECT i.IngredientImage FROM Produce p
                    JOIN Ingredients i ON p.IngredientID = i.IngredientID
                    WHERE p.ProduceID = :sid
                """), {"sid": source_id}).fetchone()
                image_url = img[0] if img else None
            elif prefix == "M":
                img = db.execute(text("""
                    SELECT i.IngredientImage FROM MeatInventory m
                    JOIN Ingredients i ON m.IngredientID = i.IngredientID
                    WHERE m.MeatInventoryID = :sid
                """), {"sid": source_id}).fetchone()
                image_url = img[0] if img else None
            elif prefix == "F":
                img = db.execute(text("""
                    SELECT ImageURL FROM ProcessedFood WHERE ProcessedFoodID = :sid
                """), {"sid": source_id}).fetchone()
                image_url = img[0] if img else None
            elif prefix == "G":
                img = db.execute(text("""
                    SELECT COALESCE(pp.ProductImage1, pr.prodImageSmallPath) AS ImageURL
                    FROM SFProducts pr LEFT JOIN productsphotos pp ON pp.ID = pr.ProdID
                    WHERE pr.ProdID = :sid
                """), {"sid": source_id}).fetchone()
                image_url = img[0] if img else None
            elif prefix == "S":
                img = db.execute(text(
                    "SELECT Photo1 FROM Services WHERE ServicesID = :sid"
                ), {"sid": source_id}).fetchone()
                image_url = img[0] if img else None
        except Exception:
            image_url = None

        row["ImageURL"] = image_url
        result["items"].append(row)

    result["history"] = []
    return result


@marketplace_router.get("/orders")
def list_orders(buyer_people_id: int, db: Session = Depends(get_db)):
    orders = db.execute(text("""
        SELECT o.OrderID, o.OrderNumber, o.OrderStatus, o.PaymentStatus,
               o.TotalAmount, o.CreatedAt, o.DeliveryMethod,
               COUNT(oi.OrderItemID) AS ItemCount
        FROM MarketplaceOrders o
        LEFT JOIN MarketplaceOrderItems oi ON o.OrderID = oi.OrderID
        WHERE o.BuyerPeopleID = :pid
        GROUP BY o.OrderID, o.OrderNumber, o.OrderStatus, o.PaymentStatus,
                 o.TotalAmount, o.CreatedAt, o.DeliveryMethod
        ORDER BY o.CreatedAt DESC
    """), {"pid": buyer_people_id}).fetchall()
    result = []
    for o in orders:
        row = dict(o._mapping)
        row["TotalAmount"] = float(row["TotalAmount"]) if row["TotalAmount"] else 0.0
        result.append(row)
    return result


# ─────────────────────────────────────────────
# SELLER ACTIONS  (frontend-compatible paths)
# ─────────────────────────────────────────────

@marketplace_router.get("/orders/seller/{business_id}")
def get_seller_orders_v2(
    business_id: int,
    status: str = Query(None),
    db: Session = Depends(get_db),
):
    sql = """
        SELECT oi.OrderItemID, oi.ListingID, oi.SellerBusinessID, oi.SellerName,
               oi.ProductTitle, oi.ProductType, oi.Quantity, oi.UnitPrice,
               oi.LineTotal, oi.PlatformFee, oi.SellerPayout, oi.Notes,
               oi.SellerStatus, oi.TrackingNumber, oi.ShippedAt, oi.DeliveredAt,
               oi.EstimatedDeliveryDate, oi.RejectionReason, oi.CreatedAt,
               o.OrderNumber, o.BuyerName, o.BuyerEmail, o.BuyerPhone,
               o.DeliveryMethod, o.DeliveryAddress, o.RequestedDeliveryDate
        FROM MarketplaceOrderItems oi
        JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
        WHERE oi.SellerBusinessID = :bid
    """
    params: dict = {"bid": business_id}
    if status:
        sql += " AND oi.SellerStatus = :status"
        params["status"] = status
    sql += " ORDER BY o.CreatedAt DESC"
    rows = db.execute(text(sql), params).fetchall()
    orders = []
    for r in rows:
        row = dict(r._mapping)
        for f in ["UnitPrice", "LineTotal", "SellerPayout", "PlatformFee"]:
            if row.get(f) is not None:
                row[f] = float(row[f])
        orders.append(row)
    return {"orders": orders}


@marketplace_router.post("/orders/seller/confirm")
def seller_confirm_v2(body: dict, db: Session = Depends(get_db)):
    body = blank_to_none(body)
    oiid = body.get("OrderItemID")
    status = body.get("Status")
    if not oiid or status not in ("confirmed", "rejected"):
        raise HTTPException(400, "OrderItemID and valid Status required")
    db.execute(text("""
        UPDATE MarketplaceOrderItems
        SET SellerStatus = :status, RejectionReason = :reason,
            EstimatedDeliveryDate = :edd, UpdatedAt = GETDATE()
        WHERE OrderItemID = :oiid
    """), {
        "status": status,
        "reason": body.get("RejectionReason"),
        "edd": body.get("EstimatedDeliveryDate"),
        "oiid": oiid,
    })
    db.commit()
    return {"ok": True}


@marketplace_router.post("/orders/seller/ship")
def seller_ship_v2(body: dict, db: Session = Depends(get_db)):
    body = blank_to_none(body)
    oiid = body.get("OrderItemID")
    if not oiid:
        raise HTTPException(400, "OrderItemID required")
    db.execute(text("""
        UPDATE MarketplaceOrderItems
        SET SellerStatus = 'shipped', TrackingNumber = :tracking,
            ShippedAt = GETDATE(), UpdatedAt = GETDATE()
        WHERE OrderItemID = :oiid
    """), {"tracking": body.get("TrackingNumber"), "oiid": oiid})
    db.commit()
    return {"ok": True}


@marketplace_router.get("/seller/analytics")
def seller_analytics(business_id: int, db: Session = Depends(get_db)):
    """Revenue, top buyers, repeat order rate for the seller dashboard."""
    # Overall KPIs
    overall = db.execute(text("""
        SELECT
            ISNULL(SUM(oi.LineTotal), 0)  AS TotalRevenue,
            COUNT(DISTINCT o.OrderID)      AS TotalOrders,
            ISNULL(AVG(oi.LineTotal), 0)   AS AvgOrderValue,
            ISNULL(SUM(CASE WHEN YEAR(o.CreatedAt)=YEAR(GETDATE()) AND MONTH(o.CreatedAt)=MONTH(GETDATE())
                            THEN oi.LineTotal ELSE 0 END), 0) AS RevenueThisMonth
        FROM MarketplaceOrderItems oi
        JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
        WHERE oi.SellerBusinessID = :bid AND oi.SellerStatus NOT IN ('rejected')
    """), {"bid": business_id}).fetchone()

    # Monthly revenue — last 12 full months
    monthly_rows = db.execute(text("""
        SELECT YEAR(o.CreatedAt) AS yr, MONTH(o.CreatedAt) AS mo,
               ISNULL(SUM(oi.LineTotal), 0) AS Revenue,
               COUNT(DISTINCT o.OrderID) AS Orders
        FROM MarketplaceOrderItems oi
        JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
        WHERE oi.SellerBusinessID = :bid
          AND oi.SellerStatus NOT IN ('rejected')
          AND o.CreatedAt >= DATEADD(MONTH, -11, DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1))
        GROUP BY YEAR(o.CreatedAt), MONTH(o.CreatedAt)
        ORDER BY yr, mo
    """), {"bid": business_id}).fetchall()

    # Top 5 buyers by spend
    top_buyers = db.execute(text("""
        SELECT TOP 5
            o.BuyerName, o.BuyerEmail,
            COUNT(DISTINCT o.OrderID) AS OrderCount,
            ISNULL(SUM(oi.LineTotal), 0) AS TotalSpend
        FROM MarketplaceOrderItems oi
        JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
        WHERE oi.SellerBusinessID = :bid AND oi.SellerStatus NOT IN ('rejected')
        GROUP BY o.BuyerName, o.BuyerEmail
        ORDER BY TotalSpend DESC
    """), {"bid": business_id}).fetchall()

    # Repeat order rate
    repeat = db.execute(text("""
        SELECT
            COUNT(*) AS TotalBuyers,
            SUM(CASE WHEN order_count > 1 THEN 1 ELSE 0 END) AS RepeatBuyers
        FROM (
            SELECT o.BuyerPeopleID, COUNT(DISTINCT o.OrderID) AS order_count
            FROM MarketplaceOrderItems oi
            JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
            WHERE oi.SellerBusinessID = :bid AND oi.SellerStatus NOT IN ('rejected')
            GROUP BY o.BuyerPeopleID
        ) bc
    """), {"bid": business_id}).fetchone()

    total_buyers  = repeat.TotalBuyers  if repeat else 0
    repeat_buyers = repeat.RepeatBuyers if repeat else 0
    repeat_rate   = round((repeat_buyers / total_buyers * 100), 1) if total_buyers else 0.0

    return {
        "overall": {
            "TotalRevenue":    float(overall.TotalRevenue),
            "TotalOrders":     overall.TotalOrders,
            "AvgOrderValue":   float(overall.AvgOrderValue),
            "RevenueThisMonth": float(overall.RevenueThisMonth),
        },
        "monthly": [
            {"yr": r.yr, "mo": r.mo, "Revenue": float(r.Revenue), "Orders": r.Orders}
            for r in monthly_rows
        ],
        "topBuyers": [
            {"name": r.BuyerName, "email": r.BuyerEmail, "orders": r.OrderCount, "spend": float(r.TotalSpend)}
            for r in top_buyers
        ],
        "repeatRate": repeat_rate,
        "totalBuyers": total_buyers,
    }


@marketplace_router.get("/seller/orders")
def get_seller_orders(business_id: int, db: Session = Depends(get_db)):
    items = db.execute(text("""
        SELECT oi.*, o.OrderNumber, o.BuyerName, o.BuyerEmail,
               o.DeliveryMethod, o.RequestedDeliveryDate, o.CreatedAt AS OrderDate
        FROM MarketplaceOrderItems oi
        JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
        WHERE oi.SellerBusinessID = :bid
        ORDER BY o.CreatedAt DESC
    """), {"bid": business_id}).fetchall()
    result = []
    for i in items:
        row = dict(i._mapping)
        for f in ["UnitPrice", "LineTotal", "SellerPayout"]:
            if row.get(f) is not None:
                row[f] = float(row[f])
        result.append(row)
    return result


@marketplace_router.post("/seller/orders/{order_item_id}/action")
def seller_item_action(order_item_id: int, req: SellerActionRequest, db: Session = Depends(get_db)):
    item = db.execute(text("""
        SELECT oi.*, o.OrderID FROM MarketplaceOrderItems oi
        JOIN MarketplaceOrders o ON oi.OrderID = o.OrderID
        WHERE oi.OrderItemID = :oiid
    """), {"oiid": order_item_id}).fetchone()
    if not item:
        raise HTTPException(404, "Order item not found")
    if item.SellerStatus not in ("pending",):
        raise HTTPException(400, f"Item is already '{item.SellerStatus}'")
    if req.SellerStatus not in ("confirmed", "rejected"):
        raise HTTPException(400, "Status must be 'confirmed' or 'rejected'")

    # Partial fulfillment: seller specifies a smaller fulfilled qty than ordered
    if req.SellerStatus == "confirmed" and req.FulfilledQuantity is not None:
        fq = float(req.FulfilledQuantity)
        orig_qty = float(item.Quantity)
        if 0 < fq < orig_qty:
            unit_price = float(item.UnitPrice)
            new_line = round(fq * unit_price, 2)
            db.execute(text("""
                UPDATE MarketplaceOrderItems
                SET Quantity = :qty, FulfilledQuantity = :fq,
                    LineTotal = :lt, SellerPayout = :sp, PlatformFee = :pf,
                    SellerStatus = 'confirmed', EstimatedDeliveryDate = :edd, UpdatedAt = GETDATE()
                WHERE OrderItemID = :oiid
            """), {
                "qty": fq, "fq": fq, "lt": new_line,
                "sp": round(new_line * 0.975, 2), "pf": round(new_line * 0.025, 2),
                "edd": req.EstimatedDeliveryDate, "oiid": order_item_id,
            })
            # Restore unfulfilled remainder to source inventory
            remainder = orig_qty - fq
            _lid = item.ListingID
            _pfx = str(_lid)[0].upper()
            _sid = int(str(_lid)[1:])
            if _pfx == "P":
                db.execute(text("UPDATE Produce SET Quantity = Quantity + :qty WHERE ProduceID = :sid"), {"qty": remainder, "sid": _sid})
            elif _pfx == "M":
                db.execute(text("UPDATE MeatInventory SET Quantity = Quantity + :qty WHERE MeatInventoryID = :sid"), {"qty": remainder, "sid": _sid})
            elif _pfx == "F":
                db.execute(text("UPDATE ProcessedFood SET Quantity = Quantity + :qty WHERE ProcessedFoodID = :sid"), {"qty": remainder, "sid": _sid})
            elif _pfx == "G":
                db.execute(text("UPDATE SFProducts SET ProdQuantityAvailable = ProdQuantityAvailable + :qty WHERE ProdID = :sid"), {"qty": remainder, "sid": _sid})
            db.commit()
            return {"message": "Item partially fulfilled", "FulfilledQuantity": fq, "OrderID": item.OrderID}

    db.execute(text("""
        UPDATE MarketplaceOrderItems
        SET SellerStatus = :status, RejectionReason = :reason,
            EstimatedDeliveryDate = :edd, UpdatedAt = GETDATE()
        WHERE OrderItemID = :oiid
    """), {"status": req.SellerStatus, "reason": req.RejectionReason,
           "edd": req.EstimatedDeliveryDate, "oiid": order_item_id})

    # If rejected, restore inventory in source table
    if req.SellerStatus == "rejected":
        listing_id = item.ListingID
        prefix = str(listing_id)[0].upper()
        source_id = int(str(listing_id)[1:])
        if prefix == "P":
            db.execute(text("UPDATE Produce SET Quantity = Quantity + :qty WHERE ProduceID = :sid"),
                       {"qty": item.Quantity, "sid": source_id})
        elif prefix == "M":
            db.execute(text("UPDATE MeatInventory SET Quantity = Quantity + :qty WHERE MeatInventoryID = :sid"),
                       {"qty": item.Quantity, "sid": source_id})
        elif prefix == "F":
            db.execute(text("UPDATE ProcessedFood SET Quantity = Quantity + :qty WHERE ProcessedFoodID = :sid"),
                       {"qty": item.Quantity, "sid": source_id})
        elif prefix == "G":
            db.execute(text("UPDATE SFProducts SET ProdQuantityAvailable = ProdQuantityAvailable + :qty WHERE ProdID = :sid"),
                       {"qty": item.Quantity, "sid": source_id})

    db.commit()
    return {"message": f"Item {req.SellerStatus}", "OrderID": item.OrderID}


@marketplace_router.post("/seller/orders/{order_item_id}/ship")
def ship_item(order_item_id: int, req: ShipItemRequest, db: Session = Depends(get_db)):
    item = db.execute(text("SELECT * FROM MarketplaceOrderItems WHERE OrderItemID = :oiid"),
                      {"oiid": order_item_id}).fetchone()
    if not item:
        raise HTTPException(404, "Order item not found")
    if item.SellerStatus != "confirmed":
        raise HTTPException(400, "Item must be confirmed before shipping")
    db.execute(text("""
        UPDATE MarketplaceOrderItems
        SET SellerStatus = 'shipped', TrackingNumber = :tracking,
            EstimatedDeliveryDate = :edd, ShippedAt = GETDATE(), UpdatedAt = GETDATE()
        WHERE OrderItemID = :oiid
    """), {"tracking": req.TrackingNumber, "edd": req.EstimatedDeliveryDate, "oiid": order_item_id})
    db.commit()
    return {"message": "Item marked as shipped"}


# ─────────────────────────────────────────────
# SELLER LISTINGS  (read-only view of their inventory)
# ─────────────────────────────────────────────

@marketplace_router.get("/seller/listings")
def get_seller_listings(business_id: int, db: Session = Depends(get_db)):
    """Returns unified produce + meat + processed food for a seller."""
    results = []

    produce = db.execute(text("""
        SELECT p.ProduceID AS SourceID, 'produce' AS ProductType,
               i.IngredientName AS Title, p.RetailPrice AS UnitPrice,
               p.WholesalePrice, 'unit' AS UnitLabel,
               p.Quantity AS QuantityAvailable,
               p.IsOrganic, p.IsLocal, p.ShowProduce AS IsActive,
               p.AvailableDate, p.ExpirationDate,
               i.IngredientImage AS ImageURL
        FROM Produce p
        JOIN Ingredients i ON p.IngredientID = i.IngredientID
        WHERE p.BusinessID = :bid
        ORDER BY p.ProduceID DESC
    """), {"bid": business_id}).fetchall()

    for r in produce:
        m = dict(r._mapping)
        m["ListingID"]  = f"P{m['SourceID']}"
        m["UnitPrice"]  = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
        m["WholesalePrice"] = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
        m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
        m["IsOrganic"]  = bool(m["IsOrganic"])
        m["IsLocal"]    = bool(m["IsLocal"])
        m["IsActive"]   = bool(m["IsActive"])
        m["IsFeatured"] = False
        results.append(m)

    meat = db.execute(text("""
        SELECT m.MeatInventoryID AS SourceID, 'meat' AS ProductType,
               i.IngredientName + ' - ' + ISNULL(ic.IngredientCut,'') AS Title,
               m.RetailPrice AS UnitPrice, m.WholesalePrice,
               m.WeightUnit AS UnitLabel, m.Quantity AS QuantityAvailable,
               0 AS IsOrganic, 1 AS IsLocal, m.ShowMeat AS IsActive,
               m.AvailableDate, NULL AS ExpirationDate,
               i.IngredientImage AS ImageURL
        FROM MeatInventory m
        JOIN Ingredients i ON m.IngredientID = i.IngredientID
        LEFT JOIN Cut ic ON m.IngredientCutID = ic.IngredientCutID
        WHERE m.BusinessID = :bid
        ORDER BY m.MeatInventoryID DESC
    """), {"bid": business_id}).fetchall()

    for r in meat:
        m = dict(r._mapping)
        m["ListingID"]  = f"M{m['SourceID']}"
        m["UnitPrice"]  = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
        m["WholesalePrice"] = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
        m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
        m["IsOrganic"]  = False
        m["IsLocal"]    = True
        m["IsActive"]   = bool(m["IsActive"])
        m["IsFeatured"] = False
        results.append(m)

    food = db.execute(text("""
        SELECT f.ProcessedFoodID AS SourceID, 'processed_food' AS ProductType,
               f.Name AS Title, f.RetailPrice AS UnitPrice,
               f.WholesalePrice, 'each' AS UnitLabel,
               f.Quantity AS QuantityAvailable,
               0 AS IsOrganic, 1 AS IsLocal, f.ShowProcessedFood AS IsActive,
               f.AvailableDate, NULL AS ExpirationDate,
               f.ImageURL AS ImageURL
        FROM ProcessedFood f
        WHERE f.BusinessID = :bid
        ORDER BY f.ProcessedFoodID DESC
    """), {"bid": business_id}).fetchall()

    for r in food:
        m = dict(r._mapping)
        m["ListingID"]  = f"F{m['SourceID']}"
        m["UnitPrice"]  = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
        m["WholesalePrice"] = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
        m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
        m["IsOrganic"]  = False
        m["IsLocal"]    = True
        m["IsActive"]   = bool(m["IsActive"])
        m["IsFeatured"] = False
        results.append(m)

    products = db.execute(text("""
        SELECT pr.ProductID AS SourceID, 'product' AS ProductType,
               pr.Title, pr.UnitPrice, pr.WholesalePrice, pr.UnitLabel,
               pr.QuantityAvailable, pr.IsOrganic, pr.IsActive,
               pr.CategoryName, NULL AS ExpirationDate, NULL AS AvailableDate,
               pr.ImageURL
        FROM MarketplaceProducts pr
        WHERE pr.BusinessID = :bid
        ORDER BY pr.ProductID DESC
    """), {"bid": business_id}).fetchall()
    for r in products:
        m = dict(r._mapping)
        m["ListingID"]         = f"G{m['SourceID']}"
        m["UnitPrice"]         = float(m["UnitPrice"]) if m["UnitPrice"] else 0.0
        m["WholesalePrice"]    = float(m["WholesalePrice"]) if m["WholesalePrice"] else None
        m["QuantityAvailable"] = float(m["QuantityAvailable"]) if m["QuantityAvailable"] else 0.0
        m["IsOrganic"]         = bool(m["IsOrganic"])
        m["IsLocal"]           = True
        m["IsActive"]          = bool(m["IsActive"])
        m["IsFeatured"]        = False
        results.append(m)

    return results


# ─────────────────────────────────────────────
# PRICE TIERS  (B2B volume discounts per listing)
# ─────────────────────────────────────────────

_TIER_PREFIX_MAP = {"P": "produce", "M": "meat", "F": "processed_food", "G": "product", "S": "service"}


def _parse_listing_id(listing_id: str):
    if not listing_id or len(listing_id) < 2:
        raise HTTPException(400, "Invalid listing ID")
    prefix = listing_id[0].upper()
    listing_type = _TIER_PREFIX_MAP.get(prefix)
    if not listing_type:
        raise HTTPException(400, f"Unknown listing type prefix: {prefix}")
    try:
        source_id = int(listing_id[1:])
    except ValueError:
        raise HTTPException(400, "Invalid listing ID format")
    return listing_type, source_id


@marketplace_router.get("/tiers/{listing_id}")
def get_price_tiers(listing_id: str, db: Session = Depends(get_db)):
    """Return volume price tiers for a listing (public)."""
    listing_type, source_id = _parse_listing_id(listing_id)
    rows = db.execute(text("""
        SELECT TierID, MinQty, TierPrice FROM MarketplacePriceTiers
        WHERE ListingType = :lt AND SourceID = :sid ORDER BY MinQty ASC
    """), {"lt": listing_type, "sid": source_id}).fetchall()
    return [{"TierID": r.TierID, "MinQty": float(r.MinQty), "TierPrice": float(r.TierPrice)} for r in rows]


@marketplace_router.put("/tiers/{listing_id}")
def set_price_tiers(listing_id: str, tiers: list, db: Session = Depends(get_db)):
    """Replace all volume price tiers for a listing (seller-facing)."""
    listing_type, source_id = _parse_listing_id(listing_id)
    db.execute(text("DELETE FROM MarketplacePriceTiers WHERE ListingType = :lt AND SourceID = :sid"),
               {"lt": listing_type, "sid": source_id})
    for tier in tiers:
        min_qty = tier.get("MinQty") if isinstance(tier, dict) else getattr(tier, "MinQty", None)
        tier_price = tier.get("TierPrice") if isinstance(tier, dict) else getattr(tier, "TierPrice", None)
        if min_qty is None or tier_price is None:
            continue
        db.execute(text("""
            INSERT INTO MarketplacePriceTiers (ListingType, SourceID, MinQty, TierPrice)
            VALUES (:lt, :sid, :min_qty, :tier_price)
        """), {"lt": listing_type, "sid": source_id, "min_qty": float(min_qty), "tier_price": float(tier_price)})
    db.commit()
    return {"ok": True, "count": len(tiers)}


# ─────────────────────────────────────────────
# CHECKOUT  (server-side cart sync flow)
# ─────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    BuyerPeopleID:          int
    BuyerBusinessID:        Optional[int]  = None
    DeliveryMethod:         str            = "pickup"
    DeliveryAddress:        Optional[str]  = None
    DeliveryNotes:          Optional[str]  = None
    RequestedDeliveryDate:  Optional[date] = None
    PartialFulfillmentOk:   bool           = False


@marketplace_router.post("/checkout")
def checkout(req: CheckoutRequest, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT ci.ListingID, ci.Quantity
        FROM CartItems ci
        WHERE ci.BuyerPeopleID = :pid
    """), {"pid": req.BuyerPeopleID}).fetchall()

    if not rows:
        raise HTTPException(400, "Cart is empty")

    items = [CartItem(ListingID=r[0], Quantity=float(r[1])) for r in rows]
    order_req = PlaceOrderRequest(
        BuyerPeopleID=req.BuyerPeopleID,
        BuyerBusinessID=req.BuyerBusinessID,
        DeliveryMethod=req.DeliveryMethod,
        DeliveryAddress=req.DeliveryAddress,
        DeliveryNotes=req.DeliveryNotes,
        RequestedDeliveryDate=req.RequestedDeliveryDate,
        PartialFulfillmentOk=req.PartialFulfillmentOk,
        items=items,
    )
    result = place_order(order_req, db)
    db.execute(text("DELETE FROM CartItems WHERE BuyerPeopleID = :pid"), {"pid": req.BuyerPeopleID})
    db.commit()
    return result


class CartItemAdd(BaseModel):
    BuyerPeopleID:   int
    BuyerBusinessID: Optional[int] = None
    ListingID:       str
    Quantity:        float
    Notes:           Optional[str] = None


@marketplace_router.post("/cart")
def add_to_cart(data: CartItemAdd, db: Session = Depends(get_db)):
    # Look up price and seller from the source table
    prefix = str(data.ListingID)[0].upper()
    source_id = int(str(data.ListingID)[1:])

    if prefix == "P":
        row = db.execute(text("""
            SELECT p.BusinessID AS SellerBusinessID, p.RetailPrice AS UnitPrice
            FROM Produce p WHERE p.ProduceID = :sid AND p.ShowProduce = 1
        """), {"sid": source_id}).fetchone()
    elif prefix == "M":
        row = db.execute(text("""
            SELECT m.BusinessID AS SellerBusinessID, m.RetailPrice AS UnitPrice
            FROM MeatInventory m WHERE m.MeatInventoryID = :sid AND m.ShowMeat = 1
        """), {"sid": source_id}).fetchone()
    elif prefix == "F":
        row = db.execute(text("""
            SELECT f.BusinessID AS SellerBusinessID, f.RetailPrice AS UnitPrice
            FROM ProcessedFood f WHERE f.ProcessedFoodID = :sid AND f.ShowProcessedFood = 1
        """), {"sid": source_id}).fetchone()
    elif prefix == "G":
        row = db.execute(text("""
            SELECT pr.BusinessID AS SellerBusinessID, pr.prodPrice AS UnitPrice
            FROM SFProducts pr WHERE pr.ProdID = :sid AND pr.Publishproduct = 1
        """), {"sid": source_id}).fetchone()
    elif prefix == "S":
        row = db.execute(text("""
            SELECT s.BusinessID AS SellerBusinessID, s.Price2 AS UnitPrice
            FROM Services s
            WHERE s.ServicesID = :sid
              AND s.ServiceAvailable = 1
              AND s.BusinessID IS NOT NULL
              AND s.Price2 IS NOT NULL AND s.Price2 > 0
              AND (s.ServiceContactForPrice IS NULL OR s.ServiceContactForPrice = 0)
        """), {"sid": source_id}).fetchone()
    else:
        raise HTTPException(400, f"Invalid listing ID: {data.ListingID}")

    if not row:
        raise HTTPException(404, "Listing not found or inactive")

    seller_bid = row[0]
    unit_price = float(row[1]) if row[1] else 0.0

    existing = db.execute(text(
        "SELECT CartItemID FROM CartItems WHERE BuyerPeopleID = :pid AND ListingID = :lid"
    ), {"pid": data.BuyerPeopleID, "lid": data.ListingID}).fetchone()

    if existing:
        db.execute(text(
            "UPDATE CartItems SET Quantity = :qty, UpdatedAt = GETDATE() WHERE CartItemID = :cid"
        ), {"qty": data.Quantity, "cid": existing[0]})
    else:
        db.execute(text("""
            INSERT INTO CartItems (BuyerPeopleID, BuyerBusinessID, ListingID, SellerBusinessID, Quantity, UnitPrice, Notes, AddedAt, UpdatedAt)
            VALUES (:pid, :bid, :lid, :sbid, :qty, :price, :notes, GETDATE(), GETDATE())
        """), {
            "pid":   data.BuyerPeopleID,
            "bid":   data.BuyerBusinessID,
            "lid":   data.ListingID,
            "sbid":  seller_bid,
            "qty":   data.Quantity,
            "price": unit_price,
            "notes": data.Notes,
        })
    db.commit()
    return {"message": "Added to cart"}


def _resolve_cart_listing_meta(db: Session, listing_id: str):
    """Look up Title / ProductType / UnitLabel / ImageURL / QuantityAvailable
    for a single ListingID by reading the right source table for the prefix."""
    try:
        prefix = str(listing_id)[0].upper()
        source_id = int(str(listing_id)[1:])
    except (ValueError, IndexError):
        return None

    if prefix == "P":
        sql = """
            SELECT i.IngredientName AS Title, 'produce' AS ProductType, 'unit' AS UnitLabel,
                   i.IngredientImage AS ImageURL, p.Quantity AS QuantityAvailable
            FROM Produce p JOIN Ingredients i ON p.IngredientID = i.IngredientID
            WHERE p.ProduceID = :sid
        """
    elif prefix == "M":
        sql = """
            SELECT i.IngredientName + ' - ' + ISNULL(ic.IngredientCut,'') AS Title,
                   'meat' AS ProductType, m.WeightUnit AS UnitLabel,
                   i.IngredientImage AS ImageURL, m.Quantity AS QuantityAvailable
            FROM MeatInventory m JOIN Ingredients i ON m.IngredientID = i.IngredientID
            LEFT JOIN Cut ic ON m.IngredientCutID = ic.IngredientCutID
            WHERE m.MeatInventoryID = :sid
        """
    elif prefix == "F":
        sql = """
            SELECT f.Name AS Title, 'processed_food' AS ProductType, 'each' AS UnitLabel,
                   f.ImageURL, f.Quantity AS QuantityAvailable
            FROM ProcessedFood f WHERE f.ProcessedFoodID = :sid
        """
    elif prefix == "G":
        sql = """
            SELECT pr.prodName AS Title, 'product' AS ProductType, 'each' AS UnitLabel,
                   COALESCE(pp.ProductImage1, pr.prodImageSmallPath) AS ImageURL,
                   CAST(pr.ProdQuantityAvailable AS DECIMAL(10,2)) AS QuantityAvailable
            FROM SFProducts pr LEFT JOIN productsphotos pp ON pp.ID = pr.ProdID
            WHERE pr.ProdID = :sid
        """
    elif prefix == "S":
        sql = """
            SELECT s.ServiceTitle AS Title, 'service' AS ProductType, 'booking' AS UnitLabel,
                   s.Photo1 AS ImageURL, CAST(9999 AS DECIMAL(10,2)) AS QuantityAvailable
            FROM Services s WHERE s.ServicesID = :sid
        """
    else:
        return None

    row = db.execute(text(sql), {"sid": source_id}).mappings().fetchone()
    return dict(row) if row else None


@marketplace_router.get("/cart/{people_id}")
def get_cart(people_id: int, db: Session = Depends(get_db)):
    """Buyer's cart, grouped by seller. Resolves Title + image per item by
    reading the source table for the listing's prefix."""
    rows = db.execute(text("""
        SELECT ci.CartItemID, ci.ListingID, ci.Quantity, ci.UnitPrice, ci.Notes,
               ci.SellerBusinessID, b.BusinessName AS SellerName
        FROM CartItems ci
        LEFT JOIN Business b ON ci.SellerBusinessID = b.BusinessID
        WHERE ci.BuyerPeopleID = :pid
        ORDER BY b.BusinessName, ci.AddedAt
    """), {"pid": people_id}).mappings().fetchall()

    sellers = {}
    item_count = 0
    subtotal = 0.0
    for r in rows:
        item = dict(r)
        item["Quantity"]  = float(item["Quantity"] or 0)
        item["UnitPrice"] = float(item["UnitPrice"] or 0)
        meta = _resolve_cart_listing_meta(db, item["ListingID"]) or {}
        item["Title"]            = meta.get("Title") or item["ListingID"]
        item["ProductType"]      = meta.get("ProductType")
        item["UnitLabel"]        = meta.get("UnitLabel") or "each"
        item["ImageURL"]         = meta.get("ImageURL")
        item["QuantityAvailable"] = float(meta["QuantityAvailable"]) if meta.get("QuantityAvailable") is not None else None
        item["lineTotal"]        = round(item["Quantity"] * item["UnitPrice"], 2)

        sid = item["SellerBusinessID"]
        if sid not in sellers:
            sellers[sid] = {
                "SellerBusinessID": sid,
                "SellerName": item["SellerName"] or "Seller",
                "items": [],
                "subtotal": 0.0,
            }
        sellers[sid]["items"].append(item)
        sellers[sid]["subtotal"] += item["lineTotal"]
        item_count += 1
        subtotal += item["lineTotal"]

    for s in sellers.values():
        s["subtotal"] = round(s["subtotal"], 2)

    fee = round(subtotal * 0.025, 2)
    return {
        "sellers": list(sellers.values()),
        "itemCount": item_count,
        "subtotal": round(subtotal, 2),
        "platformFee": fee,
        "total": round(subtotal + fee, 2),
    }


class CartItemUpdate(BaseModel):
    Quantity: float
    Notes: Optional[str] = None


@marketplace_router.put("/cart/{cart_item_id}")
def update_cart_item(cart_item_id: int, data: CartItemUpdate, db: Session = Depends(get_db)):
    if data.Quantity <= 0:
        db.execute(text("DELETE FROM CartItems WHERE CartItemID = :cid"), {"cid": cart_item_id})
    else:
        db.execute(
            text("UPDATE CartItems SET Quantity = :qty, Notes = :notes, UpdatedAt = GETDATE() WHERE CartItemID = :cid"),
            {"qty": data.Quantity, "notes": data.Notes, "cid": cart_item_id},
        )
    db.commit()
    return {"message": "Cart updated"}


@marketplace_router.delete("/cart/clear/{people_id}")
def clear_cart(people_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM CartItems WHERE BuyerPeopleID = :pid"), {"pid": people_id})
    db.commit()
    return {"message": "Cart cleared"}


@marketplace_router.delete("/cart/{cart_item_id}")
def remove_cart_item(cart_item_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM CartItems WHERE CartItemID = :cid"), {"cid": cart_item_id})
    db.commit()
    return {"message": "Removed from cart"}


# ─────────────────────────────────────────────
# REVIEWS
# ─────────────────────────────────────────────

class ReviewRequest(BaseModel):
    ListingID:        str
    ReviewerPeopleID: int
    OrderID:          int
    Rating:           int
    ReviewText:       Optional[str] = None


# ─────────────────────────────────────────────
# PRODUCTS  (physical goods — seller CRUD)
# ─────────────────────────────────────────────

class ProductCreate(BaseModel):
    BusinessID:        int
    Title:             str
    Description:       Optional[str]  = None
    CategoryName:      Optional[str]  = None
    UnitPrice:         float
    WholesalePrice:    Optional[float] = None
    UnitLabel:         str            = 'each'
    QuantityAvailable: float          = 0
    MinOrderQuantity:  float          = 1
    ImageURL:          Optional[str]  = None
    Tags:              Optional[str]  = None
    IsOrganic:         bool           = False
    IsFeatured:        bool           = False
    Weight:            Optional[float] = None
    WeightUnit:        Optional[str]  = None
    Color:             Optional[str]  = None
    Size:              Optional[str]  = None
    Material:          Optional[str]  = None
    SKU:               Optional[str]  = None
    DeliveryOptions:   str            = 'pickup'


def _ser_product(r):
    m = dict(r._mapping)
    for f in ["UnitPrice", "WholesalePrice", "QuantityAvailable", "MinOrderQuantity", "Weight"]:
        if m.get(f) is not None:
            m[f] = float(m[f])
    m["IsActive"]   = bool(m.get("IsActive", 1))
    m["IsOrganic"]  = bool(m.get("IsOrganic", 0))
    m["IsFeatured"] = bool(m.get("IsFeatured", 0))
    m["ListingID"]  = f"G{m['ProductID']}"
    return m


@marketplace_router.get("/products")
def list_products(
    business_id: Optional[int]  = Query(None),
    search:      Optional[str]  = Query(None),
    category:    Optional[str]  = Query(None),
    sort:        str            = Query("newest"),
    db:          Session        = Depends(get_db),
):
    where = ["pr.IsActive = 1"]
    params = {}
    if business_id:
        where.append("pr.BusinessID = :bid")
        params["bid"] = business_id
    if search and search.strip():
        where.append("(pr.Title LIKE :search OR pr.Description LIKE :search OR pr.CategoryName LIKE :search)")
        params["search"] = f"%{search.strip()}%"
    if category and category != "all":
        where.append("pr.CategoryName = :cat")
        params["cat"] = category
    order = {"price_asc": "pr.UnitPrice ASC", "price_desc": "pr.UnitPrice DESC",
             "name_asc": "pr.Title ASC"}.get(sort, "pr.ProductID DESC")
    rows = db.execute(text(f"""
        SELECT pr.*, b.BusinessName AS SellerName, a.AddressCity AS SellerCity, a.AddressState AS SellerState
        FROM MarketplaceProducts pr
        JOIN Business b ON pr.BusinessID = b.BusinessID
        LEFT JOIN Address a ON b.AddressID = a.AddressID
        WHERE {" AND ".join(where)}
        ORDER BY pr.IsFeatured DESC, {order}
    """), params).fetchall()
    return [_ser_product(r) for r in rows]


@marketplace_router.get("/products/categories")
def list_product_categories(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT DISTINCT CategoryName FROM MarketplaceProducts
        WHERE IsActive = 1 AND CategoryName IS NOT NULL AND CategoryName != ''
        ORDER BY CategoryName
    """)).fetchall()
    return [r[0] for r in rows]


@marketplace_router.get("/products/seller")
def seller_products(business_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT pr.*, b.BusinessName AS SellerName, NULL AS SellerCity, NULL AS SellerState
        FROM MarketplaceProducts pr
        JOIN Business b ON pr.BusinessID = b.BusinessID
        WHERE pr.BusinessID = :bid
        ORDER BY pr.ProductID DESC
    """), {"bid": business_id}).fetchall()
    return [_ser_product(r) for r in rows]


@marketplace_router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT pr.*, b.BusinessName AS SellerName, a.AddressCity AS SellerCity, a.AddressState AS SellerState
        FROM MarketplaceProducts pr
        JOIN Business b ON pr.BusinessID = b.BusinessID
        LEFT JOIN Address a ON b.AddressID = a.AddressID
        WHERE pr.ProductID = :pid
    """), {"pid": product_id}).fetchone()
    if not row:
        raise HTTPException(404, "Product not found")
    return _ser_product(row)


@marketplace_router.post("/products")
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO MarketplaceProducts
            (BusinessID, Title, Description, CategoryName, UnitPrice, WholesalePrice,
             UnitLabel, QuantityAvailable, MinOrderQuantity, ImageURL, Tags,
             IsOrganic, IsFeatured, Weight, WeightUnit, Color, Size, Material, SKU, DeliveryOptions,
             IsActive, CreatedAt, UpdatedAt)
        VALUES
            (:bid, :title, :desc, :cat, :price, :wprice,
             :unit, :qty, :minqty, :img, :tags,
             :organic, :featured, :weight, :wunit, :color, :size, :material, :sku, :delivery,
             1, GETDATE(), GETDATE())
    """), {
        "bid": data.BusinessID, "title": data.Title, "desc": data.Description,
        "cat": data.CategoryName, "price": data.UnitPrice, "wprice": data.WholesalePrice,
        "unit": data.UnitLabel, "qty": data.QuantityAvailable, "minqty": data.MinOrderQuantity,
        "img": data.ImageURL, "tags": data.Tags,
        "organic": int(data.IsOrganic), "featured": int(data.IsFeatured),
        "weight": data.Weight, "wunit": data.WeightUnit, "color": data.Color,
        "size": data.Size, "material": data.Material, "sku": data.SKU,
        "delivery": data.DeliveryOptions,
    })
    product_id = db.execute(text("SELECT SCOPE_IDENTITY()")).scalar()
    db.commit()
    return get_product(int(product_id), db)


@marketplace_router.put("/products/{product_id}")
def update_product(product_id: int, data: dict, db: Session = Depends(get_db)):
    data = blank_to_none(data)
    allowed = {"Title", "Description", "CategoryName", "UnitPrice", "WholesalePrice",
               "UnitLabel", "QuantityAvailable", "MinOrderQuantity", "ImageURL", "Tags",
               "IsOrganic", "IsFeatured", "IsActive", "Weight", "WeightUnit",
               "Color", "Size", "Material", "SKU", "DeliveryOptions"}
    sets = [f"{k} = :{k}" for k in data if k in allowed]
    if not sets:
        raise HTTPException(400, "No valid fields to update")
    sets.append("UpdatedAt = GETDATE()")
    db.execute(text(f"UPDATE MarketplaceProducts SET {', '.join(sets)} WHERE ProductID = :pid"),
               {**{k: v for k, v in data.items() if k in allowed}, "pid": product_id})
    db.commit()
    return get_product(product_id, db)


@marketplace_router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM MarketplaceProducts WHERE ProductID = :pid"), {"pid": product_id})
    db.commit()
    return {"message": "Product deleted"}


@marketplace_router.post("/reviews")
def submit_review(req: ReviewRequest, db: Session = Depends(get_db)):
    if not 1 <= req.Rating <= 5:
        raise HTTPException(400, "Rating must be between 1 and 5")
    db.execute(text("""
        INSERT INTO MarketplaceReviews (ListingID, ReviewerPeopleID, OrderID, Rating, ReviewText, CreatedAt)
        VALUES (:lid, :pid, :oid, :rating, :text, GETDATE())
    """), {"lid": req.ListingID, "pid": req.ReviewerPeopleID,
           "oid": req.OrderID, "rating": req.Rating, "text": req.ReviewText})
    db.commit()


# ── Livestock Marketplace endpoints ──────────────────────────────────────────
# These serve LivestockForSale.jsx, LivestockMarketplace.jsx, and RanchList.jsx

import time as _time
_livestock_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes

GCP_ANIMALS = "https://storage.googleapis.com/oatmeal-farm-network-images/Animals"

SLUG_TO_SPECIES_ID = {
    'alpacas': 2, 'bison': 9, 'buffalo': 34, 'camels': 18, 'cattle': 8,
    'chickens': 13, 'crocodiles': 25, 'dogs': 3, 'deer': 21, 'donkeys': 7,
    'ducks': 15, 'emus': 19, 'geese': 22, 'goats': 6, 'guinea-fowl': 26,
    'honey-bees': 23, 'horses': 5, 'llamas': 4, 'musk-ox': 27,
    'ostriches': 28, 'pheasants': 29, 'pigs': 12, 'pigeons': 30,
    'quails': 31, 'rabbits': 11, 'sheep': 10, 'snails': 33,
    'turkeys': 14, 'yaks': 17,
}

SLUG_TO_SINGULAR = {
    'alpacas': 'Alpaca', 'bison': 'Bison', 'buffalo': 'Buffalo',
    'camels': 'Camel', 'cattle': 'Cattle', 'chickens': 'Chicken',
    'crocodiles': 'Crocodile', 'deer': 'Deer', 'dogs': 'Working Dog',
    'donkeys': 'Donkey', 'ducks': 'Duck', 'emus': 'Emu', 'geese': 'Goose',
    'goats': 'Goat', 'guinea-fowl': 'Guinea Fowl', 'honey-bees': 'Honey Bee',
    'horses': 'Horse', 'llamas': 'Llama', 'musk-ox': 'Musk Ox',
    'ostriches': 'Ostrich', 'pheasants': 'Pheasant', 'pigs': 'Pig',
    'pigeons': 'Pigeon', 'quails': 'Quail', 'rabbits': 'Rabbit',
    'sheep': 'Sheep', 'snails': 'Snail', 'turkeys': 'Turkey', 'yaks': 'Yak',
}


def _unescape(s) -> str:
    """Replace SQL-escaped double single-quotes ('' ) with a real apostrophe."""
    if not s:
        return s
    return str(s).replace("''", "'")


_GCS_ANIMALS = "https://storage.googleapis.com/oatmeal-farm-network-images/Animals"
# Any value already pointing at Google Cloud Storage is passed through as-is by
# _photo_url(). (_GCS_PREFIX was referenced but never defined, which made
# /api/marketplace/animal/{id} raise NameError -> 500 -> the public website
# livestock detail view showed "Could not load animal details".)
_GCS_PREFIX = "https://storage.googleapis.com/"
_OLD_DOMAINS  = [
    'oatmealfarmnetwork.com', 'livestockofamerica.com',
    'livestockoftheworld.com', 'alpacainfinity.com',
    'globallivestocksolutions.com',
]

import re as _re

def _fix_animal_url(url: str | None) -> str | None:
    """Normalise any photo URL to a GCS URL, mirroring livestock.py _fix_image_url."""
    if not url:
        return None
    url = url.strip()
    if len(url) < 4:
        return None
    if url.lower().startswith('http'):
        url = _re.sub(r'^http:', 'https:', url, flags=_re.IGNORECASE)
        for domain in _OLD_DOMAINS:
            if domain in url.lower():
                filename = url.split('/')[-1].strip()
                if filename and len(filename) > 3:
                    return f"{_GCS_ANIMALS}/{filename}"
        return url  # already a full URL (including valid GCS URLs)
    if url.lower().startswith('/uploads/'):
        filename = url[9:].strip()
        if filename and len(filename) > 3:
            return f"{_GCS_ANIMALS}/{filename}"
        return None
    if '/' not in url and len(url) > 4:
        return f"{_GCS_ANIMALS}/{url}"
    filename = url.split('/')[-1].strip()
    if filename and len(filename) > 3:
        return f"{_GCS_ANIMALS}/{filename}"
    return None


def _animal_photo(row) -> str | None:
    """Return the best available photo URL for a listing card, normalised to GCS."""
    for field in ('ListPageImage', 'Photo1', 'Photo2', 'Photo3', 'Photo4',
                  'Photo5', 'Photo6', 'Photo7', 'Photo8'):
        v = getattr(row, field, None)
        url = _fix_animal_url(str(v) if v else None)
        if url:
            return url
    return None


def _animal_dict(row, studs: bool = False) -> dict:
    breeds = [b for b in [
        getattr(row, 'Breed1', None) or '',
        getattr(row, 'Breed2', None) or '',
    ] if b]
    price = None
    try:
        raw = float(row.StudFee if studs else row.Price)
        if raw > 0:
            price = raw
    except Exception:
        pass
    return {
        "animal_id":  row.AnimalID,
        "full_name":  _unescape(getattr(row, 'FullName', '') or ''),
        "photo":      _animal_photo(row),
        "price":      price if not studs else None,
        "stud_fee":   price if studs else None,
        "breeds":     [_unescape(b) for b in breeds],
        "location":   getattr(row, 'AddressState', '') or '',
        "seller":     _unescape(getattr(row, 'BusinessName', '') or ''),
        "species_id": getattr(row, 'SpeciesID', None),
    }


@marketplace_router.get("/homepage-listings")
def livestock_homepage_listings(db: Session = Depends(get_db)):
    """Return up to 24 recent for-sale animals across all species for the homepage."""
    cached = _livestock_cache.get("homepage")
    if cached and _time.time() - cached["ts"] < _CACHE_TTL:
        return cached["data"]
    try:
        rows = db.execute(text("""
            SELECT TOP 60
                a.AnimalID, a.FullName, a.SpeciesID,
                ph.Photo1, ph.Photo2, ph.Photo3, ph.Photo4, ph.Photo5,
                ph.Photo6, ph.Photo7, ph.Photo8, ph.ListPageImage,
                p.Price,
                b1.Breed AS Breed1, b2.Breed AS Breed2,
                biz.BusinessName,
                addr.AddressState
            FROM Animals a
            LEFT JOIN Pricing p     ON p.AnimalID       = a.AnimalID
            LEFT JOIN Photos ph     ON ph.AnimalID      = a.AnimalID
            LEFT JOIN SpeciesBreedLookupTable b1  ON b1.BreedLookupID = a.BreedID
            LEFT JOIN SpeciesBreedLookupTable b2  ON b2.BreedLookupID = a.BreedID2
            LEFT JOIN BusinessAccess ba ON ba.PeopleID  = a.PeopleID AND ba.Active = 1
            LEFT JOIN Business biz  ON biz.BusinessID  = ba.BusinessID
            LEFT JOIN Address addr  ON addr.AddressID   = biz.AddressID
            WHERE a.PublishForSale = 1
            ORDER BY
                CASE WHEN (
                    (ph.ListPageImage IS NOT NULL AND ph.ListPageImage != '' AND ph.ListPageImage != '0')
                    OR (ph.Photo1 IS NOT NULL AND ph.Photo1 != '' AND ph.Photo1 != '0')
                    OR (ph.Photo2 IS NOT NULL AND ph.Photo2 != '' AND ph.Photo2 != '0')
                    OR (ph.Photo3 IS NOT NULL AND ph.Photo3 != '' AND ph.Photo3 != '0')
                    OR (ph.Photo4 IS NOT NULL AND ph.Photo4 != '' AND ph.Photo4 != '0')
                    OR (ph.Photo5 IS NOT NULL AND ph.Photo5 != '' AND ph.Photo5 != '0')
                    OR (ph.Photo6 IS NOT NULL AND ph.Photo6 != '' AND ph.Photo6 != '0')
                    OR (ph.Photo7 IS NOT NULL AND ph.Photo7 != '' AND ph.Photo7 != '0')
                    OR (ph.Photo8 IS NOT NULL AND ph.Photo8 != '' AND ph.Photo8 != '0')
                ) THEN 0 ELSE 1 END ASC,
                a.LastUpdated DESC
        """)).fetchall()
        animals = [_animal_dict(r) for r in rows]

        with_photo = [a for a in animals if a.get("photo")]
        no_photo   = [a for a in animals if not a.get("photo")]

        # Diversity sort: round-robin through species so no species dominates
        # any section of the page.  Within each species the SQL ORDER BY already
        # put the most-recently-updated animal first.
        #
        # Cap each species first so a dominant species (e.g. alpacas) can't
        # flood the tail after smaller species are exhausted.
        # Formula: ceil(30 / n_species), minimum 3 per species.
        from collections import defaultdict
        sp_groups: dict = defaultdict(list)
        for a in with_photo:
            sp_groups[a.get("species_id")].append(a)

        n_species = max(len(sp_groups), 1)
        per_cap   = max(3, -(-30 // n_species))   # ceiling division
        sp_groups = {k: v[:per_cap] for k, v in sp_groups.items()}

        diverse: list = []
        while sp_groups:
            for sp in list(sp_groups.keys()):
                if sp_groups[sp]:
                    diverse.append(sp_groups[sp].pop(0))
            sp_groups = {k: v for k, v in sp_groups.items() if v}

        # Homepage only shows animals with confirmed GCS photos.
        # Return 30 so the frontend can trim to full rows at any breakpoint.
        result = diverse[:30]

        _livestock_cache["homepage"] = {"data": result, "ts": _time.time()}
        return result
    except Exception as e:
        import traceback; traceback.print_exc()
        return []


_TAG_RE = _re.compile(r'<[^>]+>')


def _plain_text(raw, limit: int = 420) -> str:
    """Flatten a stored HTML description into plain card copy."""
    if not raw:
        return ''
    import html as _html
    txt = _re.sub(r'<\s*br\s*/?\s*>', ' ', str(raw), flags=_re.IGNORECASE)
    txt = _re.sub(r'</\s*(p|div|li)\s*>', ' ', txt, flags=_re.IGNORECASE)
    txt = _TAG_RE.sub('', txt)
    txt = _html.unescape(_unescape(txt))
    txt = _re.sub(r'\s+', ' ', txt).strip()
    if limit and len(txt) > limit:
        txt = txt[:limit].rsplit(' ', 1)[0].rstrip(' ,;:.') + '…'
    return txt


def _usable_photo(row) -> str | None:
    """Photo URL for a feature card, or None when it can't render.

    Legacy rows store a bare '0' where a filename should be; _fix_animal_url
    leaves those as a live-looking URL that 404s, which would put a broken
    frame in a hero card. Reject anything whose filename is too short to be
    real rather than trusting the URL shape.
    """
    url = _animal_photo(row)
    if not url:
        return None
    filename = url.rstrip('/').split('/')[-1].split('?')[0]
    return url if len(filename) > 4 and '.' in filename else None


def _featured_dict(row, studs: bool) -> dict:
    """Shape one Animals row for the homepage feature cards."""
    breeds = [_unescape(b) for b in (
        getattr(row, 'Breed1', None) or '',
        getattr(row, 'Breed2', None) or '',
    ) if b]

    def money(value):
        try:
            v = float(value)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    species_id = getattr(row, 'SpeciesID', None)
    # StudDescription is optional on the record; fall back to the general one.
    description = _plain_text(
        getattr(row, 'StudDescription', None) if studs else None
    ) or _plain_text(getattr(row, 'Description', None))

    return {
        "animal_id":    row.AnimalID,
        "full_name":    _unescape(getattr(row, 'FullName', '') or '').strip(),
        "photo":        _usable_photo(row),
        "description":  description,
        "price":        None if studs else money(getattr(row, 'Price', None)),
        "stud_fee":     money(getattr(row, 'StudFee', None)) if studs else None,
        "breeds":       breeds,
        "category":     (getattr(row, 'Category', '') or '').strip(),
        "location":     getattr(row, 'AddressState', '') or '',
        "seller":       _unescape(getattr(row, 'BusinessName', '') or ''),
        "business_id":  getattr(row, 'BusinessID', None),
        "species_id":   species_id,
        "species_slug": SPECIES_ID_TO_SLUG.get(species_id),
        "last_updated": row.LastUpdated.isoformat() if getattr(row, 'LastUpdated', None) else None,
    }


# OUTER APPLY (not LEFT JOIN) for photos and business: a PeopleID can hold
# several active BusinessAccess rows, which would otherwise fan one animal out
# into duplicate rows and shift the "most recent" pick.
#
# TOP 12 is a lookahead, not a page: only the first row with a usable photo is
# kept, so this just needs enough headroom to skip recent photo-less records.
_FEATURED_SQL = """
    SELECT TOP 12
        a.AnimalID, a.FullName, a.SpeciesID, a.Category, a.LastUpdated,
        a.Description, a.StudDescription,
        ph.ListPageImage, ph.Photo1, ph.Photo2, ph.Photo3, ph.Photo4,
        ph.Photo5, ph.Photo6, ph.Photo7, ph.Photo8,
        p.Price, p.StudFee,
        b1.Breed AS Breed1, b2.Breed AS Breed2,
        biz.BusinessID, biz.BusinessName, addr.AddressState
    FROM Animals a
    OUTER APPLY (
        SELECT TOP 1 * FROM Pricing x WHERE x.AnimalID = a.AnimalID
    ) p
    OUTER APPLY (
        SELECT TOP 1 * FROM Photos x WHERE x.AnimalID = a.AnimalID
    ) ph
    LEFT JOIN SpeciesBreedLookupTable b1 ON b1.BreedLookupID = a.BreedID
    LEFT JOIN SpeciesBreedLookupTable b2 ON b2.BreedLookupID = a.BreedID2
    OUTER APPLY (
        SELECT TOP 1 b.BusinessID, b.BusinessName, b.AddressID
        FROM Business b
        WHERE b.BusinessID = COALESCE(a.BusinessID, (
            SELECT TOP 1 ba.BusinessID FROM BusinessAccess ba
            WHERE ba.PeopleID = a.PeopleID AND ba.Active = 1
            ORDER BY ba.BusinessID
        ))
    ) biz
    LEFT JOIN Address addr ON addr.AddressID = biz.AddressID
    WHERE {publish_flag}
    ORDER BY a.LastUpdated DESC
"""


@marketplace_router.get("/homepage-featured")
def livestock_homepage_featured(db: Session = Depends(get_db)):
    """Homepage feature cards: the newest for-sale animal and the newest stud.

    Candidates come back newest-first; we take the newest one that actually has
    a photo so the feature card never renders as an empty frame, and fall back
    to the newest overall when none of the recent records has one.
    """
    cached = _livestock_cache.get("homepage_featured")
    if cached and _time.time() - cached["ts"] < _CACHE_TTL:
        return cached["data"]

    def candidates(studs: bool, flag: str) -> list:
        try:
            rows = db.execute(text(_FEATURED_SQL.format(publish_flag=flag))).fetchall()
            return [_featured_dict(r, studs) for r in rows]
        except Exception:
            import traceback; traceback.print_exc()
            return []

    def lead_of(picks: list):
        if not picks:
            return None
        return next((p for p in picks if p["photo"]), picks[0])

    for_sale = lead_of(candidates(False, "a.PublishForSale = 1"))
    stud     = lead_of(candidates(True,  "a.PublishStud = 1"))

    result = {"for_sale": for_sale, "stud": stud}
    _livestock_cache["homepage_featured"] = {"data": result, "ts": _time.time()}
    return result


@marketplace_router.get("/species/{slug}")
def livestock_species_info(slug: str):
    """Return singular term and label for a species slug."""
    return {
        "slug":          slug,
        "singular_term": SLUG_TO_SINGULAR.get(slug, ''),
        "label":         slug.replace('-', ' ').title(),
    }


@marketplace_router.get("/filters/{slug}")
def livestock_filters(slug: str, db: Session = Depends(get_db)):
    """Return available breeds and states for the given species slug."""
    species_id = SLUG_TO_SPECIES_ID.get(slug)
    if not species_id:
        return {"breeds": [], "states": [], "ranches": []}
    try:
        breed_rows = db.execute(text("""
            SELECT DISTINCT sbl.BreedLookupID AS id, sbl.Breed AS name
            FROM SpeciesBreedLookupTable sbl
            JOIN Animals a ON a.BreedID = sbl.BreedLookupID
            JOIN Pricing p ON p.AnimalID = a.AnimalID
            WHERE sbl.SpeciesID = :sid
              AND (a.PublishForSale = 1 OR a.PublishStud = 1)
            ORDER BY sbl.Breed
        """), {"sid": species_id}).fetchall()

        state_rows = db.execute(text("""
            SELECT DISTINCT addr.AddressState AS state, addr.StateIndex AS state_index
            FROM Animals a
            LEFT JOIN BusinessAccess ba ON ba.PeopleID = a.PeopleID AND ba.Active = 1
            JOIN Business biz      ON biz.BusinessID = COALESCE(a.BusinessID, ba.BusinessID)
            JOIN Address addr      ON addr.AddressID = biz.AddressID
            WHERE a.SpeciesID = :sid
              AND (a.PublishForSale = 1 OR a.PublishStud = 1)
              AND addr.AddressState IS NOT NULL
            ORDER BY addr.AddressState
        """), {"sid": species_id}).fetchall()

        ranch_rows = db.execute(text("""
            SELECT DISTINCT biz.BusinessID AS id, biz.BusinessName AS name
            FROM Animals a
            LEFT JOIN BusinessAccess ba ON ba.PeopleID = a.PeopleID AND ba.Active = 1
            JOIN Business biz      ON biz.BusinessID = COALESCE(a.BusinessID, ba.BusinessID)
            WHERE a.SpeciesID = :sid
              AND (a.PublishForSale = 1 OR a.PublishStud = 1)
              AND biz.BusinessName IS NOT NULL
              AND LTRIM(RTRIM(biz.BusinessName)) <> ''
            ORDER BY biz.BusinessName
        """), {"sid": species_id}).fetchall()

        return {
            "breeds":  [{"id": r.id, "name": r.name} for r in breed_rows],
            "states":  [{"state": r.state, "state_index": r.state_index} for r in state_rows],
            "ranches": [{"id": r.id, "name": r.name} for r in ranch_rows],
        }
    except Exception:
        import traceback; traceback.print_exc()
        return {"breeds": [], "states": [], "ranches": []}


# Sentinel "no upper bound" used by the listing endpoints' query defaults.
_PRICE_CEILING = 100_000_000


def _livestock_listing(
    slug: str, studs: bool, page: int,
    breed_id: int, state_index: int,
    min_price: float, max_price: float,
    ancestry: str, sort_by: str, order_by: str,
    db: Session,
    business_id: int = 0,
) -> dict:
    PER_PAGE = 10
    species_id = SLUG_TO_SPECIES_ID.get(slug)
    if not species_id:
        return {"total": 0, "page": page, "per_page": PER_PAGE, "total_pages": 0, "animals": [], "label": slug}

    publish_flag = "a.PublishStud = 1" if studs else "a.PublishForSale = 1"
    price_col    = "p.StudFee"         if studs else "p.Price"

    sort_map = {
        "lastupdated": "a.LastUpdated",
        "price":       price_col,
        "name":        "a.FullName",
        "breed":       "b1.Breed",
    }
    order_sql  = sort_map.get(sort_by, "a.LastUpdated")
    dir_sql    = "ASC" if order_by == "asc" else "DESC"

    filters = [f"a.SpeciesID = :sid", publish_flag]
    params: dict = {"sid": species_id}

    # Price/StudFee is NULL on most legacy rows (every published stud has a NULL
    # StudFee). A bare `col >= :min AND col <= :max` drops all of them, because
    # any comparison with NULL is never true — that hid 100% of stud listings.
    # Only constrain rows that carry a value, and apply each bound solely when
    # the caller actually asked for it.
    if min_price is not None and min_price > 0:
        filters.append(f"{price_col} >= :min_price")
        params["min_price"] = min_price
    if max_price is not None and max_price < _PRICE_CEILING:
        filters.append(f"({price_col} IS NULL OR {price_col} <= :max_price)")
        params["max_price"] = max_price

    if breed_id and breed_id != 0:
        filters.append("(a.BreedID = :breed_id OR a.BreedID2 = :breed_id)")
        params["breed_id"] = breed_id

    if state_index and state_index != 0:
        filters.append("addr.StateIndex = :state_index")
        params["state_index"] = state_index

    if business_id and business_id != 0:
        # Matched via EXISTS, not the display join: an owner can hold several
        # active BusinessAccess rows, and the join below resolves only one of
        # them for display. EXISTS still finds the animal under any of them.
        filters.append("""(
            a.BusinessID = :business_id
            OR EXISTS (
                SELECT 1 FROM BusinessAccess ba2
                WHERE ba2.PeopleID = a.PeopleID
                  AND ba2.Active = 1
                  AND ba2.BusinessID = :business_id
            )
        )""")
        params["business_id"] = business_id

    where = " AND ".join(filters)

    # Every join here must yield at most one row per animal, or listings get
    # duplicated and `total` inflates (21 owners hold multiple active
    # BusinessAccess rows, which turned 92 alpaca studs into 116 result rows).
    # Pricing is an OUTER APPLY rather than an inner join because an animal
    # with no Pricing row is still a real listing — it just shows "call for
    # price" instead of disappearing.
    join_block = """
        OUTER APPLY (SELECT TOP 1 * FROM Pricing x WHERE x.AnimalID = a.AnimalID) p
        OUTER APPLY (SELECT TOP 1 * FROM Photos  x WHERE x.AnimalID = a.AnimalID) ph
        LEFT JOIN SpeciesBreedLookupTable b1 ON b1.BreedLookupID = a.BreedID
        LEFT JOIN SpeciesBreedLookupTable b2 ON b2.BreedLookupID = a.BreedID2
        OUTER APPLY (
            SELECT TOP 1 b.BusinessID, b.BusinessName, b.AddressID
            FROM Business b
            WHERE b.BusinessID = COALESCE(a.BusinessID, (
                SELECT TOP 1 ba.BusinessID FROM BusinessAccess ba
                WHERE ba.PeopleID = a.PeopleID AND ba.Active = 1
                ORDER BY ba.BusinessID
            ))
        ) biz
        LEFT JOIN Address addr  ON addr.AddressID  = biz.AddressID
    """

    total = db.execute(text(f"""
        SELECT COUNT(*) FROM Animals a {join_block} WHERE {where}
    """), params).scalar() or 0

    offset = (page - 1) * PER_PAGE
    rows = db.execute(text(f"""
        SELECT a.AnimalID, a.FullName, a.LastUpdated,
               ph.Photo1, ph.Photo2, ph.ListPageImage,
               p.Price, p.StudFee,
               b1.Breed AS Breed1, b2.Breed AS Breed2,
               biz.BusinessID, biz.BusinessName, addr.AddressState
        FROM Animals a {join_block}
        WHERE {where}
        ORDER BY {order_sql} {dir_sql}
        OFFSET :offset ROWS FETCH NEXT :per_page ROWS ONLY
    """), {**params, "offset": offset, "per_page": PER_PAGE}).fetchall()

    # Batch-lookup upcoming events for the businesses on this page
    biz_ids = {r.BusinessID for r in rows if getattr(r, 'BusinessID', None)}
    events_by_biz: dict[int, dict] = {}
    if biz_ids:
        try:
            ev_rows = db.execute(text("""
                SELECT EventID, BusinessID, EventName, EventStartDate, EventEndDate
                FROM OFNEvents
                WHERE BusinessID IN :bids
                  AND (Deleted IS NULL OR Deleted = 0)
                  AND IsPublished = 1
                  AND (EventEndDate IS NULL OR EventEndDate >= GETDATE())
                ORDER BY EventStartDate ASC
            """).bindparams(bindparam("bids", expanding=True)),
                {"bids": list(biz_ids)}).fetchall()
            for ev in ev_rows:
                bid = ev.BusinessID
                if bid not in events_by_biz:
                    events_by_biz[bid] = {
                        "EventID": ev.EventID,
                        "EventName": ev.EventName,
                        "EventStartDate": ev.EventStartDate.isoformat() if ev.EventStartDate else None,
                    }
        except Exception:
            pass

    animals = []
    for r in rows:
        a = _animal_dict(r, studs)
        bid = getattr(r, 'BusinessID', None)
        if bid and bid in events_by_biz:
            a["upcoming_event"] = events_by_biz[bid]
        animals.append(a)

    return {
        "total":       total,
        "page":        page,
        "per_page":    PER_PAGE,
        "total_pages": max(1, -(-total // PER_PAGE)),
        "label":       slug.replace('-', ' ').title(),
        "animals":     animals,
    }


@marketplace_router.get("/for-sale/{slug}")
def livestock_for_sale(
    slug: str,
    page:        int   = Query(1,          ge=1),
    breed_id:    int   = Query(0),
    state_index: int   = Query(0),
    business_id: int   = Query(0),
    min_price:   float = Query(0),
    max_price:   float = Query(_PRICE_CEILING),
    ancestry:    str   = Query("Any"),
    sort_by:     str   = Query("lastupdated"),
    order_by:    str   = Query("desc"),
    db: Session = Depends(get_db),
):
    try:
        return _livestock_listing(slug, False, page, breed_id, state_index,
                                  min_price, max_price, ancestry, sort_by, order_by, db,
                                  business_id=business_id)
    except Exception:
        import traceback; traceback.print_exc()
        return {"total": 0, "page": page, "per_page": 10, "total_pages": 0, "animals": [], "label": slug}


@marketplace_router.get("/studs/{slug}")
def livestock_studs(
    slug: str,
    page:        int   = Query(1,          ge=1),
    breed_id:    int   = Query(0),
    state_index: int   = Query(0),
    business_id: int   = Query(0),
    min_stud_fee:  float = Query(0),
    max_stud_fee:  float = Query(_PRICE_CEILING),
    ancestry:    str   = Query("Any"),
    sort_by:     str   = Query("lastupdated"),
    order_by:    str   = Query("desc"),
    db: Session = Depends(get_db),
):
    try:
        return _livestock_listing(slug, True, page, breed_id, state_index,
                                  min_stud_fee, max_stud_fee, ancestry, sort_by, order_by, db,
                                  business_id=business_id)
    except Exception:
        import traceback; traceback.print_exc()
        return {"total": 0, "page": page, "per_page": 10, "total_pages": 0, "animals": [], "label": slug}


# ── Animal detail page ────────────────────────────────────────────────────────

SPECIES_ID_TO_SLUG = {v: k for k, v in SLUG_TO_SPECIES_ID.items()}

def _photo_url(filename) -> str | None:
    """Return a GCS URL for the given filename value, or None.

    If the value is already a GCS URL it is returned as-is.
    Otherwise we construct one from the bare filename so the browser can
    attempt to load it — onError handlers on the frontend hide any that 404.
    """
    if not filename:
        return None
    s = str(filename).strip()
    if not s or s == "0" or len(s) < 3:
        return None
    if s.startswith(_GCS_PREFIX):
        return s
    from urllib.parse import quote
    fname = s.split("/")[-1].strip()
    if not fname or len(fname) < 3:
        return None
    return f"{GCP_ANIMALS}/{quote(fname, safe='')}"


@marketplace_router.get("/animal/{animal_id}/progeny")
def get_animal_progeny(animal_id: int, db: Session = Depends(get_db)):
    """Return all animals for which this animal is a direct parent (Sire or Dam).

    Matches Ancestors.SireLink/DamLink on this animal's ID, and falls back to
    matching Ancestors.Sire/Ancestors.Dam on the animal's FullName for legacy
    data that was entered as free text."""
    parent = db.execute(text(
        "SELECT AnimalID, FullName, Category FROM Animals WHERE AnimalID = :aid"
    ), {"aid": animal_id}).fetchone()
    if not parent:
        raise HTTPException(status_code=404, detail="Animal not found")
    parent_d = dict(parent._mapping)
    parent_name = (parent_d.get("FullName") or "").strip()
    parent_cat  = (parent_d.get("Category") or "").lower()

    link_like = f"%/animal/{animal_id}"
    sql = (
        "SELECT DISTINCT a.AnimalID, a.FullName, a.SpeciesID, a.Category, "
        "       a.DOBMonth, a.DOBDay, a.DOBYear, "
        "       p.Photo1, "
        "       c.Color1, c.Color2, c.Color3, c.Color4, c.Color5 "
        "FROM Animals a "
        "JOIN Ancestors anc ON anc.AnimalID = a.AnimalID "
        "LEFT JOIN Photos p ON p.AnimalID = a.AnimalID "
        "LEFT JOIN Colors c ON c.AnimalID = a.AnimalID "
        "WHERE anc.SireLink LIKE :link_like "
        "   OR anc.DamLink  LIKE :link_like "
    )
    params = {"link_like": link_like}
    if parent_name:
        sql += "   OR RTRIM(LTRIM(anc.Sire)) = :pname OR RTRIM(LTRIM(anc.Dam)) = :pname "
        params["pname"] = parent_name
    sql += "ORDER BY a.DOBYear DESC, a.DOBMonth DESC, a.DOBDay DESC, a.FullName"
    rows = db.execute(text(sql), params).fetchall()

    out = []
    for r in rows:
        d = dict(r._mapping)
        colors = [d.get(f"Color{i}") for i in range(1, 6)]
        colors = [c for c in colors if c and str(c).strip()]
        out.append({
            "animal_id":  d["AnimalID"],
            "full_name":  d.get("FullName") or "",
            "species_id": d.get("SpeciesID"),
            "category":   d.get("Category") or "",
            "dob_year":   d.get("DOBYear"),
            "dob_month":  d.get("DOBMonth"),
            "dob_day":    d.get("DOBDay"),
            "photo":      _photo_url(d.get("Photo1")),
            "colors":     ", ".join(colors),
        })
    return {
        "parent_id":   animal_id,
        "parent_name": parent_name,
        "parent_gender": "female" if any(w in parent_cat for w in ("female", "dam", "maiden"))
                         else ("male" if any(w in parent_cat for w in ("male", "herdsire", "stud")) else None),
        "progeny":     out,
    }


_FIBER_FIELDS = (
    "SampleDateMonth", "SampleDateDay", "SampleDateYear",
    "Average", "StandardDev", "COV", "GreaterThan30",
    "BlanketWeight", "ShearWeight", "CF", "Length", "Curve", "CrimpPerInch",
)


def _fiber_row_has_value(row: dict) -> bool:
    """True when a Fiber row has anything the detail table would actually show.

    '0' counts as empty here to match how the page renders: every cell falls
    back to an em-dash for a falsy value, so a row whose only content is 0
    still displays as a full line of dashes.
    """
    for field in _FIBER_FIELDS:
        value = row.get(field)
        if value is None:
            continue
        text_value = str(value).strip()
        if text_value and text_value not in ("0", "0.0", "0.00"):
            return True
    return False


@marketplace_router.get("/animal/{animal_id}")
def get_animal_detail(animal_id: int, lang: str = "en", db: Session = Depends(get_db)):
    """Public endpoint — returns everything needed for the animal detail page."""

    # ── core animal fields (no fragile outer joins) ───────────────────────────
    row = db.execute(text("""
        SELECT
            a.AnimalID, a.FullName, a.SpeciesID, a.Description, a.StudDescription,
            a.DOBMonth, a.DOBDay, a.DOBYear,
            a.Category, a.BreedID, a.BreedID2, a.BreedID3, a.BreedID4,
            a.Weight, a.Height, a.Horns, a.Gaited, a.Warmblooded, a.Temperment,
            a.Vaccinations, a.PublishStud, a.PublishForSale,
            a.LastUpdated, a.PeopleID, a.BusinessID,
            a.CoOwnerName1, a.CoOwnerLink1, a.CoOwnerBusiness1,
            a.CoOwnerName2, a.CoOwnerLink2, a.CoOwnerBusiness2,
            a.CoOwnerName3, a.CoOwnerLink3, a.CoOwnerBusiness3
        FROM Animals a
        WHERE a.AnimalID = :aid
    """), {"aid": animal_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Animal not found")

    d = dict(row._mapping)
    people_id = d.get("PeopleID")

    # ── pricing ───────────────────────────────────────────────────────────────
    pr = db.execute(text(
        "SELECT Price, StudFee, Free, Sold, PriceComments, Financeterms "
        "FROM Pricing WHERE AnimalID = :aid"
    ), {"aid": animal_id}).fetchone()
    pr = dict(pr._mapping) if pr else {}

    # ── colors ────────────────────────────────────────────────────────────────
    cr = db.execute(text(
        "SELECT Color1, Color2, Color3, Color4, Color5 FROM Colors WHERE AnimalID = :aid"
    ), {"aid": animal_id}).fetchone()
    cr = dict(cr._mapping) if cr else {}

    # ── ancestry ─────────────────────────────────────────────────────────────
    anc_row = db.execute(text("SELECT * FROM Ancestors WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
    # DB column casing is inconsistent (e.g. Siredam, Damsire, DamAri). Build a
    # case-insensitive accessor so downstream .get("SireDam") works regardless.
    _anc_raw = dict(anc_row._mapping) if anc_row else {}
    _anc_ci = {k.lower(): v for k, v in _anc_raw.items()}
    class _AncCI:
        def get(self, key, default=None):
            return _anc_ci.get(key.lower(), default)
    anc = _AncCI()

    # ── alpaca bloodline percentages (optional; columns may not exist) ───────
    bloodline = {}
    try:
        pct_row = db.execute(text(
            "SELECT PercentPeruvian, PercentChilean, PercentBolivian, "
            "PercentUnknownOther, PercentAccoyo FROM Animals WHERE AnimalID = :aid"
        ), {"aid": animal_id}).fetchone()
        if pct_row:
            for key, label in (
                ("PercentPeruvian",     "Peruvian"),
                ("PercentChilean",      "Chilean"),
                ("PercentBolivian",     "Bolivian"),
                ("PercentUnknownOther", "Unknown / Other"),
                ("PercentAccoyo",       "Accoyo"),
            ):
                val = getattr(pct_row, key, None)
                if val and str(val).strip():
                    bloodline[label] = str(val).strip()
    except Exception:
        db.rollback()

    # ── photos (up to 16) ─────────────────────────────────────────────────────
    photos_row = db.execute(text(
        "SELECT Photo1,Photo2,Photo3,Photo4,Photo5,Photo6,Photo7,Photo8,"
        "Photo9,Photo10,Photo11,Photo12,Photo13,Photo14,Photo15,Photo16,"
        "AnimalVideo,Histogram,FiberAnalysis,ARI "
        "FROM Photos WHERE AnimalID = :aid"
    ), {"aid": animal_id}).fetchone()

    photos = []
    video_url = histogram_url = fiber_analysis_url = registration_url = None
    if photos_row:
        for i in range(1, 17):
            url = _photo_url(getattr(photos_row, f"Photo{i}", None))
            if url:
                photos.append(url)
        video_url          = _photo_url(getattr(photos_row, "AnimalVideo", None))
        histogram_url      = _photo_url(getattr(photos_row, "Histogram", None))
        fiber_analysis_url = _photo_url(getattr(photos_row, "FiberAnalysis", None))
        registration_url   = _photo_url(getattr(photos_row, "ARI", None))

    # ── owner: prefer Animals.BusinessID direct link, fall back to People → BusinessAccess ──
    owner_info = {"business_name": None, "business_id": None, "city": None,
                  "state": None, "logo": None, "people_id": people_id}
    direct_biz_id = d.get("BusinessID")
    biz_row = None
    if direct_biz_id:
        biz_row = db.execute(text("""
            SELECT b.BusinessName, b.BusinessID, b.Logo, addr.AddressCity, addr.AddressState
            FROM Business b
            LEFT JOIN Address addr ON addr.AddressID = b.AddressID
            WHERE b.BusinessID = :bid
        """), {"bid": direct_biz_id}).fetchone()
    if not biz_row and people_id:
        biz_row = db.execute(text("""
            SELECT b.BusinessName, b.BusinessID, b.Logo, addr.AddressCity, addr.AddressState
            FROM BusinessAccess ba
            JOIN Business b   ON b.BusinessID  = ba.BusinessID
            LEFT JOIN Address addr ON addr.AddressID = b.AddressID
            WHERE ba.PeopleID = :pid AND ba.Active = 1
        """), {"pid": people_id}).fetchone()
    if biz_row:
        owner_info["business_name"] = biz_row.BusinessName
        owner_info["business_id"]   = biz_row.BusinessID
        owner_info["city"]          = biz_row.AddressCity
        owner_info["state"]         = biz_row.AddressState
        owner_info["logo"]          = _photo_url(biz_row.Logo)

    # ── species singular/plural terms ─────────────────────────────────────────
    species_id = d.get("SpeciesID")

    # ── resolve Category (stored as SpeciesCategoryID) to a readable name ─────
    category_display = d.get("Category")
    if category_display is not None:
        raw_cat = str(category_display).strip()
        if raw_cat.isdigit():
            cat_row = db.execute(text(
                "SELECT SpeciesCategory FROM speciescategory WHERE SpeciesCategoryID = :cid"
            ), {"cid": int(raw_cat)}).fetchone()
            if cat_row:
                category_display = cat_row[0]
            elif raw_cat == "0":
                category_display = None

    # ── breed names ───────────────────────────────────────────────────────────
    breed_names = []
    for bid_col in ("BreedID", "BreedID2", "BreedID3", "BreedID4"):
        bid = d.get(bid_col)
        if bid:
            br = db.execute(text(
                "SELECT Breed FROM SpeciesBreedLookupTable WHERE BreedLookupID = :bid"
            ), {"bid": bid}).fetchone()
            if br:
                breed_names.append(br[0])

    # ── registration numbers ──────────────────────────────────────────────────
    reg_rows = db.execute(text("""
        SELECT DISTINCT RegType, RegNumber FROM AnimalRegistration
        WHERE AnimalID = :aid AND RegNumber IS NOT NULL AND RegNumber != ''
        ORDER BY RegType
    """), {"aid": animal_id}).fetchall()
    registrations = [{"type": r.RegType, "number": r.RegNumber} for r in reg_rows]

    # ── awards ────────────────────────────────────────────────────────────────
    award_rows = db.execute(text("""
        SELECT AwardYear, ShowName, Placing, Type, Awardcomments
        FROM Awards WHERE AnimalID = :aid ORDER BY Placing ASC
    """), {"aid": animal_id}).fetchall()
    awards = [
        {
            "AwardYear":     r.AwardYear,
            "ShowName":      r.ShowName,
            "Placing":       r.Placing,
            "AwardClass":    r.Type,
            "AwardComments": r.Awardcomments,
        }
        for r in award_rows
        if any([r.AwardYear and str(r.AwardYear) != "0",
                r.ShowName and str(r.ShowName).strip(),
                r.Placing and str(r.Placing).strip(),
                r.Type and str(r.Type).strip()])
    ]

    # ── fiber stats (correct casing from actual table) ────────────────────────
    fiber_rows = db.execute(text("""
        SELECT SampleDateMonth, SampleDateDay, SampleDateYear,
               Average, StandardDev, COV, GreaterThan30,
               BlanketWeight, ShearWeight, CF, Length, Curve, CrimpPerInch
        FROM Fiber WHERE AnimalID = :aid
        ORDER BY SampleDateYear DESC, Average DESC
    """), {"aid": animal_id}).fetchall()
    # 96% of Fiber rows (35,671 of 37,051) are blank legacy placeholders with
    # nothing in any column. They rendered as a row of em-dashes on the detail
    # page, so drop them here and keep only rows that carry a real value.
    fiber_stats = [d for d in (dict(r._mapping) for r in fiber_rows)
                   if _fiber_row_has_value(d)]

    # ── species slug ──────────────────────────────────────────────────────────
    species_slug     = SPECIES_ID_TO_SLUG.get(species_id)
    species_singular = SLUG_TO_SINGULAR.get(species_slug, "Animal")

    result = {
        "animal_id":        d["AnimalID"],
        "full_name":        _unescape(d.get("FullName") or ""),
        "species_id":       species_id,
        "species_slug":     species_slug,
        "species_singular": species_singular,
        "description":      _unescape(d.get("Description") or ""),
        "stud_description": _unescape(d.get("StudDescription") or ""),
        "dob": {"month": d.get("DOBMonth"), "day": d.get("DOBDay"), "year": d.get("DOBYear")},
        "category":     category_display,
        "breeds":       [_unescape(b) for b in breed_names],
        "colors":       [x.strip() for x in [cr.get("Color1"), cr.get("Color2"), cr.get("Color3"), cr.get("Color4"), cr.get("Color5")] if x and str(x).strip()],
        "weight":       d.get("Weight"),
        "height":       d.get("Height"),
        "horns":        d.get("Horns"),
        "gaited":       d.get("Gaited"),
        "warmblooded":  d.get("Warmblooded"),
        "temperament":  d.get("Temperment"),
        "vaccinations": d.get("Vaccinations"),
        "pricing": {
            "price":          float(pr["Price"])   if pr.get("Price")   else None,
            "stud_fee":       float(pr["StudFee"]) if pr.get("StudFee") else None,
            "free":           bool(pr.get("Free")),
            "sold":           bool(pr.get("Sold")),
            "price_comments": pr.get("PriceComments"),
        },
        "sold":             bool(pr.get("Sold")),
        "sale_pending":     False,
        "publish_stud":     bool(d.get("PublishStud")),
        "publish_for_sale": bool(d.get("PublishForSale")),
        "finance_terms":    pr.get("Financeterms"),
        "last_updated":     str(d["LastUpdated"]) if d.get("LastUpdated") else None,
        "co_owners": [x for x in [
            {"name": d.get("CoOwnerName1"), "business": d.get("CoOwnerBusiness1"), "link": d.get("CoOwnerLink1")} if d.get("CoOwnerName1") or d.get("CoOwnerBusiness1") else None,
            {"name": d.get("CoOwnerName2"), "business": d.get("CoOwnerBusiness2"), "link": d.get("CoOwnerLink2")} if d.get("CoOwnerName2") or d.get("CoOwnerBusiness2") else None,
            {"name": d.get("CoOwnerName3"), "business": d.get("CoOwnerBusiness3"), "link": d.get("CoOwnerLink3")} if d.get("CoOwnerName3") or d.get("CoOwnerBusiness3") else None,
        ] if x],
        "owner": owner_info,
        "ancestry": {
            "sire_term": "Sire",
            "dam_term":  "Dam",
            "sire":          {"name": _unescape(anc.get("Sire")),         "color": anc.get("SireColor"),         "link": anc.get("SireLink"),         "reg": anc.get("SireARI")},
            "sire_sire":     {"name": _unescape(anc.get("SireSire")),     "color": anc.get("SireSireColor"),     "link": anc.get("SireSireLink"),     "reg": anc.get("SireSireARI")},
            "sire_dam":      {"name": _unescape(anc.get("SireDam")),      "color": anc.get("SireDamColor"),      "link": anc.get("SireDamLink"),      "reg": anc.get("SireDamARI")},
            "sire_sire_sire":{"name": _unescape(anc.get("SireSireSire")), "color": anc.get("SireSireSireColor"), "link": anc.get("SireSireSireLink"), "reg": anc.get("SireSireSireARI")},
            "sire_sire_dam": {"name": _unescape(anc.get("SireSireDam")),  "color": anc.get("SireSireDamColor"),  "link": anc.get("SireSireDamLink"),  "reg": anc.get("SireSireDamARI")},
            "sire_dam_sire": {"name": _unescape(anc.get("SireDamSire")),  "color": anc.get("SireDamSireColor"),  "link": anc.get("SireDamSireLink"),  "reg": anc.get("SireDamSireARI")},
            "sire_dam_dam":  {"name": _unescape(anc.get("SireDamDam")),   "color": anc.get("SireDamDamColor"),   "link": anc.get("SireDamDamLink"),   "reg": anc.get("SireDamDamARI")},
            "dam":           {"name": _unescape(anc.get("Dam")),          "color": anc.get("DamColor"),          "link": anc.get("DamLink"),          "reg": anc.get("DamARI") or anc.get("DamAri")},
            "dam_sire":      {"name": _unescape(anc.get("DamSire")),      "color": anc.get("DamSireColor"),      "link": anc.get("DamSireLink"),      "reg": anc.get("DamSireARI")},
            "dam_dam":       {"name": _unescape(anc.get("DamDam")),       "color": anc.get("DamDamColor"),       "link": anc.get("DamDamLink"),       "reg": anc.get("DamDamARI")},
            "dam_sire_sire": {"name": _unescape(anc.get("DamSireSire")),  "color": anc.get("DamSireSireColor"),  "link": anc.get("DamSireSireLink"),  "reg": anc.get("DamSireSireARI")},
            "dam_sire_dam":  {"name": _unescape(anc.get("DamSireDam")),   "color": anc.get("DamSireDamColor"),   "link": anc.get("DamSireDamLink"),   "reg": anc.get("DamSireDamARI")},
            "dam_dam_sire":  {"name": _unescape(anc.get("DamDamSire")),   "color": anc.get("DamDamSireColor"),   "link": anc.get("DamDamSireLink"),   "reg": anc.get("DamDamSireARI")},
            "dam_dam_dam":   {"name": _unescape(anc.get("DamDamDam")),    "color": anc.get("DamDamDamColor"),    "link": anc.get("DamDamDamLink"),    "reg": anc.get("DamDamDamARI")},
            "bloodline":     bloodline,
        },
        "photos":             photos,
        "video_url":          video_url,
        "histogram_url":      histogram_url,
        "fiber_analysis_url": fiber_analysis_url,
        "registration_url":   registration_url,
        "registrations":      registrations,
        "awards":             awards,
        "fiber_stats":        fiber_stats,
    }
    return translate_fields(result, ["description", "stud_description"], lang, db)


# ─────────────────────────────────────────────
# RESTAURANT — SAVED FARMS  ("My Farms" bookmarks)
# ─────────────────────────────────────────────

class SavedFarmAdd(BaseModel):
    BuyerBusinessID: int
    FarmBusinessID:  int
    AddedByPeopleID: Optional[int] = None
    Notes:           Optional[str] = None


@marketplace_router.get("/saved-farms")
def list_saved_farms(buyer_business_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT sf.SavedID, sf.FarmBusinessID, sf.Notes, sf.CreatedAt,
               b.BusinessName,
               b.PickupAvailable, b.ShippingAvailable, b.DeliveryRadius,
               a.AddressCity, a.AddressState
        FROM RestaurantSavedFarms sf
        JOIN Business b ON sf.FarmBusinessID = b.BusinessID
        LEFT JOIN Address a ON b.AddressID = a.AddressID
        WHERE sf.BuyerBusinessID = :bid
        ORDER BY sf.CreatedAt DESC
    """), {"bid": buyer_business_id}).fetchall()
    return [dict(r._mapping) for r in rows]


@marketplace_router.post("/saved-farms")
def add_saved_farm(data: SavedFarmAdd, db: Session = Depends(get_db)):
    if data.BuyerBusinessID == data.FarmBusinessID:
        raise HTTPException(400, "A business cannot save itself.")
    try:
        db.execute(text("""
            INSERT INTO RestaurantSavedFarms (BuyerBusinessID, FarmBusinessID, AddedByPeopleID, Notes)
            VALUES (:b, :f, :p, :n)
        """), {"b": data.BuyerBusinessID, "f": data.FarmBusinessID,
               "p": data.AddedByPeopleID, "n": data.Notes})
        db.commit()
        return {"message": "Farm saved."}
    except Exception:
        db.rollback()
        # Likely cause: unique-constraint violation = farm already saved
        return {"message": "Farm was already saved."}


@marketplace_router.delete("/saved-farms")
def remove_saved_farm(buyer_business_id: int, farm_business_id: int, db: Session = Depends(get_db)):
    db.execute(text("""
        DELETE FROM RestaurantSavedFarms
        WHERE BuyerBusinessID = :b AND FarmBusinessID = :f
    """), {"b": buyer_business_id, "f": farm_business_id})
    db.commit()
    return {"message": "Farm removed."}


# ─────────────────────────────────────────────
# LIVESTOCK — SAVED ITEMS  ("My Animals" bookmarks)
# ─────────────────────────────────────────────
# The animals, studs, and ranches a signed-in person has bookmarked from the
# livestock marketplace. PeopleID always comes from the bearer token and never
# from the query string, so one user cannot read or delete another's bookmarks
# by guessing an id.

SAVED_ANIMAL_TYPES = ("animal", "stud")
SAVED_ITEM_TYPES   = ("animal", "stud", "ranch")

# Which column holds the id, per item type. Also the whitelist that keeps the
# column name in the DELETE below out of caller control.
SAVED_ID_COLUMN = {"animal": "AnimalID", "stud": "AnimalID", "ranch": "BusinessID"}


class SavedItemAdd(BaseModel):
    item_type:   str
    animal_id:   Optional[int] = None
    business_id: Optional[int] = None


def _saved_item_key(item_type, animal_id, business_id):
    """Validate one incoming bookmark; return (ItemType, AnimalID, BusinessID).

    Exactly one of the two id columns is set, matching the filtered unique
    indexes on the table. Shared by POST and DELETE so both agree on what a
    valid bookmark looks like.
    """
    t = (item_type or "").strip().lower()
    if t not in SAVED_ITEM_TYPES:
        raise HTTPException(400, f"item_type must be one of: {', '.join(SAVED_ITEM_TYPES)}.")
    is_ranch = t == "ranch"
    raw = business_id if is_ranch else animal_id
    if raw is None:
        raise HTTPException(400, f"{'business_id' if is_ranch else 'animal_id'} is required "
                                 f"when item_type is '{t}'.")
    try:
        rid = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "Saved item id must be an integer.")
    return (t, None, rid) if is_ranch else (t, rid, None)


def _load_saved_animals(db: Session, animal_ids):
    """Batch-load the Animals rows behind a set of saved animal/stud ids.

    Selects the same columns and joins _livestock_listing uses, so _animal_dict
    renders a saved card identically to the listing card it was saved from.
    One query for the whole page rather than one per bookmark. An animal that
    was deleted simply does not come back — the caller renders that as "no
    longer available" rather than dropping the row, so the user can still
    un-save it.
    """
    if not animal_ids:
        return {}
    rows = db.execute(text("""
        SELECT a.AnimalID, a.FullName, a.SpeciesID,
               ph.Photo1, ph.Photo2, ph.ListPageImage,
               p.Price, p.StudFee,
               b1.Breed AS Breed1, b2.Breed AS Breed2,
               biz.BusinessID, biz.BusinessName, addr.AddressState
        FROM Animals a
        OUTER APPLY (SELECT TOP 1 * FROM Pricing x WHERE x.AnimalID = a.AnimalID) p
        OUTER APPLY (SELECT TOP 1 * FROM Photos  x WHERE x.AnimalID = a.AnimalID) ph
        LEFT JOIN SpeciesBreedLookupTable b1 ON b1.BreedLookupID = a.BreedID
        LEFT JOIN SpeciesBreedLookupTable b2 ON b2.BreedLookupID = a.BreedID2
        OUTER APPLY (
            SELECT TOP 1 b.BusinessID, b.BusinessName, b.AddressID
            FROM Business b
            WHERE b.BusinessID = COALESCE(a.BusinessID, (
                SELECT TOP 1 ba.BusinessID FROM BusinessAccess ba
                WHERE ba.PeopleID = a.PeopleID AND ba.Active = 1
                ORDER BY ba.BusinessID
            ))
        ) biz
        LEFT JOIN Address addr ON addr.AddressID = biz.AddressID
        WHERE a.AnimalID IN :ids
    """).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(animal_ids)}).fetchall()
    return {r.AnimalID: r for r in rows}


def _load_saved_ranches(db: Session, business_ids):
    """Batch-load the Business rows behind a set of saved ranch ids."""
    if not business_ids:
        return {}
    rows = db.execute(text("""
        SELECT b.BusinessID, b.BusinessName, b.BusinessLogo,
               a.AddressCity, a.AddressState, a.AddressCountry
        FROM Business b
        LEFT JOIN Address a ON b.AddressID = a.AddressID
        WHERE b.BusinessID IN :ids
    """).bindparams(bindparam("ids", expanding=True)),
        {"ids": list(business_ids)}).fetchall()
    return {r.BusinessID: r for r in rows}


def _saved_ranch_dict(row) -> dict:
    """Ranch card fields, matching the shape /api/ranches/list/{slug} returns.

    Reuses the ranches router's logo normaliser: raw BusinessLogo values are
    legacy paths on retired domains that do not resolve as-is.
    """
    from routers.ranches import _fix_logo, _safe_str
    return {
        "business_id":   row.BusinessID,
        "business_name": _safe_str(getattr(row, "BusinessName", "")),
        "logo":          _fix_logo(getattr(row, "BusinessLogo", None)),
        "city":          _safe_str(getattr(row, "AddressCity", "")),
        "state":         _safe_str(getattr(row, "AddressState", "")),
        "country":       _safe_str(getattr(row, "AddressCountry", "")),
    }


@marketplace_router.get("/saved")
def list_saved_items(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    """Everything the signed-in person has saved, fully hydrated.

    This single response serves both consumers: the Saved tab renders `items`,
    and every SaveButton reads its checked state off the `animals` / `studs` /
    `ranches` id lists in the same payload, fetched once and shared through the
    SavedItems context. The cost is bounded by how many things the user saved,
    not by how many listings are on screen, so there is no separate ids-only
    endpoint to keep in sync.
    """
    rows = db.execute(text("""
        SELECT SavedID, ItemType, AnimalID, BusinessID, CreatedAt
        FROM LivestockSavedItems
        WHERE PeopleID = :pid
        ORDER BY CreatedAt DESC
    """), {"pid": current_user.PeopleID}).fetchall()

    animals = _load_saved_animals(
        db, {r.AnimalID for r in rows if r.ItemType in SAVED_ANIMAL_TYPES and r.AnimalID is not None})
    ranches = _load_saved_ranches(
        db, {r.BusinessID for r in rows if r.ItemType == "ranch" and r.BusinessID is not None})

    items = []
    for r in rows:
        item = {
            "saved_id":   r.SavedID,
            "item_type":  r.ItemType,
            "created_at": r.CreatedAt.isoformat() if r.CreatedAt else None,
        }
        if r.ItemType == "ranch":
            ranch = ranches.get(r.BusinessID)
            item["business_id"] = r.BusinessID
            item["ranch"] = _saved_ranch_dict(ranch) if ranch is not None else None
        else:
            animal = animals.get(r.AnimalID)
            item["animal_id"] = r.AnimalID
            item["animal"] = _animal_dict(animal, r.ItemType == "stud") if animal is not None else None
        items.append(item)

    return {
        "items":   items,
        "animals": [r.AnimalID   for r in rows if r.ItemType == "animal" and r.AnimalID is not None],
        "studs":   [r.AnimalID   for r in rows if r.ItemType == "stud"   and r.AnimalID is not None],
        "ranches": [r.BusinessID for r in rows if r.ItemType == "ranch"  and r.BusinessID is not None],
    }


@marketplace_router.post("/saved")
def add_saved_item(data: SavedItemAdd, db: Session = Depends(get_db),
                   current_user = Depends(get_current_user)):
    item_type, animal_id, business_id = _saved_item_key(
        data.item_type, data.animal_id, data.business_id)
    try:
        db.execute(text("""
            INSERT INTO LivestockSavedItems (PeopleID, ItemType, AnimalID, BusinessID)
            VALUES (:pid, :t, :aid, :bid)
        """), {"pid": current_user.PeopleID, "t": item_type,
               "aid": animal_id, "bid": business_id})
        db.commit()
        return {"message": "Item saved.", "item_type": item_type,
                "animal_id": animal_id, "business_id": business_id}
    except Exception:
        db.rollback()
        # Likely cause: filtered-unique-index violation = already saved. Saving
        # the same thing twice is what a double-click looks like, so report success.
        return {"message": "Item was already saved.", "item_type": item_type,
                "animal_id": animal_id, "business_id": business_id}


@marketplace_router.delete("/saved")
def remove_saved_item(item_type: str, animal_id: Optional[int] = None,
                      business_id: Optional[int] = None, db: Session = Depends(get_db),
                      current_user = Depends(get_current_user)):
    t, a_id, b_id = _saved_item_key(item_type, animal_id, business_id)
    # Column comes from SAVED_ID_COLUMN, keyed by an already-validated item
    # type, so it is never caller-controlled text. The id stays a bind param.
    col = SAVED_ID_COLUMN[t]
    db.execute(text(f"""
        DELETE FROM LivestockSavedItems
        WHERE PeopleID = :pid AND ItemType = :t AND {col} = :iid
    """), {"pid": current_user.PeopleID, "t": t, "iid": b_id if t == "ranch" else a_id})
    db.commit()
    return {"message": "Item removed."}


# ─────────────────────────────────────────────
# RESTAURANT — STANDING ORDERS  (recurring purchases)
# ─────────────────────────────────────────────

def _compute_next_delivery_date(frequency: str, day_of_week, from_date=None):
    """DayOfWeek convention: 0=Sun..6=Sat (matches JS Date.getDay)."""
    from datetime import date, timedelta
    base = from_date or date.today()
    interval_days = {'weekly': 7, 'biweekly': 14, 'monthly': 30}.get(frequency, 7)
    if day_of_week is None:
        return base + timedelta(days=interval_days)
    js_today = (base.weekday() + 1) % 7  # Python Mon=0..Sun=6 → JS Sun=0..Sat=6
    delta = (int(day_of_week) - js_today) % 7
    if delta == 0:
        delta = interval_days
    return base + timedelta(days=delta)


class StandingOrderCreate(BaseModel):
    BuyerBusinessID:   int
    FarmBusinessID:    int
    ListingType:       str           # 'produce' | 'meat' | 'processed_food' | 'sf'
    ListingSourceID:   int
    ProductTitle:      Optional[str] = None
    Quantity:          float = 1
    UnitLabel:         Optional[str] = None
    Frequency:         str = 'weekly'  # weekly | biweekly | monthly
    DayOfWeek:         Optional[int] = None
    NextDeliveryDate:  Optional[str] = None  # YYYY-MM-DD; auto-computed if omitted
    Notes:             Optional[str] = None
    CreatedByPeopleID: Optional[int] = None


class StandingOrderUpdate(BaseModel):
    Quantity:         Optional[float] = None
    Frequency:        Optional[str]   = None
    DayOfWeek:        Optional[int]   = None
    NextDeliveryDate: Optional[str]   = None
    Status:           Optional[str]   = None
    Notes:            Optional[str]   = None


@marketplace_router.get("/standing-orders")
def list_standing_orders(
    buyer_business_id: Optional[int] = None,
    farm_business_id:  Optional[int] = None,
    db: Session = Depends(get_db),
):
    """List standing orders from either side. Supply EITHER buyer_business_id
    (restaurant's view of its outgoing orders) OR farm_business_id (farm's view
    of its incoming orders). The returned rows include both sides' business names."""
    if buyer_business_id is None and farm_business_id is None:
        raise HTTPException(400, "Provide buyer_business_id or farm_business_id.")

    where = "so.BuyerBusinessID = :bid" if buyer_business_id is not None else "so.FarmBusinessID = :fid"
    params = {"bid": buyer_business_id} if buyer_business_id is not None else {"fid": farm_business_id}

    rows = db.execute(text(f"""
        SELECT so.StandingOrderID, so.BuyerBusinessID, so.FarmBusinessID,
               so.ListingType, so.ListingSourceID, so.ProductTitle,
               so.Quantity, so.UnitLabel,
               so.Frequency, so.DayOfWeek, so.NextDeliveryDate,
               so.Status, so.Notes, so.CreatedAt, so.UpdatedAt,
               fb.BusinessName AS FarmName,
               bb.BusinessName AS BuyerName,
               a.AddressCity AS FarmCity, a.AddressState AS FarmState
        FROM RestaurantStandingOrders so
        LEFT JOIN Business fb ON so.FarmBusinessID  = fb.BusinessID
        LEFT JOIN Business bb ON so.BuyerBusinessID = bb.BusinessID
        LEFT JOIN Address  a  ON fb.AddressID       = a.AddressID
        WHERE {where}
        ORDER BY CASE so.Status
                    WHEN 'overdue'   THEN 0
                    WHEN 'active'    THEN 1
                    WHEN 'paused'    THEN 2
                    WHEN 'cancelled' THEN 3
                    ELSE 4 END,
                 so.NextDeliveryDate ASC, so.CreatedAt DESC
    """), params).fetchall()
    return [dict(r._mapping) for r in rows]


@marketplace_router.get("/standing-orders/activity")
def standing_order_activity(
    business_id: int,
    role: str = "farm",
    db: Session = Depends(get_db),
):
    """Dashboard widget data: counts + next/recent deliveries for one business.
    role='farm' -> filter by FarmBusinessID; role='buyer' -> filter by BuyerBusinessID.
    Names returned describe the OTHER party (so a farm sees BuyerName, etc.)."""
    if role not in ('farm', 'buyer'):
        raise HTTPException(400, "role must be 'farm' or 'buyer'.")
    side_col = "FarmBusinessID" if role == 'farm' else "BuyerBusinessID"
    other_alias = "bb" if role == 'farm' else "fb"

    stats = db.execute(text(f"""
        SELECT
            SUM(CASE WHEN Status IN ('active','overdue') THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN Status = 'overdue' THEN 1 ELSE 0 END)              AS overdue_count,
            SUM(CASE WHEN Status IN ('active','overdue')
                      AND NextDeliveryDate IS NOT NULL
                      AND NextDeliveryDate <= DATEADD(day, 7, CAST(GETDATE() AS DATE))
                     THEN 1 ELSE 0 END)                                      AS upcoming_7d_count
        FROM RestaurantStandingOrders
        WHERE {side_col} = :bid
    """), {"bid": business_id}).fetchone()

    fulfilled_30d = db.execute(text(f"""
        SELECT COUNT(*) AS c
        FROM StandingOrderFulfillments f
        JOIN RestaurantStandingOrders so ON f.StandingOrderID = so.StandingOrderID
        WHERE so.{side_col} = :bid
          AND f.DeliveredAt >= DATEADD(day, -30, GETDATE())
    """), {"bid": business_id}).scalar() or 0

    upcoming = db.execute(text(f"""
        SELECT TOP 10
               so.StandingOrderID, so.ProductTitle, so.Quantity, so.UnitLabel,
               so.NextDeliveryDate, so.Status,
               {other_alias}.BusinessName AS OtherPartyName,
               DATEDIFF(day, CAST(GETDATE() AS DATE), so.NextDeliveryDate) AS DaysUntil
        FROM RestaurantStandingOrders so
        LEFT JOIN Business bb ON so.BuyerBusinessID = bb.BusinessID
        LEFT JOIN Business fb ON so.FarmBusinessID  = fb.BusinessID
        WHERE so.{side_col} = :bid
          AND so.Status IN ('active','overdue')
          AND so.NextDeliveryDate IS NOT NULL
        ORDER BY CASE so.Status WHEN 'overdue' THEN 0 ELSE 1 END,
                 so.NextDeliveryDate ASC
    """), {"bid": business_id}).fetchall()

    recent = db.execute(text(f"""
        SELECT TOP 10
               f.FulfillmentID, f.StandingOrderID, f.DeliveredAt, f.DeliveredQuantity,
               so.ProductTitle, so.UnitLabel,
               {other_alias}.BusinessName AS OtherPartyName
        FROM StandingOrderFulfillments f
        JOIN RestaurantStandingOrders so ON f.StandingOrderID = so.StandingOrderID
        LEFT JOIN Business bb ON so.BuyerBusinessID = bb.BusinessID
        LEFT JOIN Business fb ON so.FarmBusinessID  = fb.BusinessID
        WHERE so.{side_col} = :bid
          AND f.DeliveredAt >= DATEADD(day, -30, GETDATE())
        ORDER BY f.DeliveredAt DESC
    """), {"bid": business_id}).fetchall()

    return {
        "stats": {
            "active":        int(stats.active_count or 0)   if stats else 0,
            "overdue":       int(stats.overdue_count or 0)  if stats else 0,
            "upcoming_7d":   int(stats.upcoming_7d_count or 0) if stats else 0,
            "fulfilled_30d": int(fulfilled_30d),
        },
        "upcoming": [dict(r._mapping) for r in upcoming],
        "recent":   [dict(r._mapping) for r in recent],
    }


@marketplace_router.post("/standing-orders")
def create_standing_order(data: StandingOrderCreate, db: Session = Depends(get_db)):
    if data.ListingType not in ('produce', 'meat', 'processed_food', 'sf'):
        raise HTTPException(400, "Invalid ListingType.")
    if data.Frequency not in ('weekly', 'biweekly', 'monthly'):
        raise HTTPException(400, "Invalid Frequency.")
    next_date = data.NextDeliveryDate or _compute_next_delivery_date(data.Frequency, data.DayOfWeek).isoformat()
    db.execute(text("""
        INSERT INTO RestaurantStandingOrders
            (BuyerBusinessID, FarmBusinessID, ListingType, ListingSourceID,
             ProductTitle, Quantity, UnitLabel,
             Frequency, DayOfWeek, NextDeliveryDate, Notes, CreatedByPeopleID)
        VALUES
            (:bb, :fb, :lt, :ls, :pt, :q, :ul, :fr, :dow, :nd, :n, :p)
    """), {
        "bb": data.BuyerBusinessID, "fb": data.FarmBusinessID,
        "lt": data.ListingType, "ls": data.ListingSourceID,
        "pt": data.ProductTitle, "q": data.Quantity, "ul": data.UnitLabel,
        "fr": data.Frequency, "dow": data.DayOfWeek,
        "nd": next_date, "n": data.Notes,
        "p":  data.CreatedByPeopleID,
    })
    new_id = db.execute(text("SELECT CAST(SCOPE_IDENTITY() AS INT) AS id")).scalar()
    # Notify farm's BusinessAccess people about the new recurring order.
    buyer_name_row = db.execute(text(
        "SELECT BusinessName FROM Business WHERE BusinessID = :b"
    ), {"b": data.BuyerBusinessID}).fetchone()
    buyer_name = (buyer_name_row.BusinessName if buyer_name_row else 'A restaurant')
    notify_business(
        db, business_id=data.FarmBusinessID,
        type='standing_order.created',
        title=f"🔁 New standing order from {buyer_name}",
        body=f"{data.Quantity} {data.UnitLabel or 'unit'} of {data.ProductTitle or 'your product'}, {data.Frequency}. First delivery: {next_date}.",
        link_path='/farm/standing-orders',
        entity_type='standing_order', entity_id=new_id,
    )
    db.commit()

    notify_info = _notify_farm_new_standing_order(db, data, next_date)
    return {
        "message": "Standing order created.",
        "NextDeliveryDate": next_date,
        "farm_notified": notify_info,
    }


@marketplace_router.put("/standing-orders/{standing_order_id}")
def update_standing_order(standing_order_id: int, data: StandingOrderUpdate, db: Session = Depends(get_db)):
    supplied = data.dict(exclude_unset=True)
    fields = []
    params = {"id": standing_order_id}
    for k, v in supplied.items():
        if v is None:
            continue
        if k == 'Frequency' and v not in ('weekly', 'biweekly', 'monthly'):
            raise HTTPException(400, "Invalid Frequency.")
        if k == 'Status' and v not in ('active', 'paused', 'cancelled', 'overdue'):
            raise HTTPException(400, "Invalid Status.")
        fields.append(f"{k} = :{k}")
        params[k] = v

    # Recompute NextDeliveryDate if cadence changed and caller didn't set it explicitly.
    cadence_changed = ('Frequency' in supplied or 'DayOfWeek' in supplied)
    if cadence_changed and 'NextDeliveryDate' not in supplied:
        current = db.execute(text(
            "SELECT Frequency, DayOfWeek FROM RestaurantStandingOrders WHERE StandingOrderID = :id"
        ), {"id": standing_order_id}).fetchone()
        if current:
            freq = supplied.get('Frequency') or current.Frequency
            dow  = supplied.get('DayOfWeek') if 'DayOfWeek' in supplied else current.DayOfWeek
            next_date = _compute_next_delivery_date(freq, dow).isoformat()
            fields.append("NextDeliveryDate = :NextDeliveryDate")
            params["NextDeliveryDate"] = next_date

    if not fields:
        return {"message": "No changes."}
    fields.append("UpdatedAt = GETDATE()")
    db.execute(text(f"""
        UPDATE RestaurantStandingOrders
        SET {', '.join(fields)}
        WHERE StandingOrderID = :id
    """), params)
    db.commit()
    return {"message": "Standing order updated.", "NextDeliveryDate": params.get("NextDeliveryDate")}


_FREQUENCY_HUMAN = {'weekly': 'weekly', 'biweekly': 'every 2 weeks', 'monthly': 'monthly'}


def _notify_farm_new_standing_order(db: Session, data: 'StandingOrderCreate', next_date: str):
    """Email the farm when a restaurant creates a recurring order. Best-effort — never fails the request."""
    try:
        row = db.execute(text("""
            SELECT b.BusinessEmail AS farm_email, b.BusinessName AS farm_name,
                   rb.BusinessName AS buyer_name
            FROM Business b
            LEFT JOIN Business rb ON rb.BusinessID = :bb
            WHERE b.BusinessID = :fb
        """), {"fb": data.FarmBusinessID, "bb": data.BuyerBusinessID}).fetchone()
        if not row or not row._mapping.get('farm_email'):
            return {"sent": False, "note": "farm has no BusinessEmail on file"}
        farm_email = row._mapping['farm_email']
        farm_name  = row._mapping.get('farm_name') or 'Farm'
        buyer_name = row._mapping.get('buyer_name') or 'A restaurant'
        base_url   = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
        freq_label = _FREQUENCY_HUMAN.get(data.Frequency, data.Frequency)
        qty_label  = f"{data.Quantity}{(' ' + data.UnitLabel) if data.UnitLabel else ''}"
        subject    = f"New standing order from {buyer_name} — {data.ProductTitle or 'Marketplace item'}"
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;background:#f7f2e8;">
          <div style="background:#fff;border-radius:14px;border:1px solid #e5e7eb;overflow:hidden;">
            <div style="padding:20px 24px;background:#3D6B34;color:#fff;">
              <h1 style="margin:0;font-size:20px;">🔁 New Standing Order</h1>
              <p style="margin:4px 0 0;font-size:13px;opacity:0.9;">A restaurant signed up for a recurring delivery.</p>
            </div>
            <div style="padding:20px 24px;color:#111827;font-size:14px;line-height:1.55;">
              <p>Hi {farm_name},</p>
              <p><strong>{buyer_name}</strong> just created a standing order for:</p>
              <table style="width:100%;border-collapse:collapse;margin:12px 0;">
                <tr><td style="padding:6px 0;color:#6b7280;width:140px;">Product</td><td><strong>{data.ProductTitle or '(unnamed)'}</strong></td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">Quantity</td><td>{qty_label}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">Frequency</td><td>{freq_label}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">First delivery</td><td>{next_date}</td></tr>
              </table>
              {'<p style="background:#f9fafb;border-left:3px solid #3D6B34;padding:10px 14px;border-radius:4px;color:#374151;"><em>"' + data.Notes + '"</em></p>' if data.Notes else ''}
              <p style="margin-top:18px;">
                <a href="{base_url}/account" style="display:inline-block;padding:10px 20px;background:#3D6B34;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;font-size:14px;">View in your account →</a>
              </p>
              <p style="color:#6b7280;font-size:12px;margin-top:18px;">
                You'll get reminders ahead of each delivery. Update your availability any time from your farm profile.
              </p>
            </div>
          </div>
        </div>
        """
        sent, note = _send_email(farm_email, subject, html)
        return {"sent": sent, "to": farm_email, "note": note}
    except Exception as e:
        return {"sent": False, "note": f"notify exception: {e}"}


def _notify_fulfillment(db: Session, standing_order_id: int, fulfillment):
    """Email restaurant + farm confirming a standing-order delivery. Best-effort."""
    try:
        row = db.execute(text("""
            SELECT so.StandingOrderID, so.ProductTitle, so.Quantity, so.UnitLabel,
                   so.Frequency, so.NextDeliveryDate,
                   fb.BusinessName AS farm_name, fb.BusinessEmail AS farm_email,
                   bb.BusinessName AS buyer_name, bb.BusinessEmail AS buyer_email
            FROM RestaurantStandingOrders so
            LEFT JOIN Business fb ON so.FarmBusinessID  = fb.BusinessID
            LEFT JOIN Business bb ON so.BuyerBusinessID = bb.BusinessID
            WHERE so.StandingOrderID = :id
        """), {"id": standing_order_id}).fetchone()
        if not row:
            return {"sent": False, "note": "standing order not found"}
        m = row._mapping
        base_url = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
        qty_label = f"{fulfillment['DeliveredQuantity'] or m.get('Quantity')}{(' ' + m.get('UnitLabel')) if m.get('UnitLabel') else ''}"
        delivered_at = fulfillment['DeliveredAt']
        next_date    = fulfillment['NextDeliveryDate']
        subject = f"✓ Delivered: {m.get('ProductTitle') or 'standing order'} — {m.get('farm_name')} → {m.get('buyer_name')}"
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;background:#f7f2e8;">
          <div style="background:#fff;border-radius:14px;border:1px solid #e5e7eb;overflow:hidden;">
            <div style="padding:20px 24px;background:#3D6B34;color:#fff;">
              <h1 style="margin:0;font-size:20px;">✅ Delivery Confirmed</h1>
            </div>
            <div style="padding:20px 24px;color:#111827;font-size:14px;line-height:1.55;">
              <table style="width:100%;border-collapse:collapse;margin:4px 0 14px;">
                <tr><td style="padding:6px 0;color:#6b7280;width:140px;">Product</td><td><strong>{m.get('ProductTitle') or '(unnamed)'}</strong></td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">Delivered</td><td>{qty_label}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">On</td><td>{delivered_at}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">From</td><td>{m.get('farm_name')}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">To</td><td>{m.get('buyer_name')}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">Next delivery</td><td><strong>{next_date}</strong></td></tr>
              </table>
              {'<p style="background:#f9fafb;border-left:3px solid #3D6B34;padding:10px 14px;border-radius:4px;color:#374151;"><em>"' + fulfillment['Notes'] + '"</em></p>' if fulfillment.get('Notes') else ''}
              <p style="margin-top:18px;">
                <a href="{base_url}/restaurant/standing-orders" style="display:inline-block;padding:10px 20px;background:#3D6B34;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;font-size:14px;">View standing orders →</a>
              </p>
            </div>
          </div>
        </div>
        """
        results = []
        for email in [m.get('buyer_email'), m.get('farm_email')]:
            if email:
                sent, note = _send_email(email, subject, html)
                results.append({"to": email, "sent": sent, "note": note})
        return {"recipients": results}
    except Exception as e:
        return {"sent": False, "note": f"fulfillment notify exception: {e}"}


class StandingOrderFulfill(BaseModel):
    DeliveredQuantity:  Optional[float] = None
    Notes:              Optional[str]   = None
    RecordedByPeopleID: Optional[int]   = None


@marketplace_router.post("/standing-orders/{standing_order_id}/fulfill")
def fulfill_standing_order(standing_order_id: int, data: StandingOrderFulfill, db: Session = Depends(get_db)):
    """Record a delivery, auto-advance NextDeliveryDate, and email buyer + farm."""
    order = db.execute(text(
        "SELECT Frequency, DayOfWeek, NextDeliveryDate FROM RestaurantStandingOrders WHERE StandingOrderID = :id"
    ), {"id": standing_order_id}).fetchone()
    if not order:
        raise HTTPException(404, "Standing order not found.")

    ins = db.execute(text("""
        INSERT INTO StandingOrderFulfillments (StandingOrderID, DeliveredQuantity, Notes, RecordedByPeopleID)
        OUTPUT INSERTED.FulfillmentID, INSERTED.DeliveredAt
        VALUES (:id, :q, :n, :p)
    """), {
        "id": standing_order_id, "q": data.DeliveredQuantity,
        "n": data.Notes, "p": data.RecordedByPeopleID,
    }).fetchone()
    fulfillment_id = ins[0]
    delivered_at   = ins[1].isoformat() if ins[1] else None

    base = order.NextDeliveryDate or None
    next_date = _compute_next_delivery_date(order.Frequency, order.DayOfWeek, from_date=base).isoformat()
    db.execute(text("""
        UPDATE RestaurantStandingOrders
        SET NextDeliveryDate = :nd,
            Status = CASE WHEN Status = 'overdue' THEN 'active' ELSE Status END,
            UpdatedAt = GETDATE()
        WHERE StandingOrderID = :id
    """), {"id": standing_order_id, "nd": next_date})
    # Notify the buyer side that their delivery was marked fulfilled.
    _notify_standing_order_event(
        db, standing_order_id, recipient_side='buyer',
        notification_type='standing_order.fulfilled',
        title="✅ Your delivery was marked fulfilled",
        body=f"Delivered {data.DeliveredQuantity or '—'}. Next delivery: {next_date}.",
    )
    db.commit()

    notify = _notify_fulfillment(db, standing_order_id, {
        "DeliveredAt": delivered_at,
        "DeliveredQuantity": data.DeliveredQuantity,
        "Notes": data.Notes,
        "NextDeliveryDate": next_date,
    })

    return {
        "message": "Fulfillment recorded.",
        "FulfillmentID": fulfillment_id,
        "DeliveredAt": delivered_at,
        "NextDeliveryDate": next_date,
        "notify": notify,
    }


@marketplace_router.get("/standing-orders/{standing_order_id}/fulfillments")
def list_fulfillments(standing_order_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT FulfillmentID, StandingOrderID, DeliveredAt, DeliveredQuantity, RecordedByPeopleID, Notes
        FROM StandingOrderFulfillments
        WHERE StandingOrderID = :id
        ORDER BY DeliveredAt DESC
    """), {"id": standing_order_id}).fetchall()
    return [dict(r._mapping) for r in rows]


def _render_reminder_html(row_mapping, days_until: int, base_url: str) -> str:
    product  = row_mapping.get('ProductTitle') or '(unnamed)'
    qty      = row_mapping.get('Quantity')
    unit     = row_mapping.get('UnitLabel') or ''
    qty_line = f"{qty}{(' ' + unit) if unit else ''}"
    when     = "tomorrow" if days_until == 1 else f"in {days_until} days"
    farm     = row_mapping.get('farm_name') or 'Your farm partner'
    buyer    = row_mapping.get('buyer_name') or 'Your restaurant partner'
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;background:#f7f2e8;">
      <div style="background:#fff;border-radius:14px;border:1px solid #e5e7eb;overflow:hidden;">
        <div style="padding:20px 24px;background:#3D6B34;color:#fff;">
          <h1 style="margin:0;font-size:20px;">⏰ Delivery {when}</h1>
        </div>
        <div style="padding:20px 24px;color:#111827;font-size:14px;line-height:1.55;">
          <table style="width:100%;border-collapse:collapse;margin:4px 0 14px;">
            <tr><td style="padding:6px 0;color:#6b7280;width:140px;">Product</td><td><strong>{product}</strong></td></tr>
            <tr><td style="padding:6px 0;color:#6b7280;">Quantity</td><td>{qty_line}</td></tr>
            <tr><td style="padding:6px 0;color:#6b7280;">From</td><td>{farm}</td></tr>
            <tr><td style="padding:6px 0;color:#6b7280;">To</td><td>{buyer}</td></tr>
            <tr><td style="padding:6px 0;color:#6b7280;">Scheduled</td><td><strong>{row_mapping.get('NextDeliveryDate')}</strong></td></tr>
          </table>
          <p>This is a friendly reminder that your standing-order delivery is {when}. Mark it fulfilled in your dashboard once it's delivered.</p>
          <p style="margin-top:18px;">
            <a href="{base_url}/restaurant/standing-orders" style="display:inline-block;padding:10px 20px;background:#3D6B34;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;font-size:14px;">Open standing orders →</a>
          </p>
        </div>
      </div>
    </div>
    """


@marketplace_router.post("/standing-orders/remind-due")
def remind_due_standing_orders(db: Session = Depends(get_db)):
    """Send pre-delivery reminder emails for active orders whose NextDeliveryDate is 1-2 days out.
    Idempotent: a row's LastReminderSentFor = NextDeliveryDate blocks re-sends within the same cycle.
    Safe to call hourly from an external scheduler."""
    run = db.execute(text("""
        INSERT INTO CronJobRuns (JobName, Status) OUTPUT INSERTED.CronRunID
        VALUES ('standing_order_reminders', 'running')
    """)).fetchone()
    run_id = run[0]
    db.commit()

    processed = succeeded = failed = 0
    notes = []
    try:
        rows = db.execute(text("""
            SELECT so.StandingOrderID, so.NextDeliveryDate, so.ProductTitle,
                   so.Quantity, so.UnitLabel,
                   fb.BusinessName AS farm_name,  fb.BusinessEmail AS farm_email,
                   bb.BusinessName AS buyer_name, bb.BusinessEmail AS buyer_email,
                   DATEDIFF(day, CAST(GETDATE() AS DATE), so.NextDeliveryDate) AS days_until
            FROM RestaurantStandingOrders so
            LEFT JOIN Business fb ON so.FarmBusinessID  = fb.BusinessID
            LEFT JOIN Business bb ON so.BuyerBusinessID = bb.BusinessID
            WHERE so.Status = 'active'
              AND so.NextDeliveryDate IS NOT NULL
              AND DATEDIFF(day, CAST(GETDATE() AS DATE), so.NextDeliveryDate) BETWEEN 1 AND 2
              AND (so.LastReminderSentFor IS NULL OR so.LastReminderSentFor <> so.NextDeliveryDate)
        """)).fetchall()

        base_url = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
        for r in rows:
            processed += 1
            m = r._mapping
            days_until = int(m['days_until'])
            subject = f"⏰ Delivery {'tomorrow' if days_until == 1 else f'in {days_until} days'}: {m.get('ProductTitle') or 'standing order'}"
            html = _render_reminder_html(m, days_until, base_url)
            any_sent = False
            for email in [m.get('buyer_email'), m.get('farm_email')]:
                if email:
                    sent, note = _send_email(email, subject, html)
                    if sent:
                        any_sent = True
                    else:
                        notes.append(f"[{m['StandingOrderID']}] {email}: {note}")
            if any_sent:
                db.execute(text("""
                    UPDATE RestaurantStandingOrders
                    SET LastReminderSentFor = :nd
                    WHERE StandingOrderID = :id
                """), {"nd": m['NextDeliveryDate'], "id": m['StandingOrderID']})
                succeeded += 1
            else:
                failed += 1
                notes.append(f"[{m['StandingOrderID']}] no recipient with BusinessEmail")

        db.commit()
        status = 'success' if failed == 0 else ('partial' if succeeded > 0 else 'error')
        db.execute(text("""
            UPDATE CronJobRuns
            SET CompletedAt = GETDATE(), Status = :s,
                ItemsProcessed = :p, ItemsSucceeded = :ok, ItemsFailed = :f, Notes = :n
            WHERE CronRunID = :id
        """), {"id": run_id, "s": status, "p": processed, "ok": succeeded, "f": failed,
               "n": ("\n".join(notes))[:3500] if notes else None})
        db.commit()
        return {"run_id": run_id, "status": status, "processed": processed,
                "succeeded": succeeded, "failed": failed}
    except Exception as e:
        db.execute(text("""
            UPDATE CronJobRuns SET CompletedAt = GETDATE(), Status = 'error', Notes = :n
            WHERE CronRunID = :id
        """), {"id": run_id, "n": f"exception: {e}"})
        db.commit()
        raise


@marketplace_router.post("/standing-orders/flag-overdue")
def flag_overdue_standing_orders(db: Session = Depends(get_db)):
    """Mark active standing orders as overdue when NextDeliveryDate has passed
    with no fulfillment recorded on or after that date. Safe to run nightly."""
    run = db.execute(text("""
        INSERT INTO CronJobRuns (JobName, Status) OUTPUT INSERTED.CronRunID
        VALUES ('standing_order_flag_overdue', 'running')
    """)).fetchone()
    run_id = run[0]
    db.commit()
    try:
        result = db.execute(text("""
            UPDATE so
            SET Status = 'overdue', UpdatedAt = GETDATE()
            OUTPUT INSERTED.StandingOrderID
            FROM RestaurantStandingOrders so
            WHERE so.Status = 'active'
              AND so.NextDeliveryDate IS NOT NULL
              AND so.NextDeliveryDate < CAST(GETDATE() AS DATE)
              AND NOT EXISTS (
                  SELECT 1 FROM StandingOrderFulfillments f
                  WHERE f.StandingOrderID = so.StandingOrderID
                    AND CAST(f.DeliveredAt AS DATE) >= so.NextDeliveryDate
              )
        """)).fetchall()
        flagged = [r[0] for r in result]
        # Notify farms of each newly-overdue order.
        for soid in flagged:
            _notify_standing_order_event(
                db, soid, recipient_side='farm',
                notification_type='standing_order.overdue',
                title="⚠️ Delivery is overdue",
                body="The scheduled delivery date has passed with no fulfillment recorded. Mark it fulfilled or skip/reschedule.",
            )
        db.commit()

        db.execute(text("""
            UPDATE CronJobRuns
            SET CompletedAt = GETDATE(), Status = 'success',
                ItemsProcessed = :p, ItemsSucceeded = :ok, ItemsFailed = 0,
                Notes = :n
            WHERE CronRunID = :id
        """), {"id": run_id, "p": len(flagged), "ok": len(flagged),
               "n": f"Flagged StandingOrderIDs: {flagged}" if flagged else "No orders overdue."})
        db.commit()
        return {"run_id": run_id, "status": "success",
                "flagged_count": len(flagged), "flagged_ids": flagged}
    except Exception as e:
        db.execute(text("""
            UPDATE CronJobRuns SET CompletedAt = GETDATE(), Status = 'error', Notes = :n
            WHERE CronRunID = :id
        """), {"id": run_id, "n": f"exception: {e}"})
        db.commit()
        raise


@marketplace_router.post("/standing-orders/{standing_order_id}/advance")
def advance_standing_order(standing_order_id: int, db: Session = Depends(get_db)):
    """Roll NextDeliveryDate forward by one cycle without recording a fulfillment
    (use /fulfill to track the actual delivery; /advance is a lightweight skip)."""
    row = db.execute(text(
        "SELECT Frequency, DayOfWeek, NextDeliveryDate FROM RestaurantStandingOrders WHERE StandingOrderID = :id"
    ), {"id": standing_order_id}).fetchone()
    if not row:
        raise HTTPException(404, "Standing order not found.")
    base = row.NextDeliveryDate or None
    next_date = _compute_next_delivery_date(row.Frequency, row.DayOfWeek, from_date=base).isoformat()
    db.execute(text(
        "UPDATE RestaurantStandingOrders SET NextDeliveryDate = :nd, UpdatedAt = GETDATE() WHERE StandingOrderID = :id"
    ), {"id": standing_order_id, "nd": next_date})
    db.commit()
    return {"message": "Advanced.", "NextDeliveryDate": next_date}


class StandingOrderSkip(BaseModel):
    Reason:        Optional[str] = None
    InitiatedBy:   Optional[str] = None  # 'buyer' | 'farm' — for the email salutation


class StandingOrderReschedule(BaseModel):
    NewDate:       str                   # ISO date (YYYY-MM-DD), must be in the future
    Reason:        Optional[str] = None
    InitiatedBy:   Optional[str] = None  # 'buyer' | 'farm'


def _notify_schedule_change(db: Session, standing_order_id: int, change_type: str,
                            old_date, new_date: str, reason, initiated_by):
    """Email both parties (best-effort) when a delivery is skipped or rescheduled.
    change_type: 'skipped' | 'rescheduled'."""
    try:
        row = db.execute(text("""
            SELECT so.ProductTitle, so.Quantity, so.UnitLabel,
                   fb.BusinessName AS farm_name,  fb.BusinessEmail AS farm_email,
                   bb.BusinessName AS buyer_name, bb.BusinessEmail AS buyer_email
            FROM RestaurantStandingOrders so
            LEFT JOIN Business fb ON so.FarmBusinessID  = fb.BusinessID
            LEFT JOIN Business bb ON so.BuyerBusinessID = bb.BusinessID
            WHERE so.StandingOrderID = :id
        """), {"id": standing_order_id}).fetchone()
        if not row:
            return {"sent": False, "note": "standing order not found"}
        m = row._mapping
        base_url   = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
        product    = m.get('ProductTitle') or '(unnamed)'
        actor      = 'The restaurant' if initiated_by == 'buyer' else ('The farm' if initiated_by == 'farm' else 'Someone')
        verb       = 'skipped a delivery' if change_type == 'skipped' else 'rescheduled a delivery'
        emoji      = '⏭️' if change_type == 'skipped' else '📅'
        subject    = f"{emoji} Delivery {change_type}: {product} — {m.get('farm_name')} → {m.get('buyer_name')}"
        old_label  = old_date if old_date else '(no date set)'
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;background:#f7f2e8;">
          <div style="background:#fff;border-radius:14px;border:1px solid #e5e7eb;overflow:hidden;">
            <div style="padding:20px 24px;background:#3D6B34;color:#fff;">
              <h1 style="margin:0;font-size:20px;">{emoji} Delivery {change_type.capitalize()}</h1>
            </div>
            <div style="padding:20px 24px;color:#111827;font-size:14px;line-height:1.55;">
              <p>{actor} {verb} for the standing order below.</p>
              <table style="width:100%;border-collapse:collapse;margin:12px 0;">
                <tr><td style="padding:6px 0;color:#6b7280;width:160px;">Product</td><td><strong>{product}</strong></td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">Farm → Restaurant</td><td>{m.get('farm_name')} → {m.get('buyer_name')}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">Was scheduled</td><td>{old_label}</td></tr>
                <tr><td style="padding:6px 0;color:#6b7280;">New date</td><td><strong>{new_date}</strong></td></tr>
              </table>
              {'<p style="background:#f9fafb;border-left:3px solid #3D6B34;padding:10px 14px;border-radius:4px;color:#374151;"><em>"' + reason + '"</em></p>' if reason else ''}
              <p style="margin-top:18px;">
                <a href="{base_url}/restaurant/standing-orders" style="display:inline-block;padding:10px 20px;background:#3D6B34;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;font-size:14px;">View standing orders →</a>
              </p>
            </div>
          </div>
        </div>
        """
        results = []
        for email in [m.get('buyer_email'), m.get('farm_email')]:
            if email:
                sent, note = _send_email(email, subject, html)
                results.append({"to": email, "sent": sent, "note": note})
        return {"recipients": results}
    except Exception as e:
        return {"sent": False, "note": f"schedule-change notify exception: {e}"}


@marketplace_router.post("/standing-orders/{standing_order_id}/skip")
def skip_standing_order(standing_order_id: int, data: StandingOrderSkip,
                        db: Session = Depends(get_db)):
    """Skip the next scheduled delivery, advance NextDeliveryDate by one cycle,
    and email both buyer and farm. Status='overdue' resets to 'active'."""
    row = db.execute(text(
        "SELECT Frequency, DayOfWeek, NextDeliveryDate FROM RestaurantStandingOrders WHERE StandingOrderID = :id"
    ), {"id": standing_order_id}).fetchone()
    if not row:
        raise HTTPException(404, "Standing order not found.")
    old_date  = row.NextDeliveryDate.isoformat() if row.NextDeliveryDate else None
    base      = row.NextDeliveryDate or None
    next_date = _compute_next_delivery_date(row.Frequency, row.DayOfWeek, from_date=base).isoformat()
    db.execute(text("""
        UPDATE RestaurantStandingOrders
        SET NextDeliveryDate = :nd,
            Status = CASE WHEN Status = 'overdue' THEN 'active' ELSE Status END,
            UpdatedAt = GETDATE()
        WHERE StandingOrderID = :id
    """), {"id": standing_order_id, "nd": next_date})
    # Notify the OTHER party of the skip.
    recipient_side = 'buyer' if data.InitiatedBy == 'farm' else 'farm'
    _notify_standing_order_event(
        db, standing_order_id, recipient_side=recipient_side,
        notification_type='standing_order.skipped',
        title=f"⏭️ Delivery skipped ({old_date or '—'})",
        body=(data.Reason or '') + f" Next delivery: {next_date}.",
    )
    db.commit()
    notify = _notify_schedule_change(db, standing_order_id, 'skipped',
                                     old_date, next_date, data.Reason, data.InitiatedBy)
    return {"message": "Delivery skipped.",
            "OldDate": old_date, "NextDeliveryDate": next_date, "notify": notify}


@marketplace_router.post("/standing-orders/{standing_order_id}/reschedule")
def reschedule_standing_order(standing_order_id: int, data: StandingOrderReschedule,
                              db: Session = Depends(get_db)):
    """Move the next delivery to a specific future date and email both parties."""
    from datetime import date as _date
    try:
        new_date = _date.fromisoformat(data.NewDate)
    except ValueError:
        raise HTTPException(400, "NewDate must be ISO YYYY-MM-DD.")
    if new_date < _date.today():
        raise HTTPException(400, "NewDate must be today or later.")

    row = db.execute(text(
        "SELECT NextDeliveryDate FROM RestaurantStandingOrders WHERE StandingOrderID = :id"
    ), {"id": standing_order_id}).fetchone()
    if not row:
        raise HTTPException(404, "Standing order not found.")
    old_date = row.NextDeliveryDate.isoformat() if row.NextDeliveryDate else None
    db.execute(text("""
        UPDATE RestaurantStandingOrders
        SET NextDeliveryDate = :nd,
            Status = CASE WHEN Status = 'overdue' THEN 'active' ELSE Status END,
            UpdatedAt = GETDATE()
        WHERE StandingOrderID = :id
    """), {"id": standing_order_id, "nd": new_date.isoformat()})
    recipient_side = 'buyer' if data.InitiatedBy == 'farm' else 'farm'
    _notify_standing_order_event(
        db, standing_order_id, recipient_side=recipient_side,
        notification_type='standing_order.rescheduled',
        title=f"📅 Delivery rescheduled to {new_date.isoformat()}",
        body=(data.Reason or '') + f" (was {old_date or '—'}).",
    )
    db.commit()
    notify = _notify_schedule_change(db, standing_order_id, 'rescheduled',
                                     old_date, new_date.isoformat(), data.Reason, data.InitiatedBy)
    return {"message": "Delivery rescheduled.",
            "OldDate": old_date, "NextDeliveryDate": new_date.isoformat(), "notify": notify}


@marketplace_router.delete("/standing-orders/{standing_order_id}")
def delete_standing_order(standing_order_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM RestaurantStandingOrders WHERE StandingOrderID = :id"),
               {"id": standing_order_id})
    db.commit()
    return {"message": "Standing order deleted."}


# ─────────────────────────────────────────────
# RESTAURANT — "AVAILABLE THIS WEEK" EMAIL DIGEST
# ─────────────────────────────────────────────

class DigestSubscriptionUpsert(BaseModel):
    BuyerBusinessID: int
    Email:           str
    Frequency:       str  = 'weekly'
    SavedFarmsOnly:  bool = False
    Status:          str  = 'active'


@marketplace_router.get("/digest-subscription")
def get_digest_subscription(buyer_business_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT SubscriptionID, BuyerBusinessID, Email, Frequency,
               SavedFarmsOnly, Status, LastSentAt, CreatedAt, UpdatedAt
        FROM RestaurantDigestSubscriptions
        WHERE BuyerBusinessID = :bid
    """), {"bid": buyer_business_id}).fetchone()
    return dict(row._mapping) if row else None


@marketplace_router.post("/digest-subscription")
def upsert_digest_subscription(data: DigestSubscriptionUpsert, db: Session = Depends(get_db)):
    existing = db.execute(text("""
        SELECT SubscriptionID FROM RestaurantDigestSubscriptions WHERE BuyerBusinessID = :bid
    """), {"bid": data.BuyerBusinessID}).fetchone()
    if existing:
        db.execute(text("""
            UPDATE RestaurantDigestSubscriptions
            SET Email = :e, Frequency = :f, SavedFarmsOnly = :s, Status = :st, UpdatedAt = GETDATE()
            WHERE BuyerBusinessID = :bid
        """), {"e": data.Email, "f": data.Frequency,
               "s": 1 if data.SavedFarmsOnly else 0,
               "st": data.Status, "bid": data.BuyerBusinessID})
    else:
        db.execute(text("""
            INSERT INTO RestaurantDigestSubscriptions
                (BuyerBusinessID, Email, Frequency, SavedFarmsOnly, Status)
            VALUES (:bid, :e, :f, :s, :st)
        """), {"bid": data.BuyerBusinessID, "e": data.Email, "f": data.Frequency,
               "s": 1 if data.SavedFarmsOnly else 0, "st": data.Status})
    db.commit()
    return {"message": "Subscription saved."}


@marketplace_router.delete("/digest-subscription")
def remove_digest_subscription(buyer_business_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM RestaurantDigestSubscriptions WHERE BuyerBusinessID = :bid"),
               {"bid": buyer_business_id})
    db.commit()
    return {"message": "Unsubscribed."}


def _build_digest_items(db: Session, buyer_business_id: int, saved_farms_only: bool):
    """Items available within the next 7 days, optionally restricted to the buyer's saved farms."""
    saved_filter = ""
    if saved_farms_only:
        saved_filter = "AND b.BusinessID IN (SELECT FarmBusinessID FROM RestaurantSavedFarms WHERE BuyerBusinessID = :bid)"

    items = []
    # Produce
    rows = db.execute(text(f"""
        SELECT TOP 50
            'produce' AS ProductType, p.ProduceID AS SourceID,
            i.IngredientName AS Title, p.Quantity, p.RetailPrice, p.WholesalePrice,
            p.AvailableDate, b.BusinessID AS FarmID, b.BusinessName AS FarmName,
            a.AddressCity, a.AddressState
        FROM Produce p
        JOIN Ingredients i ON p.IngredientID = i.IngredientID
        JOIN Business b    ON p.BusinessID   = b.BusinessID
        LEFT JOIN Address a ON b.AddressID = a.AddressID
        WHERE p.Quantity > 0
          AND (p.ExpirationDate IS NULL OR p.ExpirationDate >= CAST(GETDATE() AS DATE))
          AND (p.AvailableDate IS NULL OR p.AvailableDate <= DATEADD(DAY, 7, GETDATE()))
          {saved_filter}
        ORDER BY p.AvailableDate ASC
    """), {"bid": buyer_business_id}).fetchall()
    items += [dict(r._mapping) for r in rows]

    # Meat
    rows = db.execute(text(f"""
        SELECT TOP 50
            'meat' AS ProductType, m.MeatInventoryID AS SourceID,
            CONCAT(i.IngredientName, ' - ', ic.IngredientCut) AS Title,
            m.Quantity, m.RetailPrice, m.WholesalePrice,
            m.AvailableDate, b.BusinessID AS FarmID, b.BusinessName AS FarmName,
            a.AddressCity, a.AddressState
        FROM MeatInventory m
        JOIN Ingredients i      ON m.IngredientID = i.IngredientID
        LEFT JOIN Cut ic        ON m.IngredientCutID = ic.IngredientCutID
        JOIN Business b         ON m.BusinessID = b.BusinessID
        LEFT JOIN Address a     ON b.AddressID  = a.AddressID
        WHERE m.ShowMeat = 1 AND m.Quantity > 0
          AND (m.AvailableDate IS NULL OR m.AvailableDate <= DATEADD(DAY, 7, GETDATE()))
          {saved_filter}
        ORDER BY m.AvailableDate ASC
    """), {"bid": buyer_business_id}).fetchall()
    items += [dict(r._mapping) for r in rows]

    return items


@marketplace_router.get("/digest-preview")
def preview_digest(buyer_business_id: int, db: Session = Depends(get_db)):
    """Returns the items that would appear in the next digest — for the opt-in UI preview."""
    sub = db.execute(text("""
        SELECT SavedFarmsOnly FROM RestaurantDigestSubscriptions WHERE BuyerBusinessID = :bid
    """), {"bid": buyer_business_id}).fetchone()
    saved_only = bool(sub and sub._mapping.get('SavedFarmsOnly'))
    items = _build_digest_items(db, buyer_business_id, saved_only)
    return {"saved_farms_only": saved_only, "item_count": len(items), "items": items[:30]}


def _render_digest_html(items, base_url):
    if not items:
        body_rows = '<tr><td style="padding:16px;color:#6b7280;font-style:italic;">Nothing fresh from your selected farms this week — check back soon.</td></tr>'
    else:
        rows = []
        for it in items[:50]:
            title    = it.get('Title') or ''
            farm     = it.get('FarmName') or ''
            city     = it.get('AddressCity') or ''
            state    = it.get('AddressState') or ''
            location = f"{city}, {state}" if city else ''
            retail   = it.get('RetailPrice')
            wholesale = it.get('WholesalePrice')
            qty      = it.get('Quantity')
            price_html = ''
            if wholesale:
                price_html += f'<span style="color:#8a6a0a;font-weight:bold;">WS ${float(wholesale):.2f}</span> &middot; '
            if retail is not None:
                price_html += f'<span style="color:#3D6B34;font-weight:bold;">${float(retail):.2f}</span>'
            qty_html = f'<span style="color:#6b7280;font-size:12px;">&nbsp;({qty} avail)</span>' if qty else ''
            rows.append(f"""
              <tr>
                <td style="padding:12px 16px;border-bottom:1px solid #f3f4f6;">
                  <div style="font-weight:600;color:#111827;">{title}</div>
                  <div style="font-size:12px;color:#6b7280;">{farm}{(' &middot; ' + location) if location else ''}</div>
                </td>
                <td style="padding:12px 16px;border-bottom:1px solid #f3f4f6;text-align:right;white-space:nowrap;">{price_html}{qty_html}</td>
              </tr>""")
        body_rows = ''.join(rows)
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f7f2e8;">
      <div style="background:#fff;border-radius:14px;border:1px solid #e5e7eb;overflow:hidden;">
        <div style="padding:20px 24px;background:#3D6B34;color:#fff;">
          <h1 style="margin:0;font-size:20px;">📬 Available This Week</h1>
          <p style="margin:4px 0 0;font-size:13px;opacity:0.9;">Fresh from local farms — perfect for menu planning.</p>
        </div>
        <table style="width:100%;border-collapse:collapse;">
          {body_rows}
        </table>
        <div style="padding:18px 24px;text-align:center;">
          <a href="{base_url}/marketplaces/farm-to-table" style="display:inline-block;padding:10px 20px;background:#3D6B34;color:#fff;text-decoration:none;border-radius:8px;font-weight:bold;font-size:14px;">Browse the marketplace →</a>
        </div>
        <p style="padding:0 24px 18px;color:#9ca3af;font-size:11px;text-align:center;">
          Manage this digest at <a href="{base_url}/restaurant/digest" style="color:#3D6B34;">{base_url.replace('https://','').replace('http://','')}/restaurant/digest</a>
        </p>
      </div>
    </div>
    """


def _send_email(to_email, subject, html):
    api_key   = os.getenv("SENDGRID_API_KEY", "")
    from_addr = os.getenv("FROM_EMAIL", "john@oatmeal-ai.com")
    from_name = os.getenv("FROM_NAME", "Oatmeal Farm Network")
    if not api_key:
        return False, "SENDGRID_API_KEY not configured"
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        msg = Mail(
            from_email=Email(from_addr, from_name),
            to_emails=To(to_email),
            subject=subject,
            html_content=Content("text/html", html),
        )
        resp = sg.send(msg)
        return resp.status_code < 300, f"sendgrid status {resp.status_code}"
    except Exception as e:
        return False, f"sendgrid error: {e}"


@marketplace_router.post("/digest-send-now")
def send_digest_now(buyer_business_id: int, db: Session = Depends(get_db)):
    """Build and send the weekly digest immediately via SendGrid."""
    sub = db.execute(text("""
        SELECT BuyerBusinessID, Email, SavedFarmsOnly FROM RestaurantDigestSubscriptions
        WHERE BuyerBusinessID = :bid AND Status = 'active'
    """), {"bid": buyer_business_id}).fetchone()
    if not sub:
        raise HTTPException(404, "No active digest subscription for this business.")

    to_email   = sub._mapping.get('Email')
    saved_only = bool(sub._mapping.get('SavedFarmsOnly'))
    items      = _build_digest_items(db, buyer_business_id, saved_only)
    subject    = f"OFN Marketplace — {len(items)} items available this week"
    base_url   = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
    html       = _render_digest_html(items, base_url)

    sent, note = _send_email(to_email, subject, html)
    if sent:
        db.execute(text("""
            UPDATE RestaurantDigestSubscriptions SET LastSentAt = GETDATE(), UpdatedAt = GETDATE()
            WHERE BuyerBusinessID = :bid
        """), {"bid": buyer_business_id})
        db.commit()

    return {
        "to": to_email,
        "subject": subject,
        "item_count": len(items),
        "sent": sent,
        "note": note,
    }


# ─────────────────────────────────────────────
# CRON RUNNER — fires every active digest that's due
# Designed for an external scheduler (Windows Task Scheduler, GitHub Action, etc.)
# to POST hourly. Idempotent: only sends to subs whose LastSentAt + interval has passed.
# ─────────────────────────────────────────────

_DIGEST_INTERVAL_DAYS = {'weekly': 7, 'biweekly': 14, 'monthly': 30}


@marketplace_router.post("/digest-run-due")
def run_due_digests(db: Session = Depends(get_db)):
    """Send the digest to every active subscription that is due. Records a CronJobRuns row."""
    from datetime import datetime, timedelta

    run = db.execute(text("""
        INSERT INTO CronJobRuns (JobName, Status) OUTPUT INSERTED.CronRunID
        VALUES ('digest-run-due', 'running')
    """)).fetchone()
    cron_run_id = run[0]
    db.commit()

    subs = db.execute(text("""
        SELECT BuyerBusinessID, Email, Frequency, SavedFarmsOnly, LastSentAt
        FROM RestaurantDigestSubscriptions
        WHERE Status = 'active'
    """)).fetchall()

    base_url = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
    now      = datetime.utcnow()
    processed = succeeded = failed = 0
    notes_lines = []

    for s in subs:
        m = s._mapping
        interval = _DIGEST_INTERVAL_DAYS.get(m.get('Frequency') or 'weekly', 7)
        last     = m.get('LastSentAt')
        if last and (now - last) < timedelta(days=interval):
            continue  # not due yet
        processed += 1
        bid = m.get('BuyerBusinessID')
        try:
            items   = _build_digest_items(db, bid, bool(m.get('SavedFarmsOnly')))
            subject = f"OFN Marketplace — {len(items)} items available this week"
            html    = _render_digest_html(items, base_url)
            sent, note = _send_email(m.get('Email'), subject, html)
            if sent:
                succeeded += 1
                db.execute(text("""
                    UPDATE RestaurantDigestSubscriptions SET LastSentAt = GETDATE(), UpdatedAt = GETDATE()
                    WHERE BuyerBusinessID = :bid
                """), {"bid": bid})
                db.commit()
            else:
                failed += 1
                notes_lines.append(f"buyer {bid}: {note}")
        except Exception as e:
            failed += 1
            notes_lines.append(f"buyer {bid}: exception {e}")

    status = 'success' if failed == 0 else ('error' if succeeded == 0 else 'partial')
    db.execute(text("""
        UPDATE CronJobRuns
        SET CompletedAt = GETDATE(), Status = :st,
            ItemsProcessed = :p, ItemsSucceeded = :s, ItemsFailed = :f,
            Notes = :n
        WHERE CronRunID = :id
    """), {
        "id": cron_run_id, "st": status,
        "p": processed, "s": succeeded, "f": failed,
        "n": '\n'.join(notes_lines)[:4000] if notes_lines else None,
    })
    db.commit()

    return {
        "cron_run_id": cron_run_id,
        "status": status,
        "subscriptions_checked": len(subs),
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
    }


@marketplace_router.get("/cron-runs")
def list_cron_runs(limit: int = 100, job_name: Optional[str] = None, db: Session = Depends(get_db)):
    """Recent cron-job runs, newest first. Used by the admin tracking page."""
    where = ""
    params = {"lim": limit}
    if job_name:
        where = "WHERE JobName = :jn"
        params["jn"] = job_name
    rows = db.execute(text(f"""
        SELECT TOP (:lim) CronRunID, JobName, StartedAt, CompletedAt, Status,
               ItemsProcessed, ItemsSucceeded, ItemsFailed, Notes
        FROM CronJobRuns
        {where}
        ORDER BY StartedAt DESC
    """), params).fetchall()
    return [dict(r._mapping) for r in rows]