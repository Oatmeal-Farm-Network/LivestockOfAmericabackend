"""Ownership guards for business-scoped endpoints.

Most routers take the business as a plain parameter and trust it, which lets any
caller read or change another business's records by changing the number. These
dependencies close that: the caller must hold the business in BusinessAccess
with Active = 1 — the same check delete_animal and the animal transfer endpoint
already make.

Two shapes, because the routers use two:

    # business_id arrives as a query parameter
    @router.get("/employees", dependencies=[Depends(require_business)])

    # business_id arrives inside the JSON body
    @router.post("/employees", dependencies=[Depends(require_business_body)])

Both are applied at the decorator, so route signatures and bodies stay as they
are. Reading the body in a dependency is safe: Starlette caches it, so the
endpoint's own body parameter still parses.
"""
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user


def assert_business_access(db: Session, business_id, people_id) -> int:
    """Raise unless people_id holds business_id. Returns the id as an int."""
    try:
        bid = int(business_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="A valid business_id is required.")

    row = db.execute(
        text("SELECT 1 AS ok FROM BusinessAccess "
             "WHERE BusinessID = :bid AND PeopleID = :pid AND Active = 1"),
        {"bid": bid, "pid": people_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=403,
                            detail="You do not have access to this business.")
    return bid


def require_business(business_id: int,
                     current_user=Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Guard for routes taking business_id as a query parameter."""
    assert_business_access(db, business_id, current_user.PeopleID)
    return current_user


async def require_business_body(request: Request,
                                current_user=Depends(get_current_user),
                                db: Session = Depends(get_db)):
    """Guard for routes carrying the business id in the JSON body."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="A JSON body is required.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="A JSON object is required.")

    business_id = body.get("business_id", body.get("BusinessID"))
    if business_id is None:
        raise HTTPException(status_code=400, detail="business_id is required.")

    assert_business_access(db, business_id, current_user.PeopleID)
    return current_user
