"""
routers/subscription_limits.py

Enforces the per-package listing allowances stored on SubscriptionPackage
(MaxForSaleListings, MaxStudListings, MaxDirectoryListings).

How a business resolves to a package
------------------------------------
There is no BusinessID -> PackageID foreign key. When a package is assigned
(platform_subscriptions.assign_package) the package *name* is copied into
Business.SubscriptionTier, so that string is the only link available and this
module joins on it.

That join is deliberately strict, which matters because the shared database
holds legacy tier strings that predate the package catalog -- roughly 1,800
businesses sit on 'basic' and another 96 on 'Free'. Neither matches a
PackageName, so both resolve to "no package" and stay unlimited. Anything that
does not resolve to a package with a non-NULL limit is never restricted, so
Oatmeal Farm Network accounts are unaffected by this module.

One consequence worth remembering: naming a future package 'basic' or 'Free'
would retroactively cap every one of those legacy businesses.

NULL means unlimited. 0 means the plan excludes that listing type entirely.
"""

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

# Business.SubscriptionStatus values that count as entitled to the package.
# assign_package writes 'active'; NULL is treated as active so a package
# assigned by other means still applies.
_ACTIVE_STATUSES = ("active", "trialing")

_LIMITS_SQL = text("""
    SELECT p.PackageName,
           p.MaxForSaleListings,
           p.MaxStudListings,
           p.MaxDirectoryListings
    FROM Business b
    JOIN SubscriptionPackage p
      ON p.PackageName = b.SubscriptionTier
     AND p.IsActive = 1
    WHERE b.BusinessID = :bid
      AND (b.SubscriptionStatus IS NULL
           OR LOWER(b.SubscriptionStatus) IN :statuses)
""").bindparams(statuses=_ACTIVE_STATUSES)


def get_package_limits(db: Session, business_id: int) -> Optional[dict]:
    """The business's package allowances, or None when it has no package."""
    if not business_id:
        return None
    row = db.execute(_LIMITS_SQL, {"bid": business_id}).mappings().fetchone()
    return dict(row) if row else None


# Column and human wording per listing kind, keyed by the value callers pass.
_KINDS = {
    "for_sale": ("MaxForSaleListings", "PublishForSale", "livestock listings for sale"),
    "stud":     ("MaxStudListings",    "PublishStud",    "stud listings"),
}


def count_published(db: Session, business_id: int, kind: str,
                    exclude_animal_id: Optional[int] = None) -> int:
    """Animals the business currently has published under `kind`."""
    _, column, _ = _KINDS[kind]
    sql = f"SELECT COUNT(*) FROM Animals WHERE BusinessID = :bid AND {column} = 1"
    params = {"bid": business_id}
    if exclude_animal_id is not None:
        sql += " AND AnimalID <> :aid"
        params["aid"] = exclude_animal_id
    return db.execute(text(sql), params).scalar() or 0


def assert_can_publish(db: Session, business_id: int, kind: str,
                       animal_id: Optional[int] = None) -> None:
    """Raise 402 if publishing one more `kind` listing would exceed the plan.

    Only called when actually turning a listing ON -- unpublishing is always
    allowed, since that is how a member gets back under their limit.

    Publishing something already published is a no-op and never refused. That
    matters for businesses sitting above their allowance (a downgrade, or a
    limit lowered after the fact): their existing listings stay editable, they
    simply cannot add another until they are back under the cap.
    """
    limits = get_package_limits(db, business_id)
    if not limits:
        return

    limit_col, publish_col, noun = _KINDS[kind]

    if animal_id is not None:
        already = db.execute(
            text(f"SELECT {publish_col} FROM Animals WHERE AnimalID = :aid"),
            {"aid": animal_id},
        ).scalar()
        if already:
            return
    allowed = limits.get(limit_col)
    if allowed is None:          # unlimited
        return

    package = limits.get("PackageName") or "your plan"

    if allowed == 0:
        raise HTTPException(
            status_code=402,
            detail=f"{package} does not include {noun}. Upgrade your plan to publish this listing.",
        )

    current = count_published(db, business_id, kind, exclude_animal_id=animal_id)
    if current >= allowed:
        raise HTTPException(
            status_code=402,
            detail=(
                f"{package} includes up to {allowed} {noun} and you already have "
                f"{current}. Unpublish one, or upgrade your plan to add more."
            ),
        )


def business_id_for_animal(db: Session, animal_id: int) -> Optional[int]:
    """The owning BusinessID, or None when the animal has no business."""
    row = db.execute(
        text("SELECT BusinessID FROM Animals WHERE AnimalID = :aid"),
        {"aid": animal_id},
    ).fetchone()
    return row[0] if row else None


# Set-based form of directory_listing_allowed(), for the directory queries that
# filter many businesses at once. Expects the Business table aliased as `b`.
# Written as NOT EXISTS so a business is hidden only when its package explicitly
# sets the allowance to 0 -- unmatched tiers and NULL limits stay visible.
DIRECTORY_VISIBLE_SQL = """
    NOT EXISTS (
        SELECT 1 FROM SubscriptionPackage sp_dir
        WHERE sp_dir.PackageName = b.SubscriptionTier
          AND sp_dir.IsActive = 1
          AND sp_dir.MaxDirectoryListings = 0
    )
"""


def directory_listing_allowed(db: Session, business_id: int) -> bool:
    """False only when the business's package explicitly excludes the directory.

    A business is its own single directory entry -- there is no table of
    listings to count -- so MaxDirectoryListings acts as an on/off switch here:
    0 hides the business, anything else (including NULL) shows it.
    """
    limits = get_package_limits(db, business_id)
    if not limits:
        return True
    return limits.get("MaxDirectoryListings") != 0
