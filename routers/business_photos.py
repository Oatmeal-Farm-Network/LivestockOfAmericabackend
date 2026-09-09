"""Photo pages for a business, shown on its directory listing.

A business keeps several named pages -- "Our Ranch", "Facilities", "2026 Calf
Crop" -- each holding its own photos. Reads are public, since the pages appear
on the public directory listing. Writes require an active BusinessAccess row.

Photos are rows rather than Photo1..PhotoN columns: fixed slots would force the
cap into the schema, make reordering a column shuffle, and leave holes when a
middle photo is removed.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from auth import get_current_user
from business_access import assert_business_access
from image_uploads import upload_image, delete_image

router = APIRouter(prefix="/api/businesses", tags=["business-photos"])

MAX_PHOTO_PAGES = 10
MAX_PHOTOS_PER_PAGE = 24


# ── pages ────────────────────────────────────────────────────────────────────

def _pages(db: Session, business_id: int):
    rows = db.execute(text("""
        SELECT pg.BusinessPhotoPageID, pg.Title, pg.SortOrder,
               (SELECT COUNT(*) FROM BusinessPhotos ph
                 WHERE ph.BusinessPhotoPageID = pg.BusinessPhotoPageID) AS PhotoCount,
               (SELECT TOP 1 ph.PhotoUrl FROM BusinessPhotos ph
                 WHERE ph.BusinessPhotoPageID = pg.BusinessPhotoPageID
                 ORDER BY ph.SortOrder, ph.BusinessPhotoID) AS CoverUrl
        FROM BusinessPhotoPages pg
        WHERE pg.BusinessID = :bid
        ORDER BY pg.SortOrder, pg.BusinessPhotoPageID
    """), {"bid": business_id}).fetchall()
    return [{
        "BusinessPhotoPageID": r.BusinessPhotoPageID,
        "Title": r.Title,
        "SortOrder": r.SortOrder,
        "PhotoCount": r.PhotoCount,
        "CoverUrl": r.CoverUrl,
    } for r in rows]


def _owned_page(db: Session, business_id: int, page_id: int):
    row = db.execute(text("""
        SELECT BusinessPhotoPageID FROM BusinessPhotoPages
        WHERE BusinessPhotoPageID = :pid AND BusinessID = :bid
    """), {"pid": page_id, "bid": business_id}).fetchone()
    if not row:
        # Scoped to the business as well as the id, so a page belonging to
        # another account cannot be reached by naming the wrong business.
        raise HTTPException(status_code=404, detail="Photo page not found")
    return row


@router.get("/{business_id}/photo-pages")
def list_pages(business_id: int, db: Session = Depends(get_db)):
    """Public: the photo pages shown on the directory listing."""
    return _pages(db, business_id)


@router.post("/{business_id}/photo-pages")
def create_page(business_id: int, data: dict, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    title = (data.get("title") or "").strip()[:120]
    if not title:
        raise HTTPException(status_code=400, detail="A page title is required.")

    used = db.execute(text("SELECT COUNT(*) FROM BusinessPhotoPages WHERE BusinessID = :bid"),
                      {"bid": business_id}).scalar() or 0
    if used >= MAX_PHOTO_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"This account already has {MAX_PHOTO_PAGES} photo pages. "
                   f"Remove one to add another.")

    nxt = db.execute(text("SELECT ISNULL(MAX(SortOrder), -1) + 1 FROM BusinessPhotoPages WHERE BusinessID = :bid"),
                     {"bid": business_id}).scalar()
    db.execute(text("""
        INSERT INTO BusinessPhotoPages (BusinessID, Title, SortOrder)
        VALUES (:bid, :title, :ord)
    """), {"bid": business_id, "title": title, "ord": nxt})
    new_id = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
    db.commit()
    return {"BusinessPhotoPageID": int(new_id.id), "Title": title,
            "SortOrder": nxt, "PhotoCount": 0, "CoverUrl": None}


# Registered before /photo-pages/{page_id}: FastAPI matches in definition
# order, so with rename first a POST to .../reorder was resolving to
# rename_page with page_id="reorder".
@router.post("/{business_id}/photo-pages/reorder")
def reorder_pages(business_id: int, data: dict, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")
    owned = {r.BusinessPhotoPageID for r in db.execute(text(
        "SELECT BusinessPhotoPageID FROM BusinessPhotoPages WHERE BusinessID = :bid"),
        {"bid": business_id}).fetchall()}
    try:
        wanted = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ids must be integers")
    if set(wanted) - owned:
        raise HTTPException(status_code=400, detail="ids must all belong to this business")
    for position, pid in enumerate(wanted):
        db.execute(text("UPDATE BusinessPhotoPages SET SortOrder = :o WHERE BusinessPhotoPageID = :p"),
                   {"o": position, "p": pid})
    db.commit()
    return _pages(db, business_id)


@router.post("/{business_id}/photo-pages/{page_id}")
def rename_page(business_id: int, page_id: int, data: dict,
                db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    _owned_page(db, business_id, page_id)
    title = (data.get("title") or "").strip()[:120]
    if not title:
        raise HTTPException(status_code=400, detail="A page title is required.")
    db.execute(text("UPDATE BusinessPhotoPages SET Title = :t WHERE BusinessPhotoPageID = :pid"),
               {"t": title, "pid": page_id})
    db.commit()
    return {"ok": True, "Title": title}


@router.delete("/{business_id}/photo-pages/{page_id}")
def delete_page(business_id: int, page_id: int, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    _owned_page(db, business_id, page_id)
    # The photo rows go with the page. The stored objects are left in the
    # bucket, the same as removing a single photo or an animal photo.
    urls = [r.PhotoUrl for r in db.execute(text(
        "SELECT PhotoUrl FROM BusinessPhotos WHERE BusinessPhotoPageID = :pid"),
        {"pid": page_id}).fetchall()]
    removed = len(urls)
    db.execute(text("DELETE FROM BusinessPhotos WHERE BusinessPhotoPageID = :pid"), {"pid": page_id})
    db.execute(text("DELETE FROM BusinessPhotoPages WHERE BusinessPhotoPageID = :pid"), {"pid": page_id})
    db.commit()
    # After the commit: the rows are gone either way, and a storage hiccup must
    # not roll back a delete the caller has already been told about.
    for u in urls:
        delete_image(u)
    return {"ok": True, "PhotosRemoved": removed}


# ── photos within a page ─────────────────────────────────────────────────────

def _photos(db: Session, business_id: int, page_id=None):
    sql = """
        SELECT BusinessPhotoID, BusinessPhotoPageID, PhotoUrl, Caption, SortOrder
        FROM BusinessPhotos
        WHERE BusinessID = :bid
    """
    params = {"bid": business_id}
    if page_id is not None:
        sql += " AND BusinessPhotoPageID = :pid"
        params["pid"] = page_id
    sql += " ORDER BY SortOrder, BusinessPhotoID"
    rows = db.execute(text(sql), params).fetchall()
    return [{
        "BusinessPhotoID": r.BusinessPhotoID,
        "BusinessPhotoPageID": r.BusinessPhotoPageID,
        "PhotoUrl": r.PhotoUrl,
        "Caption": r.Caption or "",
        "SortOrder": r.SortOrder,
    } for r in rows]


@router.get("/{business_id}/photos")
def list_photos(business_id: int, page_id: int = None, db: Session = Depends(get_db)):
    """Public. Without page_id this returns every photo the business has, which
    is what the old single-gallery callers expect."""
    return _photos(db, business_id, page_id)


def _owned_photo(db: Session, business_id: int, photo_id: int):
    row = db.execute(text("""
        SELECT BusinessPhotoID FROM BusinessPhotos
        WHERE BusinessPhotoID = :pid AND BusinessID = :bid
    """), {"pid": photo_id, "bid": business_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    return row


@router.post("/{business_id}/photos/upload")
async def upload_photo(business_id: int, page_id: int, file: UploadFile = File(...),
                       db: Session = Depends(get_db),
                       current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    _owned_page(db, business_id, page_id)

    used = db.execute(text("SELECT COUNT(*) FROM BusinessPhotos WHERE BusinessPhotoPageID = :pid"),
                      {"pid": page_id}).scalar() or 0
    if used >= MAX_PHOTOS_PER_PAGE:
        raise HTTPException(
            status_code=400,
            detail=f"This page already has {MAX_PHOTOS_PER_PAGE} photos. "
                   f"Remove one, or add another page.")

    # Validated by content, not by filename or the caller's content-type.
    url = upload_image(await file.read(), "BusinessPhotos")

    nxt = db.execute(text("SELECT ISNULL(MAX(SortOrder), -1) + 1 FROM BusinessPhotos WHERE BusinessPhotoPageID = :pid"),
                     {"pid": page_id}).scalar()
    db.execute(text("""
        INSERT INTO BusinessPhotos (BusinessID, BusinessPhotoPageID, PhotoUrl, Caption, SortOrder)
        VALUES (:bid, :pid, :url, '', :ord)
    """), {"bid": business_id, "pid": page_id, "url": url, "ord": nxt})
    new_id = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
    db.commit()
    return {"BusinessPhotoID": int(new_id.id), "BusinessPhotoPageID": page_id,
            "PhotoUrl": url, "Caption": "", "SortOrder": nxt}


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
def delete_photo(business_id: int, photo_id: int, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    assert_business_access(db, business_id, current_user.PeopleID)
    _owned_photo(db, business_id, photo_id)
    url = db.execute(text("SELECT PhotoUrl FROM BusinessPhotos WHERE BusinessPhotoID = :pid"),
                     {"pid": photo_id}).scalar()
    db.execute(text("DELETE FROM BusinessPhotos WHERE BusinessPhotoID = :pid"), {"pid": photo_id})
    db.commit()
    delete_image(url)
    return {"ok": True}


@router.post("/{business_id}/photos/reorder")
def reorder_photos(business_id: int, data: dict, db: Session = Depends(get_db),
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
        raise HTTPException(status_code=400, detail="ids must all belong to this business")
    for position, pid in enumerate(wanted):
        db.execute(text("UPDATE BusinessPhotos SET SortOrder = :o WHERE BusinessPhotoID = :p"),
                   {"o": position, "p": pid})
    db.commit()
    return _photos(db, business_id)
