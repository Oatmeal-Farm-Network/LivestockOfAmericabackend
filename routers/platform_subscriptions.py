# routers/platform_subscriptions.py
# OFN platform subscriptions — Stripe Checkout for per-Business monthly billing.
# Mount: app.include_router(platform_subscriptions_router)
#
# Plans live in SubscriptionLevels (StripeAPIID = live price, StripeAPIIDTest =
# test price). Per-business state is stored on Business:
#   StripeCustomerID, StripeSubscriptionID, SubscriptionLevel (FK to
#   SubscriptionLevels.SubscriptionID), SubscriptionStatus,
#   SubscriptionstartDate, SubscriptionEndDate, SubscriptionTier.
#
# Lifecycle:
#   /checkout → Stripe Checkout Session → buyer completes payment →
#   checkout.session.completed webhook writes StripeCustomerID +
#   StripeSubscriptionID → customer.subscription.updated keeps status in sync.

import os
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from jwt_auth import get_current_user
from routers.platform_settings import get_stripe_config


platform_subscriptions_router = APIRouter(prefix="/api/platform-subscriptions", tags=["platform-subscriptions"])

OFN_BASE_URL = os.getenv("OFN_BASE_URL", "https://oatmealfarmnetwork.com")
# Where Stripe returns the member after package checkout. Separate from
# OFN_BASE_URL because this backend serves Livestock of America.
SITE_BASE_URL = os.getenv("FRONTEND_URL", "https://livestockofamerica.com").rstrip("/")


def _stripe(db: Session):
    cfg = get_stripe_config(db)
    if not cfg.get("StripeSecretKey"):
        raise HTTPException(503, "Stripe not configured. Ask an admin to add keys in Accounting → Payments.")
    import stripe
    stripe.api_key = cfg["StripeSecretKey"]
    return stripe, cfg


def _mode_from_cfg(cfg: dict) -> str:
    # OFNPlatformSettings stores StripeTestMode as a bit; treat missing as test.
    raw = cfg.get("StripeTestMode")
    is_test = True if raw is None else bool(raw)
    return "test" if is_test else "live"


def _require_business_access(db: Session, people_id: str, business_id: int):
    row = db.execute(
        text("SELECT 1 FROM BusinessAccess WHERE PeopleID = :pid AND BusinessID = :bid AND Active = 1"),
        {"pid": int(people_id), "bid": business_id},
    ).fetchone()
    if not row:
        raise HTTPException(403, "You do not have access to this business.")


def _price_id_for(level: dict, mode: str) -> Optional[str]:
    # mode is 'live' or 'test' from OFNPlatformSettings.StripeMode.
    return level.get("StripeAPIID") if mode == "live" else level.get("StripeAPIIDTest")


# ─────────────────────────────────────────────────────────────────────────────
# READS
# ─────────────────────────────────────────────────────────────────────────────

@platform_subscriptions_router.get("/plans")
def list_plans(country_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Return active subscription plans. Filters by country_id when provided,
    otherwise returns all. Only plans with a Stripe price ID for the current
    mode are returned as selectable; the rest are returned with
    selectable=False so the UI can show them as coming-soon."""
    _, cfg = _stripe(db)
    mode = _mode_from_cfg(cfg)

    sql = "SELECT * FROM SubscriptionLevels WHERE 1=1"
    params = {}
    if country_id is not None:
        sql += " AND country_id = :cid"
        params["cid"] = country_id
    sql += " ORDER BY SubscriptionMonthlyRate, SubscriptionID"

    rows = db.execute(text(sql), params).mappings().fetchall()
    plans = []
    for r in rows:
        level = dict(r)
        for k in ("SubscriptionMonthlyRate",):
            if level.get(k) is not None:
                level[k] = float(level[k])
        price_id = _price_id_for(level, mode)
        level["selectable"] = bool(price_id)
        level["price_id"] = price_id
        plans.append(level)
    return {"mode": mode, "plans": plans}


@platform_subscriptions_router.get("/current/{business_id}")
def current_subscription(
    business_id: int,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    _require_business_access(db, people_id, business_id)
    row = db.execute(
        text("""
            SELECT b.BusinessID, b.BusinessName, b.SubscriptionLevel,
                   b.SubscriptionStatus, b.SubscriptionstartDate, b.SubscriptionEndDate,
                   b.SubscriptionTier, b.StripeCustomerID, b.StripeSubscriptionID,
                   sl.SubscriptionTitle, sl.SubscriptionMonthlyRate
            FROM Business b
            LEFT JOIN SubscriptionLevels sl ON b.SubscriptionLevel = sl.SubscriptionID
            WHERE b.BusinessID = :bid
        """),
        {"bid": business_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Business not found")
    data = dict(row._mapping)
    if data.get("SubscriptionMonthlyRate") is not None:
        data["SubscriptionMonthlyRate"] = float(data["SubscriptionMonthlyRate"])
    return data


# ─────────────────────────────────────────────────────────────────────────────
# WRITES
# ─────────────────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    subscription_id: int  # SubscriptionLevels.SubscriptionID


@platform_subscriptions_router.post("/checkout/{business_id}")
def start_checkout(
    business_id: int,
    req: CheckoutRequest,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    _require_business_access(db, people_id, business_id)

    stripe, cfg = _stripe(db)
    mode = _mode_from_cfg(cfg)

    level_row = db.execute(
        text("SELECT * FROM SubscriptionLevels WHERE SubscriptionID = :id"),
        {"id": req.subscription_id},
    ).mappings().fetchone()
    if not level_row:
        raise HTTPException(404, "Plan not found")
    level = dict(level_row)
    price_id = _price_id_for(level, mode)
    if not price_id:
        raise HTTPException(
            400,
            f"Plan '{level.get('SubscriptionTitle')}' is not yet available for {mode} payments. "
            "Contact an admin to add a Stripe price.",
        )

    business = db.execute(
        text("SELECT BusinessID, BusinessName, BusinessEmail, StripeCustomerID FROM Business WHERE BusinessID = :bid"),
        {"bid": business_id},
    ).mappings().fetchone()
    if not business:
        raise HTTPException(404, "Business not found")

    customer_id = business.get("StripeCustomerID")
    if not customer_id:
        customer = stripe.Customer.create(
            email=business.get("BusinessEmail") or None,
            name=business.get("BusinessName") or None,
            metadata={"business_id": str(business_id)},
        )
        customer_id = customer.id
        db.execute(
            text("UPDATE Business SET StripeCustomerID = :cid WHERE BusinessID = :bid"),
            {"cid": customer_id, "bid": business_id},
        )
        db.commit()

    success_url = f"{OFN_BASE_URL}/account/subscription?BusinessID={business_id}&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{OFN_BASE_URL}/account/subscription?BusinessID={business_id}&cancelled=1"

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        metadata={
            "business_id": str(business_id),
            "subscription_level_id": str(req.subscription_id),
        },
        subscription_data={
            "metadata": {
                "business_id": str(business_id),
                "subscription_level_id": str(req.subscription_id),
            },
        },
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return {"checkout_url": session.url, "session_id": session.id}


# ─────────────────────────────────────────────────────────────────────────────
# SUBSCRIPTION PACKAGES — canonical plan list managed via the oatmeal_main
# admin UI (http://localhost:8080/app/admin/subscriptions). Writes go to the
# shared SubscriptionPackage table.
# ─────────────────────────────────────────────────────────────────────────────

@platform_subscriptions_router.get("/packages")
def list_packages(
    business_type_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Active subscription packages from the SubscriptionPackage table.
    Optionally filter to packages matching a specific BusinessTypeID; packages
    with a NULL BusinessTypeID are always included (they apply to all types).
    Returns the current payment mode so the frontend can gate the pay step."""
    cfg = get_stripe_config(db)
    mode = _mode_from_cfg(cfg)

    sql = """
        SELECT p.PackageID, p.PackageName, p.Description, p.BusinessTypeID,
               p.MonthlyPrice, p.YearlyPrice, p.SortOrder,
               p.MaxForSaleListings, p.MaxStudListings, p.MaxDirectoryListings,
               bt.BusinessType
        FROM SubscriptionPackage p
        LEFT JOIN BusinessTypeLookup bt ON p.BusinessTypeID = bt.BusinessTypeID
        WHERE p.IsActive = 1
    """
    params = {}
    if business_type_id is not None:
        sql += " AND (p.BusinessTypeID = :btid OR p.BusinessTypeID IS NULL)"
        params["btid"] = business_type_id
    sql += " ORDER BY p.SortOrder, p.PackageName"

    rows = db.execute(text(sql), params).mappings().fetchall()
    packages = []
    for r in rows:
        pkg = dict(r)
        for k in ("MonthlyPrice", "YearlyPrice"):
            if pkg.get(k) is not None:
                pkg[k] = float(pkg[k])
        packages.append(pkg)
    return {"mode": mode, "packages": packages}


class AssignPackageRequest(BaseModel):
    package_id: int
    billing_cycle: Optional[str] = "monthly"  # "monthly" | "yearly"


@platform_subscriptions_router.post("/assign-package/{business_id}")
def assign_package(
    business_id: int,
    req: AssignPackageRequest,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    """Assign a SubscriptionPackage to a Business without Stripe — mirrors the
    oatmeal_main admin endpoint. Only permitted in test mode so a real
    production signup can't silently skip payment; when the admin flips
    StripeTestMode off, this route refuses and the UI must run a live flow."""
    _require_business_access(db, people_id, business_id)

    pkg = db.execute(
        text("""
            SELECT PackageID, PackageName, MonthlyPrice, YearlyPrice
            FROM SubscriptionPackage
            WHERE PackageID = :pid AND IsActive = 1
        """),
        {"pid": req.package_id},
    ).mappings().fetchone()
    if not pkg:
        raise HTTPException(404, "Package not found or inactive.")

    # A package priced at zero on both cycles costs nothing to grant, so it is
    # assigned directly in any mode -- there is no payment to skip. Anything with
    # a price still requires test mode here; the live path is /package-checkout,
    # which hands the member to Stripe.
    is_free = not float(pkg["MonthlyPrice"] or 0) and not float(pkg["YearlyPrice"] or 0)
    cfg = get_stripe_config(db)
    if not is_free and _mode_from_cfg(cfg) != "test":
        raise HTTPException(400, "Test mode is not enabled. A live payment flow is required.")

    import datetime
    days = 365 if (req.billing_cycle or "").lower() == "yearly" else 30
    start_dt = datetime.datetime.utcnow()
    end_dt = start_dt + datetime.timedelta(days=days)

    db.execute(
        text("""
            UPDATE Business
            SET SubscriptionTier = :tier,
                SubscriptionStatus = 'active',
                SubscriptionstartDate = :start,
                SubscriptionEndDate = :eod
            WHERE BusinessID = :bid
        """),
        {"tier": pkg["PackageName"], "start": start_dt, "eod": end_dt, "bid": business_id},
    )
    db.commit()
    return {
        "ok": True,
        "free": is_free,
        "test_mode": _mode_from_cfg(cfg) == "test",
        "package_id": req.package_id,
        "package_name": pkg["PackageName"],
        "billing_cycle": (req.billing_cycle or "monthly").lower(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# PACKAGE CHECKOUT — Stripe Checkout for a paid SubscriptionPackage, used by the
# payment step of account setup. The SubscriptionLevels equivalent is
# /checkout above; this one bills a package and records it by name on
# Business.SubscriptionTier, which is what the plan-limit checks read.
# ─────────────────────────────────────────────────────────────────────────────

# Which column holds the Stripe price, per billing cycle and per mode.
_PACKAGE_PRICE_COLUMNS = {
    ("monthly", "live"): "StripePriceIDMonthly",
    ("monthly", "test"): "StripePriceIDMonthlyTest",
    ("yearly",  "live"): "StripePriceIDYearly",
    ("yearly",  "test"): "StripePriceIDYearlyTest",
}


_PACKAGE_PRODUCT_COLUMNS = {"live": "StripeProductID", "test": "StripeProductIDTest"}

_CYCLE_INTERVAL = {"monthly": "month", "yearly": "year"}


def _ensure_package_price(db: Session, stripe, pkg: dict, cycle: str, mode: str,
                          currency: str) -> str:
    """Return a Stripe Price ID for this package/cycle, creating it if needed.

    Packages are authored in the admin UI, not in Stripe, so requiring someone to
    mirror each one by hand in the dashboard is an easy step to forget -- and
    forgetting it only shows up when a member tries to pay. Instead the Product
    and Price are created the first time a package is actually sold, then cached
    on the package row.

    Three things make this safe to call on every checkout:

    * An admin-entered price ID always wins, so pasting an existing Stripe price
      in the admin form still overrides everything here.
    * Stripe Prices are immutable. If the admin later edits MonthlyPrice, the
      cached ID no longer matches the amount, so a new Price is created rather
      than billing the old figure. Existing subscribers stay on the price they
      signed up at, which is how Stripe subscriptions already behave.
    * Lookup keys are deterministic (package, cycle, amount), so a concurrent
      request or a cleared column re-finds the existing Price instead of piling
      up duplicates in the dashboard.
    """
    price_col = _PACKAGE_PRICE_COLUMNS[(cycle, mode)]
    amount = float(pkg.get("MonthlyPrice") or 0) if cycle == "monthly" else float(pkg.get("YearlyPrice") or 0)
    unit_amount = int(round(amount * 100))

    existing = pkg.get(price_col)
    if existing:
        # Trust it only while it still charges what the package advertises.
        try:
            price = stripe.Price.retrieve(existing)
            if price.unit_amount == unit_amount and price.active:
                return existing
        except Exception:
            pass  # deleted, from another Stripe account, or malformed — re-provision

    lookup_key = f"loa_pkg_{pkg['PackageID']}_{cycle}_{unit_amount}_{currency.lower()}"

    # Someone (a previous run, a parallel request) may already have made it.
    found = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    if found.data:
        price_id = found.data[0].id
    else:
        product_col = _PACKAGE_PRODUCT_COLUMNS[mode]
        product_id = pkg.get(product_col)
        if product_id:
            try:
                stripe.Product.retrieve(product_id)
            except Exception:
                product_id = None
        if not product_id:
            product = stripe.Product.create(
                name=pkg["PackageName"],
                metadata={"package_id": str(pkg["PackageID"])},
            )
            product_id = product.id
            db.execute(
                text(f"UPDATE SubscriptionPackage SET {product_col} = :v WHERE PackageID = :pid"),
                {"v": product_id, "pid": pkg["PackageID"]},
            )
            db.commit()

        price_id = stripe.Price.create(
            product=product_id,
            currency=currency.lower(),
            unit_amount=unit_amount,
            recurring={"interval": _CYCLE_INTERVAL[cycle]},
            lookup_key=lookup_key,
            metadata={"package_id": str(pkg["PackageID"]), "billing_cycle": cycle},
        ).id

    db.execute(
        text(f"UPDATE SubscriptionPackage SET {price_col} = :v WHERE PackageID = :pid"),
        {"v": price_id, "pid": pkg["PackageID"]},
    )
    db.commit()
    return price_id


class PackageCheckoutRequest(BaseModel):
    package_id: int
    billing_cycle: Optional[str] = "monthly"     # "monthly" | "yearly"
    return_url: Optional[str] = None             # where Stripe sends the member back


@platform_subscriptions_router.post("/package-checkout/{business_id}")
def start_package_checkout(
    business_id: int,
    req: PackageCheckoutRequest,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    """Create a Stripe Checkout Session for a paid package.

    Free packages never reach here -- the caller assigns those directly via
    /assign-package, since there is nothing to charge.
    """
    _require_business_access(db, people_id, business_id)

    cycle = (req.billing_cycle or "monthly").lower()
    if cycle not in ("monthly", "yearly"):
        raise HTTPException(400, "billing_cycle must be 'monthly' or 'yearly'.")

    # Validate the request before demanding Stripe credentials, so a misconfigured
    # key does not mask the more useful "this plan is free" / "no price set"
    # messages. Reading the mode only needs the settings row, not the secret.
    cfg = get_stripe_config(db)
    mode = _mode_from_cfg(cfg)

    pkg = db.execute(
        text("SELECT * FROM SubscriptionPackage WHERE PackageID = :pid AND IsActive = 1"),
        {"pid": req.package_id},
    ).mappings().fetchone()
    if not pkg:
        raise HTTPException(404, "Package not found or inactive.")
    pkg = dict(pkg)

    amount = float(pkg.get("MonthlyPrice") or 0) if cycle == "monthly" else float(pkg.get("YearlyPrice") or 0)
    if amount <= 0:
        raise HTTPException(
            400,
            f"'{pkg['PackageName']}' is free on the {cycle} cycle and does not need payment.",
        )

    # Everything about the request checks out — now the call really does need Stripe.
    stripe, _ = _stripe(db)

    # Created on first sale rather than pre-built by hand in the Stripe dashboard.
    try:
        price_id = _ensure_package_price(
            db, stripe, pkg, cycle, mode, cfg.get("CurrencyCode") or "USD")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            502,
            f"Could not set up '{pkg['PackageName']}' in Stripe: {e}. "
            "Check the Stripe key, or paste a price ID on the Subscriptions admin page.",
        )

    business = db.execute(
        text("SELECT BusinessID, BusinessName, BusinessEmail, StripeCustomerID "
             "FROM Business WHERE BusinessID = :bid"),
        {"bid": business_id},
    ).mappings().fetchone()
    if not business:
        raise HTTPException(404, "Business not found")

    customer_id = business.get("StripeCustomerID")
    if not customer_id:
        customer = stripe.Customer.create(
            email=business.get("BusinessEmail") or None,
            name=business.get("BusinessName") or None,
            metadata={"business_id": str(business_id)},
        )
        customer_id = customer.id
        db.execute(
            text("UPDATE Business SET StripeCustomerID = :cid WHERE BusinessID = :bid"),
            {"cid": customer_id, "bid": business_id},
        )
        db.commit()

    # Only same-origin return URLs are honoured, so a tampered request cannot
    # turn our Stripe session into a redirect to somewhere else.
    base = req.return_url if (req.return_url or "").startswith(SITE_BASE_URL) else SITE_BASE_URL
    base = base.split("?")[0].rstrip("/")

    # package_id rides on both the session and the subscription so the webhook
    # can identify the plan whichever event arrives first.
    meta = {
        "business_id": str(business_id),
        "package_id": str(pkg["PackageID"]),
        "package_name": pkg["PackageName"],
        "billing_cycle": cycle,
    }
    # ui_mode="embedded_page" keeps the member on Livestock of America: Stripe returns
    # a client secret that mounts the payment form in an iframe on our own page,
    # instead of a URL to redirect away to. Card details still go straight to
    # Stripe and never touch this server, so PCI scope is unchanged.
    # Embedded sessions take return_url in place of success_url/cancel_url.
    session = stripe.checkout.Session.create(
        mode="subscription",
        ui_mode="embedded_page",   # "embedded" was retired by Stripe
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        metadata=meta,
        subscription_data={"metadata": meta},
        return_url=f"{base}?BusinessID={business_id}&session_id={{CHECKOUT_SESSION_ID}}",
    )
    return {
        "client_secret": session.client_secret,
        "session_id": session.id,
        "publishable_key": cfg.get("StripePublishableKey"),
        "mode": mode,
    }


@platform_subscriptions_router.get("/checkout-session/{session_id}")
def package_checkout_status(
    session_id: str,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    """Status of an embedded Checkout Session, read when Stripe returns the
    member to our page.

    This is for showing the right message only. Entitlement still comes from the
    checkout.session.completed webhook -- a return URL is just a browser
    navigation and can be typed by hand, so it must not be what grants a plan.
    """
    stripe, _ = _stripe(db)
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(404, f"Checkout session not found: {e}")

    # StripeObject in stripe-python 15 has neither .get() nor dict() support;
    # to_dict() is the supported way to get a plain mapping.
    meta = session.metadata.to_dict() if session.metadata else {}

    business_id = meta.get("business_id")
    if business_id:
        _require_business_access(db, people_id, int(business_id))

    return {
        "status": session.status,                  # open | complete | expired
        "payment_status": session.payment_status,  # paid | unpaid | no_payment_required
        "business_id": business_id,
        "package_name": meta.get("package_name"),
    }


@platform_subscriptions_router.post("/activate-test/{business_id}")
def activate_test_subscription(
    business_id: int,
    req: CheckoutRequest,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    """Activate a plan directly without Stripe. Only permitted when the admin
    has enabled StripeTestMode; live installs must go through /checkout."""
    _require_business_access(db, people_id, business_id)

    cfg = get_stripe_config(db)
    if _mode_from_cfg(cfg) != "test":
        raise HTTPException(400, "Test mode is not enabled. Use /checkout for live payments.")

    level = db.execute(
        text("SELECT SubscriptionID, SubscriptionTitle FROM SubscriptionLevels WHERE SubscriptionID = :id"),
        {"id": req.subscription_id},
    ).mappings().fetchone()
    if not level:
        raise HTTPException(404, "Plan not found")

    import datetime
    end_dt = datetime.datetime.utcnow() + datetime.timedelta(days=30)
    db.execute(
        text("""
            UPDATE Business
            SET SubscriptionLevel = :lvl,
                SubscriptionStatus = 'active',
                SubscriptionstartDate = COALESCE(SubscriptionstartDate, GETDATE()),
                SubscriptionEndDate = :eod
            WHERE BusinessID = :bid
        """),
        {"lvl": req.subscription_id, "eod": end_dt, "bid": business_id},
    )
    db.commit()
    return {"ok": True, "test_mode": True, "subscription_level_id": req.subscription_id}


@platform_subscriptions_router.post("/portal/{business_id}")
def customer_portal(
    business_id: int,
    db: Session = Depends(get_db),
    people_id: str = Depends(get_current_user),
):
    _require_business_access(db, people_id, business_id)
    stripe, _ = _stripe(db)
    row = db.execute(
        text("SELECT StripeCustomerID FROM Business WHERE BusinessID = :bid"),
        {"bid": business_id},
    ).fetchone()
    if not row or not row[0]:
        raise HTTPException(400, "No Stripe customer yet — start a subscription first.")
    session = stripe.billing_portal.Session.create(
        customer=row[0],
        return_url=f"{OFN_BASE_URL}/account/subscription?BusinessID={business_id}",
    )
    return {"portal_url": session.url}


# ─────────────────────────────────────────────────────────────────────────────
# WEBHOOK
# ─────────────────────────────────────────────────────────────────────────────

@platform_subscriptions_router.post("/webhook")
async def subscription_webhook(request: Request):
    """Handle Stripe subscription lifecycle. Uses its own webhook secret stored
    in OFNPlatformSettings.PlatformSubscriptionWebhookSecret; falls back to
    StripeWebhookSecret if the platform-subscription secret isn't set."""
    with SessionLocal() as db:
        cfg = get_stripe_config(db)
    import stripe
    stripe.api_key = cfg.get("StripeSecretKey") or ""
    secret = cfg.get("PlatformSubscriptionWebhookSecret") or cfg.get("StripeWebhookSecret") or ""

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        if secret:
            # Verify the signature, then read the payload as a plain dict.
            # construct_event returns a stripe Event object, and objects in
            # stripe-python 15 have no .get(), so the handlers below would break
            # the moment a signing secret was configured.
            stripe.Webhook.construct_event(payload, sig, secret)
        event = json.loads(payload)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Invalid webhook signature")

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {}) or {}

    with SessionLocal() as db:
        if event_type == "checkout.session.completed":
            metadata = obj.get("metadata", {}) or {}
            business_id = metadata.get("business_id")
            level_id = metadata.get("subscription_level_id")
            package_name = metadata.get("package_name")
            subscription_id = obj.get("subscription")
            customer_id = obj.get("customer")
            if business_id and subscription_id:
                # A package purchase carries package_name instead of a level id.
                # SubscriptionTier has to be written here because that column is
                # what the plan-limit checks join on -- without it a paying
                # member would keep the previous plan's allowances.
                db.execute(
                    text("""
                        UPDATE Business
                        SET StripeCustomerID = COALESCE(:cid, StripeCustomerID),
                            StripeSubscriptionID = :sid,
                            SubscriptionLevel = COALESCE(:lvl, SubscriptionLevel),
                            SubscriptionTier = COALESCE(:tier, SubscriptionTier),
                            SubscriptionStatus = 'active',
                            SubscriptionstartDate = COALESCE(SubscriptionstartDate, GETDATE())
                        WHERE BusinessID = :bid
                    """),
                    {"cid": customer_id, "sid": subscription_id,
                     "lvl": int(level_id) if level_id else None,
                     "tier": package_name,
                     "bid": int(business_id)},
                )
                db.commit()

        elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
            metadata = obj.get("metadata", {}) or {}
            business_id = metadata.get("business_id")
            subscription_id = obj.get("id")
            status = obj.get("status")
            current_period_end = obj.get("current_period_end")
            if not business_id and subscription_id:
                row = db.execute(
                    text("SELECT BusinessID FROM Business WHERE StripeSubscriptionID = :sid"),
                    {"sid": subscription_id},
                ).fetchone()
                if row:
                    business_id = row[0]
            if business_id:
                import datetime
                end_dt = datetime.datetime.utcfromtimestamp(current_period_end) if current_period_end else None
                db.execute(
                    text("""
                        UPDATE Business
                        SET SubscriptionStatus = :st,
                            SubscriptionEndDate = :eod,
                            StripeSubscriptionID = COALESCE(:sid, StripeSubscriptionID)
                        WHERE BusinessID = :bid
                    """),
                    {"st": status, "eod": end_dt, "sid": subscription_id, "bid": int(business_id)},
                )
                db.commit()

        elif event_type == "customer.subscription.deleted":
            subscription_id = obj.get("id")
            db.execute(
                text("""
                    UPDATE Business
                    SET SubscriptionStatus = 'cancelled'
                    WHERE StripeSubscriptionID = :sid
                """),
                {"sid": subscription_id},
            )
            db.commit()

        elif event_type == "invoice.payment_failed":
            subscription_id = obj.get("subscription")
            if subscription_id:
                db.execute(
                    text("""
                        UPDATE Business SET SubscriptionStatus = 'past_due'
                        WHERE StripeSubscriptionID = :sid AND SubscriptionStatus != 'cancelled'
                    """),
                    {"sid": subscription_id},
                )
                db.commit()

    return {"received": True}
