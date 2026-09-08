from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import get_db

router = APIRouter(prefix="/api/produce", tags=["produce"])

_produce_cols_ready = False

def _ensure_produce_cols(db: Session):
    global _produce_cols_ready
    if _produce_cols_ready:
        return
    try:
        db.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='Produce' AND COLUMN_NAME='LowStockThreshold')
                ALTER TABLE Produce ADD LowStockThreshold DECIMAL(10,2) NULL
        """))
        db.commit()
    except Exception:
        db.rollback()
    _produce_cols_ready = True


@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    try:
        rows = db.execute(text(
            "SELECT IngredientCategoryID, IngredientCategory FROM IngredientCategoryLookup ORDER BY IngredientCategory"
        )).fetchall()
        return [{"IngredientCategoryID": r.IngredientCategoryID, "IngredientCategory": r.IngredientCategory} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingredients")
def get_ingredients(IngredientCategoryID: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text(
            "SELECT IngredientID, IngredientName FROM Ingredients WHERE IngredientCategoryID = :cid ORDER BY IngredientName"
        ), {"cid": IngredientCategoryID}).fetchall()
        return [{"IngredientID": r.IngredientID, "IngredientName": r.IngredientName} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/measurements")
def get_measurements(db: Session = Depends(get_db)):
    try:
        rows = db.execute(text(
            "SELECT MeasurementID, Measurement, MeasurementAbbreviation FROM MeasurementLookup ORDER BY MeasurementOrder"
        )).fetchall()
        return [{"MeasurementID": r.MeasurementID, "Measurement": r.Measurement, "MeasurementAbbreviation": r.MeasurementAbbreviation} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/inventory")
def get_inventory(BusinessID: int, db: Session = Depends(get_db)):
    try:
        _ensure_produce_cols(db)
        rows = db.execute(text("""
            SELECT p.ProduceID, p.IngredientID, p.Quantity, p.MeasurementID,
                   p.WholesalePrice, p.RetailPrice, p.AvailableDate, p.ShowProduce,
                   p.HarvestDate, p.ExpirationDate, p.LowStockThreshold,
                   i.IngredientName,
                   m.Measurement, m.MeasurementAbbreviation
            FROM Produce p
            JOIN Ingredients i ON p.IngredientID = i.IngredientID
            JOIN MeasurementLookup m ON p.MeasurementID = m.MeasurementID
            WHERE p.BusinessID = :bid
            ORDER BY p.ExpirationDate ASC, i.IngredientName
        """), {"bid": BusinessID}).fetchall()
        return [
            {
                "ProduceID":               r.ProduceID,
                "IngredientID":            r.IngredientID,
                "IngredientName":          r.IngredientName,
                "Quantity":                float(r.Quantity) if r.Quantity is not None else None,
                "MeasurementID":           r.MeasurementID,
                "Measurement":             r.Measurement,
                "MeasurementAbbreviation": r.MeasurementAbbreviation,
                "WholesalePrice":          float(r.WholesalePrice) if r.WholesalePrice is not None else None,
                "RetailPrice":             float(r.RetailPrice) if r.RetailPrice is not None else None,
                "AvailableDate":           str(r.AvailableDate) if r.AvailableDate else None,
                "HarvestDate":             str(r.HarvestDate) if r.HarvestDate else None,
                "ExpirationDate":          str(r.ExpirationDate) if r.ExpirationDate else None,
                "LowStockThreshold":       float(r.LowStockThreshold) if r.LowStockThreshold is not None else None,
                "ShowProduce":             r.ShowProduce,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts")
def get_produce_alerts(BusinessID: int, db: Session = Depends(get_db)):
    """Return expiring (≤14 days or already expired) and low-stock produce items."""
    try:
        _ensure_produce_cols(db)
        rows = db.execute(text("""
            SELECT p.ProduceID, p.Quantity, p.LowStockThreshold,
                   p.ExpirationDate, p.HarvestDate,
                   i.IngredientName,
                   m.MeasurementAbbreviation,
                   DATEDIFF(day, CAST(GETDATE() AS DATE), p.ExpirationDate) AS DaysUntilExpiry
            FROM Produce p
            JOIN Ingredients i ON p.IngredientID = i.IngredientID
            JOIN MeasurementLookup m ON p.MeasurementID = m.MeasurementID
            WHERE p.BusinessID = :bid
              AND (
                (p.ExpirationDate IS NOT NULL AND p.ExpirationDate <= DATEADD(day, 14, CAST(GETDATE() AS DATE)))
                OR (p.LowStockThreshold IS NOT NULL AND p.Quantity IS NOT NULL AND p.Quantity <= p.LowStockThreshold)
              )
            ORDER BY p.ExpirationDate ASC
        """), {"bid": BusinessID}).fetchall()
        alerts = []
        for r in rows:
            days = r.DaysUntilExpiry
            qty = float(r.Quantity) if r.Quantity is not None else None
            thresh = float(r.LowStockThreshold) if r.LowStockThreshold is not None else None
            alert_types = []
            if r.ExpirationDate and days is not None and days <= 14:
                alert_types.append("expiring_soon" if days >= 0 else "expired")
            if thresh is not None and qty is not None and qty <= thresh:
                alert_types.append("low_stock")
            for atype in alert_types:
                alerts.append({
                    "ProduceID":        r.ProduceID,
                    "IngredientName":   r.IngredientName,
                    "alert_type":       atype,
                    "Quantity":         qty,
                    "LowStockThreshold": thresh,
                    "ExpirationDate":   str(r.ExpirationDate) if r.ExpirationDate else None,
                    "DaysUntilExpiry":  days,
                    "Unit":             r.MeasurementAbbreviation,
                })
        return alerts
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add")
def add_produce(payload: dict, db: Session = Depends(get_db)):
    try:
        _ensure_produce_cols(db)
        db.execute(text("""
            INSERT INTO Produce (IngredientID, Quantity, MeasurementID, WholesalePrice, RetailPrice,
                                 BusinessID, AvailableDate, HarvestDate, ExpirationDate, LowStockThreshold)
            VALUES (:ingredient, :qty, :meas, :wholesale, :retail,
                    :bid, :avail, :harvest, :expiry, :thresh)
        """), {
            "ingredient": payload.get("IngredientID") or None,
            "qty":        payload.get("Quantity") or None,
            "meas":       payload.get("MeasurementID") or None,
            "wholesale":  payload.get("WholesalePrice") or None,
            "retail":     payload.get("RetailPrice") or None,
            "bid":        payload.get("BusinessID"),
            "avail":      payload.get("AvailableDate") or None,
            "harvest":    payload.get("HarvestDate") or None,
            "expiry":     payload.get("ExpirationDate") or None,
            "thresh":     payload.get("LowStockThreshold") or None,
        })
        db.commit()
        return {"message": "Produce added successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/{produce_id}")
def update_produce(produce_id: int, payload: dict, BusinessID: int, db: Session = Depends(get_db)):
    try:
        _ensure_produce_cols(db)
        db.execute(text("""
            UPDATE Produce SET
                Quantity          = :qty,
                MeasurementID     = :meas,
                RetailPrice       = :retail,
                WholesalePrice    = :wholesale,
                AvailableDate     = :avail,
                HarvestDate       = :harvest,
                ExpirationDate    = :expiry,
                LowStockThreshold = :thresh,
                ShowProduce       = :show,
                IngredientID      = :ingredient
            WHERE ProduceID = :pid AND BusinessID = :bid
        """), {
            "qty":        payload.get("Quantity") or None,
            "meas":       payload.get("MeasurementID") or None,
            "retail":     payload.get("RetailPrice") or None,
            "wholesale":  payload.get("WholesalePrice") or None,
            "avail":      payload.get("AvailableDate") or None,
            "harvest":    payload.get("HarvestDate") or None,
            "expiry":     payload.get("ExpirationDate") or None,
            "thresh":     payload.get("LowStockThreshold") or None,
            "show":       payload.get("ShowProduce", 0),
            "ingredient": payload.get("IngredientID") or None,
            "pid":        produce_id,
            "bid":        BusinessID,
        })
        db.commit()
        return {"message": "Produce updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/{produce_id}")
def delete_produce(produce_id: int, BusinessID: int, db: Session = Depends(get_db)):
    try:
        db.execute(text(
            "DELETE FROM Produce WHERE ProduceID = :pid AND BusinessID = :bid"
        ), {"pid": produce_id, "bid": BusinessID})
        db.commit()
        return {"message": "Produce deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
