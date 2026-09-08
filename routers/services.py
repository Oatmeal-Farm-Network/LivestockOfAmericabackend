import html
import os
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from auth import get_current_user
from business_access import assert_business_access
import httpx

router = APIRouter()


# Same bucket the animal photos use; services get their own folder in it.
_GCS_BUCKET = "oatmeal-farm-network-images"
_GCS_PREFIX = f"https://storage.googleapis.com/{_GCS_BUCKET}/"


def _upload_service_photo(file_bytes: bytes, original_filename: str) -> str:
    from google.cloud import storage as _gcs
    ext = os.path.splitext(original_filename)[1].lower() or ".webp"
    fname = f"{uuid.uuid4().hex}{ext}"
    ct_map = {".webp": "image/webp", ".jpg": "image/jpeg",
              ".jpeg": "image/jpeg", ".png": "image/png"}
    blob = _gcs.Client().bucket(_GCS_BUCKET).blob(f"Services/{fname}")
    blob.upload_from_string(file_bytes, content_type=ct_map.get(ext, "image/webp"))
    return f"{_GCS_PREFIX}Services/{quote(fname, safe='')}"


# -------------------------
# Access control
#
# The write endpoints below took no user at all: any caller could add a service
# to someone else's business, or edit and delete any service by id. The frontend
# was already sending a bearer token on all of them, so requiring it here needs
# no client change. assert_business_access is the shared guard from
# business_access.py — the same check the animal endpoints make.
# -------------------------
def _require_service_access(db: Session, current_user, services_id: int) -> int:
    """Caller must hold the business that owns this service."""
    row = db.execute(
        text("SELECT BusinessID FROM Services WHERE ServicesID = :sid"),
        {"sid": services_id},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return assert_business_access(db, row.BusinessID, current_user.PeopleID)


# Was a literal API key in this file. Every other module in this service reads
# the same variable, so it follows that convention now.
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
FROM_EMAIL = os.getenv("FROM_EMAIL", "john@oatmeal-ai.com")
TO_EMAIL = os.getenv("SERVICES_NOTIFY_EMAIL", "livestockoftheworld@gmail.com")

def send_email(subject: str, body: str):
    if not SENDGRID_API_KEY:
        print("SendGrid not configured; skipping notification: %s" % subject)
        return
    payload = {
        "personalizations": [{"to": [{"email": TO_EMAIL}]}],
        "from": {"email": FROM_EMAIL, "name": "Oatmeal Farm Network"},
        "subject": subject,
        "content": [{"type": "text/html", "value": body}],
    }
    headers = {
        "Authorization": f"Bearer {SENDGRID_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(SENDGRID_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code >= 400:
            print(f"SendGrid rejected the message: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"SendGrid error: {e}")

# -------------------------
# List services for a business
# -------------------------
@router.get("/api/services")
def list_services(BusinessID: int, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    assert_business_access(db, BusinessID, current_user.PeopleID)
    rows = db.execute(text("""
        SELECT s.ServicesID, s.ServiceTitle, s.ServiceAvailable, s.ServicePrice,
               s.ServiceContactForPrice, sc.ServicesCategory, ssc.ServiceSubCategoryName
        FROM Services s
        LEFT JOIN servicescategories sc ON s.ServiceCategoryID = sc.ServiceCategoryID
        LEFT JOIN servicessubcategories ssc
               ON ssc.ServicesSubcategoryID = s.ServiceSubCategoryID
        WHERE s.BusinessID = :bid ORDER BY s.ServiceTitle
    """), {"bid": BusinessID}).fetchall()
    return [dict(r._mapping) for r in rows]

# -------------------------
# Get categories
# -------------------------
@router.get("/api/services/categories")
def get_categories(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT ServiceCategoryID, ServicesCategory
        FROM servicescategories
        ORDER BY ServicesCategory
    """)).fetchall()
    return [dict(r._mapping) for r in rows]

# -------------------------
# Get subcategories for a category
# -------------------------
@router.get("/api/services/categories/{category_id}/subcategories")
def get_subcategories(category_id: int, db: Session = Depends(get_db)):
    # The id column is ServicesSubcategoryID; selecting ServiceSubCategoryID
    # raised "Invalid column name" on every call, so this 500'd and the
    # subcategory dropdown — which hides itself when the list is empty — never
    # appeared. Aliased to the name the client already expects.
    # ServiceCategoryID is varchar here but int in servicescategories, so it is
    # converted explicitly rather than left to an implicit cast.
    rows = db.execute(text("""
        SELECT ServicesSubcategoryID AS ServiceSubCategoryID, ServiceSubCategoryName
        FROM servicessubcategories
        WHERE TRY_CONVERT(int, ServiceCategoryID) = :cid
        ORDER BY ServiceSubCategoryName
    """), {"cid": category_id}).fetchall()
    return [dict(r._mapping) for r in rows]

# -------------------------
# Add a service
# -------------------------
@router.post("/api/services/add")
def add_service(data: dict, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    assert_business_access(db, data.get("BusinessID"), current_user.PeopleID)
    db.execute(text("""
        INSERT INTO Services (
            BusinessID, ServiceCategoryID, ServiceSubCategoryID, ServiceTitle,
            ServicePrice, ServiceContactForPrice, ServiceAvailable, ServicesDescription,
            ServicePhone, Servicewebsite, Serviceemail
        ) VALUES (
            :bid, :cat, :subcat, :title,
            :price, :cfp, :avail, :desc,
            :phone, :web, :email
        )
    """), {
        "bid": data.get("BusinessID"),
        "cat": data.get("ServiceCategoryID") or None,
        "subcat": data.get("ServiceSubCategoryID") or None,
        "title": data.get("ServiceTitle"),
        "price": data.get("ServicePrice") or None,
        "cfp": data.get("ServiceContactForPrice", 0),
        "avail": data.get("ServiceAvailable"),
        "desc": data.get("ServicesDescription"),
        "phone": data.get("ServicePhone"),
        "web": data.get("Servicewebsite"),
        "email": data.get("Serviceemail"),
    })
    new_id = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
    db.commit()
    return {"ServicesID": int(new_id.id)}

# -------------------------
# Get single service (for editing)
# NOTE: path uses {services_id:int} so literal segments like "public" or
# "subcategories" fall through to their own specific routes.
# -------------------------
@router.get("/api/services/{services_id:int}")
def get_service(services_id: int, db: Session = Depends(get_db),
                current_user=Depends(get_current_user)):
    _require_service_access(db, current_user, services_id)
    row = db.execute(text("""
        SELECT s.*, sc.ServicesCategory, ssc.ServiceSubCategoryName
        FROM Services s
        LEFT JOIN servicescategories sc ON s.ServiceCategoryID = sc.ServiceCategoryID
        LEFT JOIN servicessubcategories ssc
               ON ssc.ServicesSubcategoryID = s.ServiceSubCategoryID
        WHERE s.ServicesID = :sid
    """), {"sid": services_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    return dict(row._mapping)

# -------------------------
# Update a service
# -------------------------
@router.post("/api/services/{services_id:int}/update")
def update_service(services_id: int, data: dict, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    _require_service_access(db, current_user, services_id)
    db.execute(text("""
        UPDATE Services SET
            ServiceCategoryID    = :cat,
            ServiceSubCategoryID = :subcat,
            ServiceTitle         = :title,
            ServicePrice         = :price,
            ServiceContactForPrice = :cfp,
            ServiceAvailable     = :avail,
            ServicesDescription  = :desc,
            ServicePhone         = :phone,
            Servicewebsite       = :web,
            Serviceemail         = :email
        WHERE ServicesID = :sid
    """), {
        "sid":    services_id,
        "cat":    data.get("ServiceCategoryID") or None,
        "subcat": data.get("ServiceSubCategoryID") or None,
        "title":  data.get("ServiceTitle"),
        "price":  data.get("ServicePrice") or None,
        "cfp":    data.get("ServiceContactForPrice", 0),
        "avail":  data.get("ServiceAvailable"),
        "desc":   data.get("ServicesDescription"),
        "phone":  data.get("ServicePhone"),
        "web":    data.get("Servicewebsite"),
        "email":  data.get("Serviceemail"),
    })
    db.commit()
    return {"ok": True}

# -------------------------
# Delete a service
# -------------------------
@router.delete("/api/services/{services_id:int}")
def delete_service(services_id: int, db: Session = Depends(get_db),
                   current_user=Depends(get_current_user)):
    _require_service_access(db, current_user, services_id)
    db.execute(text("DELETE FROM Services WHERE ServicesID = :sid"), {"sid": services_id})
    db.commit()
    return {"ok": True}

# -------------------------
# Public: all subcategories across all categories
# -------------------------
@router.get("/api/services/subcategories/all")
def all_subcategories(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT ssc.ServicesSubcategoryID AS ServiceSubCategoryID,
               ssc.ServiceSubCategoryName,
               ssc.ServiceCategoryID, sc.ServicesCategory
        FROM servicessubcategories ssc
        LEFT JOIN servicescategories sc
               ON TRY_CONVERT(int, ssc.ServiceCategoryID) = sc.ServiceCategoryID
        ORDER BY sc.ServicesCategory, ssc.ServiceSubCategoryName
    """)).fetchall()
    return [dict(r._mapping) for r in rows]

# -------------------------
# Public: browse services (optional category / subcategory / search filter)
# -------------------------
@router.get("/api/services/public")
def browse_services(
    category_id: int = None,
    subcategory_id: int = None,
    q: str = None,
    db: Session = Depends(get_db),
):
    where = ["s.ServiceAvailable = 1"]
    params = {}
    if category_id:
        where.append("s.ServiceCategoryID = :cid")
        params["cid"] = category_id
    # subcategory_id was accepted and then never used, so the directory's
    # subcategory dropdown filtered nothing.
    if subcategory_id:
        where.append("s.ServiceSubCategoryID = :subcid")
        params["subcid"] = subcategory_id
    if q:
        where.append(
            "(s.ServiceTitle LIKE :q OR s.ServicesDescription LIKE :q OR b.BusinessName LIKE :q)"
        )
        params["q"] = f"%{q}%"

    sql = f"""
        SELECT s.ServicesID, s.ServiceTitle, s.ServicesDescription,
               s.ServicePrice, s.ServiceContactForPrice, s.ServiceAvailable,
               s.Photo1, s.BusinessID,
               b.BusinessName,
               sc.ServicesCategory, sc.ServiceCategoryID,
               ssc.ServicesSubcategoryID AS ServiceSubCategoryID,
               ssc.ServiceSubCategoryName
        FROM Services s
        JOIN Business b ON s.BusinessID = b.BusinessID
        LEFT JOIN servicescategories sc ON s.ServiceCategoryID = sc.ServiceCategoryID
        LEFT JOIN servicessubcategories ssc
               ON ssc.ServicesSubcategoryID = s.ServiceSubCategoryID
        WHERE {' AND '.join(where)}
        ORDER BY sc.ServicesCategory, s.ServiceTitle
    """
    rows = db.execute(text(sql), params).fetchall()
    return [dict(r._mapping) for r in rows]

# -------------------------
# Public: single service detail
# -------------------------
@router.get("/api/services/public/{services_id}")
def service_detail(services_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT s.*, b.BusinessName, b.BusinessID AS BizID,
               sc.ServicesCategory, ssc.ServiceSubCategoryName,
               a.AddressCity, a.AddressState, a.AddressZip, a.AddressCountry
        FROM Services s
        JOIN Business b ON s.BusinessID = b.BusinessID
        LEFT JOIN servicescategories sc ON s.ServiceCategoryID = sc.ServiceCategoryID
        LEFT JOIN servicessubcategories ssc
               ON ssc.ServicesSubcategoryID = s.ServiceSubCategoryID
        LEFT JOIN Address a ON b.AddressID = a.AddressID
        WHERE s.ServicesID = :sid
    """), {"sid": services_id}).fetchone()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Service not found")
    d = dict(row._mapping)
    # Collect photos
    d["photos"] = [d.get(f"Photo{i}") for i in range(1, 9) if d.get(f"Photo{i}")]
    return d

# -------------------------
# Public: services for a specific business
# -------------------------
@router.get("/api/services/business/{business_id}")
def services_by_business(business_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT s.ServicesID, s.ServiceTitle, s.ServicesDescription,
               s.ServicePrice, s.ServiceContactForPrice, s.ServiceAvailable,
               s.Photo1, sc.ServicesCategory
        FROM Services s
        LEFT JOIN servicescategories sc ON s.ServiceCategoryID = sc.ServiceCategoryID
        WHERE s.BusinessID = :bid AND s.ServiceAvailable = 1
        ORDER BY s.ServiceTitle
    """), {"bid": business_id}).fetchall()
    return [dict(r._mapping) for r in rows]

# -------------------------
# Suggest a new category (sends email via SendGrid)
# -------------------------
@router.post("/api/services/suggest-category")
def suggest_category(data: dict, db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    # This took no user and no db: anyone could POST arbitrary HTML and have it
    # emailed out from our domain. The page always sends a token and a
    # BusinessID, so the sender is verified here and the business name is read
    # from the database rather than trusted from the body, which also stops the
    # notification from being addressed by a name the caller made up.
    business_id = assert_business_access(db, data.get("BusinessID"), current_user.PeopleID)
    row = db.execute(text("SELECT BusinessName FROM Business WHERE BusinessID = :bid"),
                     {"bid": business_id}).fetchone()
    business_name = (row.BusinessName if row else None) or f"Business #{business_id}"

    categories = str(data.get("Categories", ""))[:2000]
    subcategories = str(data.get("SubCategories", ""))[:2000]
    if not categories.strip():
        raise HTTPException(status_code=400, detail="Categories is required")

    # Everything below is caller-supplied, so it is escaped before being put in
    # an HTML mail body.
    body = f"""
    <h2>New Service Category Suggestion</h2>
    <p><b>Business:</b> {html.escape(business_name)} (#{business_id})</p>
    <p><b>Submitted by:</b> {html.escape(current_user.PeopleEmail or '')} (PeopleID {current_user.PeopleID})</p>
    <p><b>Suggested Categories:</b><br>{html.escape(categories)}</p>
    <p><b>Suggested Sub-Categories:</b><br>{html.escape(subcategories) or 'None provided'}</p>
    """

    send_email(
        subject=f"Service Category Suggestion from {business_name}",
        body=body,
    )
    return {"message": "Suggestion sent"}


# -------------------------
# Photos
#
# These four lived in produce.py, whose router carries prefix="/api/produce" —
# so they were actually served at /api/produce/api/services/{id}/photos and the
# edit page's Photos tab had been 404ing against every one of them. The upload
# route did not exist at all. Moved here so the paths match what the page calls,
# and scoped to the owning business like the rest of this router.
# -------------------------
# Get photos
@router.get("/api/services/{services_id}/photos")
def get_photos(services_id: int, db: Session = Depends(get_db),
               current_user=Depends(get_current_user)):
    _require_service_access(db, current_user, services_id)
    row = db.execute(text("SELECT Photo1,Photo2,Photo3,Photo4,Photo5,Photo6,Photo7,Photo8,PhotoCaption1,PhotoCaption2,PhotoCaption3,PhotoCaption4,PhotoCaption5,PhotoCaption6,PhotoCaption7,PhotoCaption8 FROM Services WHERE ServicesID = :id"), {"id": services_id}).fetchone()
    if not row:
        return []
    d = dict(row._mapping)
    return [{"slot": i+1, "url": d.get(f"Photo{i+1}") or "", "caption": d.get(f"PhotoCaption{i+1}") or ""} for i in range(8)]

# Upload photo into a slot
@router.post("/api/services/{services_id}/photos/upload")
async def upload_service_photo(services_id: int, file: UploadFile = File(...),
                               slot: int = 1, db: Session = Depends(get_db),
                               current_user=Depends(get_current_user)):
    if slot < 1 or slot > 8:
        raise HTTPException(status_code=400, detail="slot must be 1-8")
    _require_service_access(db, current_user, services_id)
    url = _upload_service_photo(await file.read(), file.filename or "photo.webp")
    db.execute(text(f"UPDATE Services SET Photo{slot} = :url WHERE ServicesID = :id"),
               {"url": url, "id": services_id})
    db.commit()
    return {"url": url, "slot": slot}

# Remove photo
@router.post("/api/services/{services_id}/photos/{slot}/remove")
def remove_photo(services_id: int, slot: int, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    if slot < 1 or slot > 8:
        raise HTTPException(status_code=400, detail="slot must be 1-8")
    _require_service_access(db, current_user, services_id)
    db.execute(text(f"UPDATE Services SET Photo{slot} = '', PhotoCaption{slot} = '' WHERE ServicesID = :id"), {"id": services_id})
    db.commit()
    return {"message": "Removed"}

# Save caption
@router.post("/api/services/{services_id}/photos/{slot}/caption")
def save_caption(services_id: int, slot: int, data: dict, db: Session = Depends(get_db),
                 current_user=Depends(get_current_user)):
    if slot < 1 or slot > 8:
        raise HTTPException(status_code=400, detail="slot must be 1-8")
    _require_service_access(db, current_user, services_id)
    db.execute(text(f"UPDATE Services SET PhotoCaption{slot} = :cap WHERE ServicesID = :id"), {"cap": data.get("caption"), "id": services_id})
    db.commit()
    return {"message": "Saved"}
