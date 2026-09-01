"""
Platform-level settings (Stripe keys, refund model, platform fee).

Singleton row in OFNPlatformSettings. Secret values are stored in the database
but never returned in cleartext by the GET endpoint — only masked previews are
exposed. The UPDATE endpoint accepts full cleartext values.

Admin gate: PeopleID must appear in PLATFORM_ADMIN_IDS (comma-separated env var).
Without that env var set, settings management is disabled entirely.
"""
import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db, SessionLocal
from jwt_auth import get_current_user

router = APIRouter()

REFUND_MODELS = {"immediate_charge", "manual_capture"}


def ensure_tables(db: Session):
    db.execute(text("""
        IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'OFNPlatformSettings')
        CREATE TABLE OFNPlatformSettings (
            SettingID              INT IDENTITY(1,1) PRIMARY KEY,
            StripePublishableKey   NVARCHAR(200),
            StripeSecretKey        NVARCHAR(200),
            StripeWebhookSecret    NVARCHAR(200),
            StripeTestMode         BIT DEFAULT 1,
            RefundModel            NVARCHAR(50) DEFAULT 'immediate_charge',
            RefundDeadlineDays     INT DEFAULT 0,
            PlatformFeePercent     DECIMAL(5,2) DEFAULT 0,
            CurrencyCode           NVARCHAR(10) DEFAULT 'USD',
            UpdatedDate            DATETIME DEFAULT GETDATE(),
            UpdatedByPeopleID      INT
        )
    """))
    exists = db.execute(text("SELECT COUNT(*) AS c FROM OFNPlatformSettings")).mappings().first()
    if not exists or exists["c"] == 0:
        db.execute(text("""
            INSERT INTO OFNPlatformSettings (RefundModel, RefundDeadlineDays, PlatformFeePercent, CurrencyCode)
            VALUES ('immediate_charge', 0, 0, 'USD')
        """))
    db.commit()


with SessionLocal() as _db:
    try:
        ensure_tables(_db)
    except Exception as e:
        print(f"Platform settings table setup error: {e}")


def _is_admin(people_id: str) -> bool:
    admins = os.getenv("PLATFORM_ADMIN_IDS", "")
    if not admins:
        return False
    allowed = {p.strip() for p in admins.split(",") if p.strip()}
    return str(people_id) in allowed


def _mask(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "•" * len(secret)
    return secret[:4] + "•••" + secret[-4:]


@router.get("/api/platform/settings")
def get_settings(people_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return masked settings. Any authenticated user can read the non-secret
    operational fields (refund model, currency) because the checkout/registration
    flows need them. Secret keys are masked for everyone except admins."""
    row = db.execute(text("""
        SELECT TOP 1 * FROM OFNPlatformSettings ORDER BY SettingID
    """)).mappings().first()
    if not row:
        raise HTTPException(500, "Settings row missing")
    is_admin = _is_admin(people_id)
    # Through get_stripe_config so an environment-supplied key wins, matching
    # what checkout actually bills with. Reading the row directly here would hand
    # the browser a live key while the server was transacting in test mode.
    cfg = get_stripe_config(db)
    return {
        "StripePublishableKey": cfg.get("StripePublishableKey"),
        "StripeSecretKeyMasked": _mask(row["StripeSecretKey"]),
        "StripeWebhookSecretMasked": _mask(row["StripeWebhookSecret"]),
        "StripeTestMode": bool(row["StripeTestMode"]),
        "RefundModel": row["RefundModel"],
        "RefundDeadlineDays": row["RefundDeadlineDays"],
        "PlatformFeePercent": float(row["PlatformFeePercent"] or 0),
        "CurrencyCode": row["CurrencyCode"],
        "UpdatedDate": row["UpdatedDate"],
        "IsAdmin": is_admin,
        "StripeConfigured": bool(row["StripeSecretKey"] and row["StripePublishableKey"]),
    }


@router.put("/api/platform/settings")
def put_settings(
    payload: dict,
    people_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not _is_admin(people_id):
        raise HTTPException(403, "Platform admin only")

    refund_model = payload.get("RefundModel", "immediate_charge")
    if refund_model not in REFUND_MODELS:
        raise HTTPException(400, f"RefundModel must be one of {REFUND_MODELS}")

    row = db.execute(text("SELECT TOP 1 SettingID, StripeSecretKey, StripeWebhookSecret FROM OFNPlatformSettings ORDER BY SettingID")).mappings().first()
    if not row:
        raise HTTPException(500, "Settings row missing")

    # Allow partial secret updates: if key field is blank or the masked placeholder,
    # keep the existing secret; only overwrite if the client sent a new non-masked value.
    new_sec = payload.get("StripeSecretKey")
    new_whs = payload.get("StripeWebhookSecret")
    if new_sec is None or new_sec == "" or "•" in str(new_sec):
        new_sec = row["StripeSecretKey"]
    if new_whs is None or new_whs == "" or "•" in str(new_whs):
        new_whs = row["StripeWebhookSecret"]

    db.execute(text("""
        UPDATE OFNPlatformSettings SET
            StripePublishableKey = :pk,
            StripeSecretKey      = :sk,
            StripeWebhookSecret  = :wh,
            StripeTestMode       = :tm,
            RefundModel          = :rm,
            RefundDeadlineDays   = :rd,
            PlatformFeePercent   = :pf,
            CurrencyCode         = :cc,
            UpdatedDate          = GETDATE(),
            UpdatedByPeopleID    = :pid
        WHERE SettingID = :id
    """), {
        "id":  row["SettingID"],
        "pk":  payload.get("StripePublishableKey"),
        "sk":  new_sec,
        "wh":  new_whs,
        "tm":  1 if payload.get("StripeTestMode", True) else 0,
        "rm":  refund_model,
        "rd":  int(payload.get("RefundDeadlineDays") or 0),
        "pf":  float(payload.get("PlatformFeePercent") or 0),
        "cc":  (payload.get("CurrencyCode") or "USD").upper()[:10],
        "pid": int(people_id) if str(people_id).isdigit() else None,
    })
    db.commit()
    return {"ok": True}


def get_stripe_config(db: Session) -> dict:
    """Stripe config for this deployment: the OFNPlatformSettings row, with
    environment variables taking precedence.

    The settings row is shared with Oatmeal Farm Network, so keeping the secret
    in the environment lets Livestock of America bill through its own Stripe
    account without putting that credential in a table another site reads -- and
    keeps it out of database backups. Set STRIPE_SECRET_KEY (and optionally
    STRIPE_PUBLISHABLE_KEY / STRIPE_WEBHOOK_SECRET) to use it; leave them unset
    to fall back to whatever the admin saved in Accounting -> Payments.
    """
    row = db.execute(text("""
        SELECT TOP 1 StripeSecretKey, StripeWebhookSecret, StripePublishableKey,
                     StripeTestMode, RefundModel, RefundDeadlineDays,
                     PlatformFeePercent, CurrencyCode
        FROM OFNPlatformSettings ORDER BY SettingID
    """)).mappings().first()
    cfg = dict(row) if row else {}

    env_secret = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if env_secret:
        cfg["StripeSecretKey"] = env_secret

    env_pub = (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip()
    if env_pub:
        cfg["StripePublishableKey"] = env_pub

    # Only accept a genuine webhook signing secret. A publishable or secret key
    # pasted into this variable would make every webhook fail signature
    # verification, which looks like Stripe being broken rather than a typo.
    env_webhook = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if env_webhook.startswith("whsec_"):
        cfg["StripeWebhookSecret"] = env_webhook

    return cfg
