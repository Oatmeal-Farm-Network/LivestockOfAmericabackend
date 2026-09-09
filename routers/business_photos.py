"""A photo gallery for a business, shown on its directory listing.

Rows rather than Photo1..PhotoN columns: the services table uses fixed slots and
that shape forces a cap into the schema, makes reordering a column shuffle, and
leaves holes when a middle photo is removed.

Reads are public -- the gallery appears on the public directory listing. Writes
require an active BusinessAccess row for the business, the same guard the rest of
the business endpoints use.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
from business_access import assert_business_access
from image_uploads import upload_image

router = APIRouter(prefix="/api/businesses", tags=["business-photos"])

MAX_BUSINESS_PHOTOS = 12


def _row(r) -> dict:
    return {
        "BusinessPhotoID": r.BusinessPhotoID,
        "PhotoUrl": r.PhotoUrl,
        "Caption": r.Caption or "",
        "SortOrder": r.SortOrder,
    }


def _photos(db: Session, business_id: int):
    rows = db.execute(text("""
        SELECT BusinessPhotoID, PhotoUrl, Caption, SortOrder
        FROM BusinessPhotos
        WHERE BusinessID = :bid
        ORDER BY SortOrder, BusinessPhotoID
    """), {"bid": business_id}).fetchall()
    return [_row(r) for r in rows]


@router.get("/{business_id}/photos")
def list_photos(business_id: int, db: Session = Depends(get_db)):
    """Public: the gallery shown on the directory listing."""
    return _photos(db, business_id)


@router.post("/{business_id}/photos/upload")
async def upload_photo(business_id: int, file: UploadFile = File(...),
                       db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)

    used = db.execute(text("SELECT COUNT(*) FROM BusinessPhotos WHERE BusinessID = :bid"),
                      {"bid": business_id}).scalar() or 0
    if used >= MAX_BUSINESS_PHOTOS:
        raise HTTPException(
            status_code=400,
            detail=f"This account already has {MAX_BUSINESS_PHOTOS} photos. "
                   f"Remove one to add another.")

    # Validated by content, not by filename or the caller's content-type.
    url = upload_image(await file.read(), "BusinessPhotos")

    nxt = db.execute(text("SELECT ISNULL(MAX(SortOrder), -1) + 1 FROM BusinessPhotos WHERE BusinessID = :bid"),
                     {"bid": business_id}).scalar()
    db.execute(text("""
        INSERT INTO BusinessPhotos (BusinessID, PhotoUrl, Caption, SortOrder)
        VALUES (:bid, :url, '', :ord)
    """), {"bid": business_id, "url": url, "ord": nxt})
    new_id = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
    db.commit()
    return {"BusinessPhotoID": int(new_id.id), "PhotoUrl": url,
            "Caption": "", "SortOrder": nxt}


def _owned_photo(db: Session, business_id: int, photo_id: int):
    row = db.execute(text("""
        SELECT BusinessPhotoID FROM BusinessPhotos
        WHERE BusinessPhotoID = :pid AND BusinessID = :bid
    """), {"pid": photo_id, "bid": business_id}).fetchone()
    if not row:
        # Scoped to the business as well as the id, so a photo id belonging to
        # another account cannot be edited by naming the wrong business.
        raise HTTPException(status_code=404, detail="Photo not found")
    return row


@router.post("/{business_id}/photos/{photo_id}/caption")
def save_caption(business_id: int, photo_id: int, data: dict,
                 db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    _owned_photo(db, business_id, photo_id)
    db.execute(text("UPDATE BusinessPhotos SET Caption = :cap WHERE BusinessPhotoID = :pid"),
               {"cap": (data.get("caption") or "")[:256], "pid": photo_id})
    db.commit()
    return {"ok": True}


@router.delete("/{business_id}/photos/{photo_id}")
def delete_photo(business_id: int, photo_id: int,
                 db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    _owned_photo(db, business_id, photo_id)
    db.execute(text("DELETE FROM BusinessPhotos WHERE BusinessPhotoID = :pid"),
               {"pid": photo_id})
    db.commit()
    return {"ok": True}


@router.post("/{business_id}/photos/reorder")
def reorder_photos(business_id: int, data: dict,
                   db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    """Persist a new order. Takes {"ids": [...]} in the order to display."""
    assert_business_access(db, business_id, current_user.PeopleID)
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")

    owned = {r.BusinessPhotoID for r in db.execute(text(
        "SELECT BusinessPhotoID FROM BusinessPhotos WHERE BusinessID = :bid"),
        {"bid": business_id}).fetchall()}
    try:
        wanted = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ids must be integers")
    if set(wanted) - owned:
        raise HTTPException(status_code=400,
                            detail="ids must all belong to this business")

    for position, pid in enumerate(wanted):
        db.execute(text("UPDATE BusinessPhotos SET SortOrder = :ord WHERE BusinessPhotoID = :pid"),
                   {"ord": position, "pid": pid})
    db.commit()
    return _photos(db, business_id)
