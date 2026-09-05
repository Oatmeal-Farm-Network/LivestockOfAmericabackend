"""
routers/animals.py
Endpoints for the Animal Edit page (AnimalEdit.jsx)

Mount in main.py:
    from routers import animals
    app.include_router(animals.router)   # prefix: /api/animals
"""

import os, uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from auth import get_current_user
from routers.subscription_limits import (
    assert_can_publish, business_id_for_animal,
)

_GCS_BUCKET  = "oatmeal-farm-network-images"
_GCS_PREFIX  = f"https://storage.googleapis.com/{_GCS_BUCKET}/"
_PHOTO_SLOTS   = ["Photo1","Photo2","Photo3","Photo4","Photo5","Photo6","Photo7","Photo8"]
_CAPTION_SLOTS = ["PhotoCaption1","PhotoCaption2","PhotoCaption3","PhotoCaption4",
                  "PhotoCaption5","PhotoCaption6","PhotoCaption7","PhotoCaption8"]


def _upload_animal_photo(file_bytes: bytes, original_filename: str) -> str:
    from google.cloud import storage as _gcs
    ext   = os.path.splitext(original_filename)[1].lower() or ".webp"
    fname = f"{uuid.uuid4().hex}{ext}"
    ct_map = {".webp":"image/webp",".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png"}
    bucket = _gcs.Client().bucket(_GCS_BUCKET)
    blob   = bucket.blob(f"Animals/{fname}")
    blob.upload_from_string(file_bytes, content_type=ct_map.get(ext, "image/webp"))
    return f"{_GCS_PREFIX}Animals/{quote(fname, safe='')}"


def _upload_animal_document(file_bytes: bytes, original_filename: str) -> str:
    from google.cloud import storage as _gcs
    ext   = os.path.splitext(original_filename)[1].lower() or ".pdf"
    fname = f"{uuid.uuid4().hex}{ext}"
    ct_map = {".pdf":"application/pdf",".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png"}
    bucket = _gcs.Client().bucket(_GCS_BUCKET)
    blob   = bucket.blob(f"AnimalDocs/{fname}")
    blob.upload_from_string(file_bytes, content_type=ct_map.get(ext, "application/octet-stream"))
    return f"{_GCS_PREFIX}AnimalDocs/{quote(fname, safe='')}"


_DOC_COLUMNS = {"registration": "ARI", "histogram": "Histogram"}

router = APIRouter(prefix="/api/animals", tags=["animals"])


# ─── helpers ─────────────────────────────────────────────────────────────────

def _row(r):
    """Convert a SQLAlchemy Row to a plain dict."""
    return dict(r._mapping) if r else {}


def _nullable_int(v):
    try:
        return int(v) if v not in (None, "", "None") else None
    except Exception:
        return None


def _nullable_float(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except Exception:
        return None


# ─── GET ancestor search (for edit/add pedigree autocomplete) ────────────────

@router.get("/search/ancestors")
def search_ancestors(q: str = "", species_id: int | None = None,
                      gender: str | None = None,
                      db: Session = Depends(get_db)):
    """Return up to 20 animals whose FullName matches `q`, optionally filtered
    by species and gender. Used by the ancestry editor to link a sire/dam to
    an existing animal on the site. Gender is derived from the `Category`
    column (values like exp_male, adult_female, preborn_male, etc)."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    sql = (
        "SELECT TOP 20 a.AnimalID, a.FullName, a.SpeciesID, "
        "       c.Color1, c.Color2, c.Color3, c.Color4, c.Color5, "
        "       (SELECT TOP 1 ar.RegNumber FROM AnimalRegistration ar "
        "        WHERE ar.AnimalID = a.AnimalID) AS RegNumber "
        "FROM Animals a "
        "LEFT JOIN Colors c ON c.AnimalID = a.AnimalID "
        "WHERE a.FullName LIKE :q "
    )
    params = {"q": f"%{q}%"}
    if species_id is not None:
        sql += "AND a.SpeciesID = :sid "
        params["sid"] = species_id
    g = (gender or "").lower()
    if g == "male":
        # Legacy: "Herdsire", "Jr. Herdsire", "Stud"; New: "exp_male", "adult_male", etc.
        # "male" substring must exclude "female".
        sql += ("AND ((a.Category LIKE '%male%' AND a.Category NOT LIKE '%female%') "
                "     OR a.Category LIKE '%herdsire%' "
                "     OR a.Category LIKE '%stud%') ")
    elif g == "female":
        # Legacy: "Dam", "Maiden"; New: "exp_female", "adult_female", etc.
        sql += ("AND (a.Category LIKE '%female%' "
                "     OR a.Category LIKE '%dam%' "
                "     OR a.Category LIKE '%maiden%') ")
    sql += "ORDER BY a.FullName"
    rows = db.execute(text(sql), params).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        colors = [d.get(f"Color{i}") for i in range(1, 6)]
        colors = [c for c in colors if c and str(c).strip()]
        out.append({
            "animal_id":   d["AnimalID"],
            "full_name":   d.get("FullName") or "",
            "species_id":  d.get("SpeciesID"),
            "colors":      ", ".join(colors),
            "reg_number":  d.get("RegNumber") or "",
        })
    return out


# ─── GET animal basics ────────────────────────────────────────────────────────

@router.get("/{animal_id}")
def get_animal(animal_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT a.*, c.Color1, c.Color2, c.Color3, c.Color4, c.Color5
        FROM Animals a
        LEFT JOIN Colors c ON c.AnimalID = a.AnimalID
        WHERE a.AnimalID = :aid
    """), {"aid": animal_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Animal not found")
    return _row(row)


# ─── GET species breeds ───────────────────────────────────────────────────────

@router.get("/species/{species_id}/breeds")
def get_breeds(species_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT BreedLookupID AS id, Breed AS name
        FROM SpeciesBreedLookupTable
        WHERE SpeciesID = :sid AND (breedavailable = 1 OR breedavailable IS NULL)
        ORDER BY Breed
    """), {"sid": species_id}).fetchall()
    return [_row(r) for r in rows]


# ─── GET species categories ───────────────────────────────────────────────────

@router.get("/species/{species_id}/categories")
def get_categories(species_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT SpeciesCategoryID AS id, SpeciesCategory AS name
        FROM speciescategory
        WHERE SpeciesID = :sid
        ORDER BY SpeciesCategoryOrder, SpeciesCategory
    """), {"sid": species_id}).fetchall()
    result = [_row(r) for r in rows]
    # Alpacas (2) and Llamas (4): ensure "Non-Breeder" is available
    if species_id in (2, 4) and not any((r.get("name") or "").strip().lower() == "non-breeder" for r in result):
        result.append({"id": -1, "name": "Non-Breeder"})
    return result


# ─── GET registrations ────────────────────────────────────────────────────────

@router.get("/{animal_id}/registrations")
def get_registrations(animal_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT AnimalRegistrationID, RegType, RegNumber
        FROM animalregistration
        WHERE AnimalID = :aid
        ORDER BY RegType
    """), {"aid": animal_id}).fetchall()
    return [_row(r) for r in rows]


# ─── POST update registrations ───────────────────────────────────────────────

@router.post("/{animal_id}/update-registrations")
async def update_registrations(animal_id: int, request: Request,
                                db: Session = Depends(get_db),
                                current_user=Depends(get_current_user)):
    rows = await request.json()
    for row in rows:
        reg_id  = row.get("AnimalRegistrationID")
        reg_num = (row.get("RegNumber") or "").strip() or None
        reg_type = row.get("RegType") or None
        if reg_id:
            db.execute(text("""
                UPDATE animalregistration SET RegNumber = :num WHERE AnimalRegistrationID = :rid AND AnimalID = :aid
            """), {"num": reg_num, "rid": reg_id, "aid": animal_id})
        elif reg_num:
            db.execute(text("""
                INSERT INTO animalregistration (AnimalID, RegType, RegNumber) VALUES (:aid, :rtype, :num)
            """), {"aid": animal_id, "rtype": reg_type, "num": reg_num})
    db.commit()
    return {"message": "Registrations updated"}


# ─── POST update basics ───────────────────────────────────────────────────────

@router.post("/{animal_id}/update-basics")
async def update_basics(animal_id: int, request: Request,
                         db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    form = await request.form()
    f = lambda k: form.get(k) or None
    n = lambda k: _nullable_float(form.get(k))
    i = lambda k: _nullable_int(form.get(k))

    db.execute(text("""
        UPDATE Animals SET
            FullName          = :name,
            SpeciesID         = :species_id,
            SpeciesCategoryID = :species_category_id,
            DOBDay            = :dob_day,
            DOBMonth          = :dob_month,
            DOBYear           = :dob_year,
            BreedID           = :breed1,
            BreedID2          = :breed2,
            BreedID3          = :breed3,
            BreedID4          = :breed4,
            Height            = :height,
            Weight            = :weight,
            Gaited            = :gaited,
            Warmblooded       = :warmblood,
            Horns             = :horns,
            Temperment        = :temperament,
            Vaccinations      = :vaccinations,
            AncestryDescription = :ancestry_desc,
            LastUpdated       = SYSUTCDATETIME()
        WHERE AnimalID = :aid
    """), {
        "aid": animal_id,
        "name": f("Name"),
        "species_id": i("SpeciesID") or None,
        "species_category_id": i("Category"),
        "dob_day": i("DOBDay"), "dob_month": i("DOBMonth"), "dob_year": i("DOBYear"),
        "breed1": i("BreedID"), "breed2": i("BreedID2"),
        "breed3": i("BreedID3"), "breed4": i("BreedID4"),
        "height": n("Height"), "weight": n("Weight"),
        "gaited": f("Gaited"), "warmblood": f("Warmblood"),
        "horns": f("Horns"), "temperament": i("Temperment"),
        "vaccinations": f("Vaccinations"),
        "ancestry_desc": f("AncestryDescription"),
    })

    # Update colors (separate Colors table)
    existing = db.execute(text("SELECT COUNT(*) FROM Colors WHERE AnimalID = :aid"), {"aid": animal_id}).scalar()
    color_params = {
        "aid": animal_id,
        "c1": f("Color1"), "c2": f("Color2"), "c3": f("Color3"),
        "c4": f("Color4"), "c5": f("Color5"),
    }
    if existing:
        db.execute(text("""
            UPDATE Colors SET Color1=:c1, Color2=:c2, Color3=:c3, Color4=:c4, Color5=:c5
            WHERE AnimalID=:aid
        """), color_params)
    else:
        db.execute(text("""
            INSERT INTO Colors (AnimalID, Color1, Color2, Color3, Color4, Color5)
            VALUES (:aid, :c1, :c2, :c3, :c4, :c5)
        """), color_params)

    db.commit()
    return {"message": "Basics updated"}


# ─── GET pricing ──────────────────────────────────────────────────────────────

@router.get("/{animal_id}/pricing")
def get_pricing(animal_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT * FROM Pricing WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
    if not row:
        # Auto-create pricing row
        db.execute(text("INSERT INTO Pricing (AnimalID) VALUES (:aid)"), {"aid": animal_id})
        db.commit()
        row = db.execute(text("SELECT * FROM Pricing WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
    return _row(row)


# ─── POST update pricing ──────────────────────────────────────────────────────

@router.post("/{animal_id}/update-pricing")
async def update_pricing(animal_id: int, request: Request,
                          db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    form = await request.form()
    f = lambda k: form.get(k) or None
    n = lambda k: _nullable_float(form.get(k))

    for_sale = 1 if form.get("ForSale") in ("1", "Yes", "True") else 0
    sold     = 1 if form.get("Sold")    in ("1", "Yes", "True") else 0
    free     = 1 if form.get("Free")    in ("1", "Yes", "True") else 0

    # ForSale lives on Animals.PublishForSale, not in the Pricing table
    if for_sale:
        assert_can_publish(db, business_id_for_animal(db, animal_id), "for_sale", animal_id)
    db.execute(text("UPDATE Animals SET PublishForSale = :v, LastUpdated = SYSUTCDATETIME() WHERE AnimalID = :aid"),
               {"v": for_sale, "aid": animal_id})

    existing = db.execute(text("SELECT COUNT(*) FROM Pricing WHERE AnimalID = :aid"), {"aid": animal_id}).scalar()
    params = {
        "aid": animal_id,
        "sold": sold, "free": free,
        "price": n("Price"), "stud_fee": n("StudFee"),
        "embryo_price": n("EmbryoPrice"), "semen_price": n("SemenPrice"),
        "price_comments": f("PriceComments"),
        "finance_terms": f("Financeterms"),
    }
    if existing:
        db.execute(text("""
            UPDATE Pricing SET
                Sold=:sold, Free=:free,
                Price=:price, StudFee=:stud_fee,
                EmbryoPrice=:embryo_price, SemenPrice=:semen_price,
                PriceComments=:price_comments, Financeterms=:finance_terms
            WHERE AnimalID=:aid
        """), params)
    else:
        db.execute(text("""
            INSERT INTO Pricing (AnimalID, Sold, Free, Price, StudFee,
                EmbryoPrice, SemenPrice, PriceComments, Financeterms)
            VALUES (:aid, :sold, :free, :price, :stud_fee,
                :embryo_price, :semen_price, :price_comments, :finance_terms)
        """), params)

    # Update co-owners on Animals table
    db.execute(text("""
        UPDATE Animals SET
            CoOwnerName1=:n1, CoOwnerLink1=:l1, CoOwnerBusiness1=:b1,
            CoOwnerName2=:n2, CoOwnerLink2=:l2, CoOwnerBusiness2=:b2,
            CoOwnerName3=:n3, CoOwnerLink3=:l3, CoOwnerBusiness3=:b3
        WHERE AnimalID=:aid
    """), {
        "aid": animal_id,
        "n1": f("CoOwnerName1"), "l1": f("CoOwnerLink1"), "b1": f("CoOwnerBusiness1"),
        "n2": f("CoOwnerName2"), "l2": f("CoOwnerLink2"), "b2": f("CoOwnerBusiness2"),
        "n3": f("CoOwnerName3"), "l3": f("CoOwnerLink3"), "b3": f("CoOwnerBusiness3"),
    })

    db.commit()
    return {"message": "Pricing updated"}


# ─── GET description ──────────────────────────────────────────────────────────

@router.get("/{animal_id}/description")
def get_description(animal_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT Description FROM Animals WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
    return {"Description": row.Description if row else ""}


# ─── POST update description ──────────────────────────────────────────────────

@router.post("/{animal_id}/update-description")
async def update_description(animal_id: int, request: Request,
                              db: Session = Depends(get_db),
                              current_user=Depends(get_current_user)):
    body = await request.json()
    db.execute(text("UPDATE Animals SET Description = :desc WHERE AnimalID = :aid"),
               {"desc": body.get("Description"), "aid": animal_id})
    db.commit()
    return {"message": "Description updated"}


# ─── GET ancestry ─────────────────────────────────────────────────────────────

@router.get("/{animal_id}/ancestry")
def get_ancestry(animal_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT * FROM Ancestors WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
    if not row:
        db.execute(text("INSERT INTO Ancestors (AnimalID) VALUES (:aid)"), {"aid": animal_id})
        db.commit()
        row = db.execute(text("SELECT * FROM Ancestors WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
    raw = _row(row)
    # Normalize DB column casing to canonical camelCase so the JS frontend can
    # read each field by its expected key (DB has Siredam / Damsire / Damdam / DamAri
    # mixed with the canonical forms).
    canonical = [
        "Sire","SireColor","SireLink","SireARI","SireCLAA",
        "SireSire","SireSireColor","SireSireLink","SireSireARI","SireSireCLAA",
        "SireSireSire","SireSireSireColor","SireSireSireLink","SireSireSireARI","SireSireSireCLAA",
        "SireSireDam","SireSireDamColor","SireSireDamLink","SireSireDamARI","SireSireDamCLAA",
        "SireDam","SireDamColor","SireDamLink","SireDamARI","SireDamCLAA",
        "SireDamSire","SireDamSireColor","SireDamSireLink","SireDamSireARI","SireDamSireCLAA",
        "SireDamDam","SireDamDamColor","SireDamDamLink","SireDamDamARI","SireDamDamCLAA",
        "Dam","DamColor","DamLink","DamARI","DamCLAA",
        "DamSire","DamSireColor","DamSireLink","DamSireARI","DamSireCLAA",
        "DamSireSire","DamSireSireColor","DamSireSireLink","DamSireSireARI","DamSireSireCLAA",
        "DamSireDam","DamSireDamColor","DamSireDamLink","DamSireDamARI","DamSireDamCLAA",
        "DamDam","DamDamColor","DamDamLink","DamDamARI","DamDamCLAA",
        "DamDamSire","DamDamSireColor","DamDamSireLink","DamDamSireARI","DamDamSireCLAA",
        "DamDamDam","DamDamDamColor","DamDamDamLink","DamDamDamARI","DamDamDamCLAA",
    ]
    ci_map = {k.lower(): v for k, v in raw.items()}
    result = {"AncestorID": raw.get("AncestorID"), "AnimalID": raw.get("AnimalID")}
    for key in canonical:
        result[key] = ci_map.get(key.lower())

    try:
        animal = db.execute(
            text("SELECT SpeciesID, PercentPeruvian, PercentChilean, PercentBolivian, PercentUnknownOther, PercentAccoyo FROM Animals WHERE AnimalID = :aid"),
            {"aid": animal_id}
        ).fetchone()
        if animal:
            result["SpeciesID"] = animal.SpeciesID
            result["PercentPeruvian"] = animal.PercentPeruvian or ""
            result["PercentChilean"] = animal.PercentChilean or ""
            result["PercentBolivian"] = animal.PercentBolivian or ""
            result["PercentUnknownOther"] = animal.PercentUnknownOther or ""
            result["PercentAccoyo"] = animal.PercentAccoyo or ""
    except Exception:
        db.rollback()
        animal = db.execute(text("SELECT SpeciesID FROM Animals WHERE AnimalID = :aid"), {"aid": animal_id}).fetchone()
        result["SpeciesID"] = animal.SpeciesID if animal else None
        for pf in ("PercentPeruvian","PercentChilean","PercentBolivian","PercentUnknownOther","PercentAccoyo"):
            result[pf] = ""
    return result


# ─── POST update ancestry ─────────────────────────────────────────────────────

@router.post("/{animal_id}/update-ancestry")
async def update_ancestry(animal_id: int, request: Request,
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    body = await request.json()
    f = lambda k: body.get(k) or None

    existing = db.execute(text("SELECT COUNT(*) FROM Ancestors WHERE AnimalID = :aid"), {"aid": animal_id}).scalar()
    fields = [
        "Sire","SireColor","SireLink","SireARI",
        "SireSire","SireSireColor","SireSireLink","SireSireARI",
        "SireSireSire","SireSireSireColor","SireSireSireLink","SireSireSireARI",
        "SireSireDam","SireSireDamColor","SireSireDamLink","SireSireDamARI",
        "SireDam","SireDamColor","SireDamLink","SireDamARI",
        "SireDamSire","SireDamSireColor","SireDamSireLink","SireDamSireARI",
        "SireDamDam","SireDamDamColor","SireDamDamLink","SireDamDamARI",
        "Dam","DamColor","DamLink","DamARI",
        "DamSire","DamSireColor","DamSireLink","DamSireARI",
        "DamSireSire","DamSireSireColor","DamSireSireLink","DamSireSireARI",
        "DamSireDam","DamSireDamColor","DamSireDamLink","DamSireDamARI",
        "DamDam","DamDamColor","DamDamLink","DamDamARI",
        "DamDamSire","DamDamSireColor","DamDamSireLink","DamDamSireARI",
        "DamDamDam","DamDamDamColor","DamDamDamLink","DamDamDamARI",
    ]
    params = {"aid": animal_id}
    params.update({fld: f(fld) for fld in fields})
    set_clause = ", ".join([f"{fld} = :{fld}" for fld in fields])

    if existing:
        db.execute(text(f"UPDATE Ancestors SET {set_clause} WHERE AnimalID = :aid"), params)
    else:
        cols = ", ".join(["AnimalID"] + fields)
        vals = ", ".join([":aid"] + [f":{fld}" for fld in fields])
        db.execute(text(f"INSERT INTO Ancestors ({cols}) VALUES ({vals})"), params)

    db.commit()

    # Save bloodline percentages to Animals table (alpaca-specific; only if columns exist)
    pct_fields = ["PercentPeruvian", "PercentChilean", "PercentBolivian", "PercentUnknownOther", "PercentAccoyo"]
    try:
        pct_params = {"aid": animal_id}
        pct_params.update({pf: body.get(pf) or None for pf in pct_fields})
        db.execute(
            text("UPDATE Animals SET " + ", ".join(f"{pf} = :{pf}" for pf in pct_fields) + " WHERE AnimalID = :aid"),
            pct_params
        )
        db.commit()
    except Exception:
        db.rollback()

    return {"message": "Ancestry updated"}


# ─── GET fiber ────────────────────────────────────────────────────────────────

@router.get("/{animal_id}/fiber")
def get_fiber(animal_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT FiberID, SampleDateYear, SampleDateMonth, SampleDateDay,
               Average, CF, StandardDev, CrimpPerInch, COV, Length,
               GreaterThan30, ShearWeight, Curve, BlanketWeight
        FROM Fiber WHERE AnimalID = :aid
        ORDER BY SampleDateYear DESC, Average DESC
    """), {"aid": animal_id}).fetchall()
    return [_row(r) for r in rows]


# ─── POST update fiber ────────────────────────────────────────────────────────

@router.post("/{animal_id}/update-fiber")
async def update_fiber(animal_id: int, request: Request,
                        db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    rows = await request.json()
    n = _nullable_float

    for row in rows:
        fiber_id = row.get("FiberID")
        params = {
            "aid": animal_id,
            "year": _nullable_int(row.get("SampleDateYear")),
            "avg": n(row.get("Average")), "cf": n(row.get("CF")),
            "sd": n(row.get("StandardDev")), "cpi": n(row.get("CrimpPerInch")),
            "cov": n(row.get("COV")), "length": n(row.get("Length")),
            "gt30": n(row.get("GreaterThan30")), "sw": n(row.get("ShearWeight")),
            "curve": n(row.get("Curve")), "bw": n(row.get("BlanketWeight")),
        }
        if fiber_id:
            params["fid"] = fiber_id
            db.execute(text("""
                UPDATE Fiber SET
                    SampleDateYear=:year, Average=:avg, CF=:cf,
                    StandardDev=:sd, CrimpPerInch=:cpi, COV=:cov, Length=:length,
                    GreaterThan30=:gt30, ShearWeight=:sw, Curve=:curve, BlanketWeight=:bw
                WHERE FiberID=:fid AND AnimalID=:aid
            """), params)
        else:
            if params["year"] or params["avg"]:
                db.execute(text("""
                    INSERT INTO Fiber (AnimalID, SampleDateYear, Average, CF,
                        StandardDev, CrimpPerInch, COV, Length,
                        GreaterThan30, ShearWeight, Curve, BlanketWeight)
                    VALUES (:aid, :year, :avg, :cf, :sd, :cpi, :cov, :length,
                        :gt30, :sw, :curve, :bw)
                """), params)

    db.commit()
    return {"message": "Fiber updated"}


# ─── GET awards ───────────────────────────────────────────────────────────────

@router.get("/{animal_id}/awards")
def get_awards(animal_id: int, db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT AwardsID, AwardYear, ShowName, Type, Placing, Awardcomments
        FROM awards
        WHERE AnimalID = :aid
          AND (LEN(ISNULL(Placing,'')) > 0 OR LEN(ISNULL(Class,'')) > 0
               OR LEN(ISNULL(AwardYear,'')) > 1 OR LEN(ISNULL(Awardcomments,'')) > 0
               OR LEN(ISNULL(ShowName,'')) > 0)
        ORDER BY AwardYear DESC, Placing DESC
    """), {"aid": animal_id}).fetchall()
    return [_row(r) for r in rows]


# ─── POST update awards ───────────────────────────────────────────────────────

@router.post("/{animal_id}/update-awards")
async def update_awards(animal_id: int, request: Request,
                         db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    rows = await request.json()

    for row in rows:
        awards_id = row.get("AwardsID")
        params = {
            "aid": animal_id,
            "year": row.get("AwardYear") or None,
            "show": row.get("ShowName") or None,
            "aclass": row.get("Type") or None,
            "placing": row.get("Placing") or None,
            "comments": row.get("Awardcomments") or None,
        }
        if awards_id:
            params["awid"] = awards_id
            db.execute(text("""
                UPDATE awards SET
                    AwardYear=:year, ShowName=:show, Type=:aclass,
                    Placing=:placing, Awardcomments=:comments
                WHERE AwardsID=:awid AND AnimalID=:aid
            """), params)
        else:
            db.execute(text("""
                INSERT INTO awards (AnimalID, AwardYear, ShowName, Type, Placing, Awardcomments)
                VALUES (:aid, :year, :show, :aclass, :placing, :comments)
            """), params)

    db.commit()
    return {"message": "Awards updated"}


# ─── GET photos ──────────────────────────────────────────────────────────────

@router.get("/{animal_id}/photos")
def get_photos(animal_id: int, db: Session = Depends(get_db)):
    row = db.execute(text(
        "SELECT Photo1,Photo2,Photo3,Photo4,Photo5,Photo6,Photo7,Photo8,"
        "PhotoCaption1,PhotoCaption2,PhotoCaption3,PhotoCaption4,"
        "PhotoCaption5,PhotoCaption6,PhotoCaption7,PhotoCaption8,"
        "ListPageImage, ARI, Histogram FROM Photos WHERE AnimalID = :aid"
    ), {"aid": animal_id}).fetchone()
    if not row:
        return {"photos": [None] * 8, "captions": [None] * 8, "list_page_image": None,
                "registration_url": None, "histogram_url": None}
    photos   = [getattr(row, slot) or None for slot in _PHOTO_SLOTS]
    captions = [getattr(row, slot) or None for slot in _CAPTION_SLOTS]
    return {
        "photos": photos,
        "captions": captions,
        "list_page_image": row.ListPageImage,
        "registration_url": row.ARI,
        "histogram_url": row.Histogram,
    }


# ─── POST upload photo ────────────────────────────────────────────────────────

@router.post("/{animal_id}/photos/upload")
async def upload_photo(animal_id: int, file: UploadFile = File(...),
                        slot: int = 1,
                        db: Session = Depends(get_db),
                        current_user=Depends(get_current_user)):
    if slot < 1 or slot > 8:
        raise HTTPException(status_code=400, detail="slot must be 1–8")
    file_bytes = await file.read()
    url = _upload_animal_photo(file_bytes, file.filename or "photo.webp")
    col = f"Photo{slot}"

    existing = db.execute(text("SELECT COUNT(*) FROM Photos WHERE AnimalID=:aid"),
                          {"aid": animal_id}).scalar()
    if existing:
        db.execute(text(f"UPDATE Photos SET {col}=:url WHERE AnimalID=:aid"),
                   {"url": url, "aid": animal_id})
    else:
        db.execute(text(f"INSERT INTO Photos (AnimalID, {col}) VALUES (:aid, :url)"),
                   {"aid": animal_id, "url": url})
    db.commit()
    return {"url": url, "slot": slot}


# ─── DELETE photo slot ─────────────────────────────────────────────────────────

@router.post("/{animal_id}/photos/delete-slot")
async def delete_photo_slot(animal_id: int, request: Request,
                             db: Session = Depends(get_db),
                             current_user=Depends(get_current_user)):
    body = await request.json()
    slot = int(body.get("slot", 0))
    if slot < 1 or slot > 8:
        raise HTTPException(status_code=400, detail="slot must be 1–8")
    col = f"Photo{slot}"
    db.execute(text(f"UPDATE Photos SET {col}=NULL WHERE AnimalID=:aid"),
               {"aid": animal_id})
    db.commit()
    return {"deleted": slot}


# ─── POST set list page image ─────────────────────────────────────────────────

@router.post("/{animal_id}/photos/set-cover")
async def set_cover_photo(animal_id: int, request: Request,
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    body = await request.json()
    url  = body.get("url")
    existing = db.execute(text("SELECT COUNT(*) FROM Photos WHERE AnimalID=:aid"),
                          {"aid": animal_id}).scalar()
    if existing:
        db.execute(text("UPDATE Photos SET ListPageImage=:url WHERE AnimalID=:aid"),
                   {"url": url, "aid": animal_id})
    else:
        db.execute(text("INSERT INTO Photos (AnimalID, ListPageImage) VALUES (:aid, :url)"),
                   {"aid": animal_id, "url": url})
    db.commit()
    return {"list_page_image": url}


# ─── POST reorder photos ──────────────────────────────────────────────────────

@router.post("/{animal_id}/photos/reorder")
async def reorder_photos(animal_id: int, request: Request,
                          db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    body = await request.json()
    urls = body.get("urls", [])
    if len(urls) != 8:
        raise HTTPException(status_code=400, detail="urls must have exactly 8 entries")
    captions = body.get("captions", [None] * 8)
    if len(captions) != 8:
        captions = [None] * 8
    sets = ", ".join([f"Photo{i+1}=:p{i}, PhotoCaption{i+1}=:c{i}" for i in range(8)])
    params = {f"p{i}": urls[i] for i in range(8)}
    params.update({f"c{i}": captions[i] or None for i in range(8)})
    params["aid"] = animal_id
    existing = db.execute(text("SELECT COUNT(*) FROM Photos WHERE AnimalID=:aid"),
                          {"aid": animal_id}).scalar()
    if existing:
        db.execute(text(f"UPDATE Photos SET {sets} WHERE AnimalID=:aid"), params)
    else:
        cols = ", ".join([f"Photo{i+1}, PhotoCaption{i+1}" for i in range(8)])
        vals = ", ".join([f":p{i}, :c{i}" for i in range(8)])
        db.execute(text(f"INSERT INTO Photos (AnimalID, {cols}) VALUES (:aid, {vals})"), params)
    db.commit()
    return {"reordered": True}


# ─── POST save captions ───────────────────────────────────────────────────────

@router.post("/{animal_id}/photos/captions")
async def save_captions(animal_id: int, request: Request,
                         db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    body = await request.json()
    captions = body.get("captions", [])
    if len(captions) != 8:
        raise HTTPException(status_code=400, detail="captions must have exactly 8 entries")
    sets = ", ".join([f"PhotoCaption{i+1}=:c{i}" for i in range(8)])
    params = {f"c{i}": captions[i] or None for i in range(8)}
    params["aid"] = animal_id
    existing = db.execute(text("SELECT COUNT(*) FROM Photos WHERE AnimalID=:aid"),
                          {"aid": animal_id}).scalar()
    if existing:
        db.execute(text(f"UPDATE Photos SET {sets} WHERE AnimalID=:aid"), params)
    else:
        cols = ", ".join([f"PhotoCaption{i+1}" for i in range(8)])
        vals = ", ".join([f":c{i}" for i in range(8)])
        db.execute(text(f"INSERT INTO Photos (AnimalID, {cols}) VALUES (:aid, {vals})"), params)
    db.commit()
    return {"saved": True}


# ─── POST upload document (registration cert / histogram) ────────────────────

@router.post("/{animal_id}/documents/upload")
async def upload_document(animal_id: int,
                           file: UploadFile = File(...),
                           kind: str = "registration",
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    col = _DOC_COLUMNS.get(kind)
    if not col:
        raise HTTPException(status_code=400, detail="kind must be 'registration' or 'histogram'")
    file_bytes = await file.read()
    url = _upload_animal_document(file_bytes, file.filename or f"{kind}.pdf")

    existing = db.execute(text("SELECT COUNT(*) FROM Photos WHERE AnimalID=:aid"),
                          {"aid": animal_id}).scalar()
    if existing:
        db.execute(text(f"UPDATE Photos SET {col}=:url WHERE AnimalID=:aid"),
                   {"url": url, "aid": animal_id})
    else:
        db.execute(text(f"INSERT INTO Photos (AnimalID, {col}) VALUES (:aid, :url)"),
                   {"aid": animal_id, "url": url})
    db.commit()
    return {"url": url, "kind": kind}


# ─── POST delete document ────────────────────────────────────────────────────

@router.post("/{animal_id}/documents/delete")
async def delete_document(animal_id: int, request: Request,
                           db: Session = Depends(get_db),
                           current_user=Depends(get_current_user)):
    body = await request.json()
    kind = body.get("kind")
    col = _DOC_COLUMNS.get(kind)
    if not col:
        raise HTTPException(status_code=400, detail="kind must be 'registration' or 'histogram'")
    db.execute(text(f"UPDATE Photos SET {col}=NULL WHERE AnimalID=:aid"),
               {"aid": animal_id})
    db.commit()
    return {"deleted": kind}


# ─── POST publish toggle ──────────────────────────────────────────────────────

@router.post("/{animal_id}/publish")
async def toggle_publish(animal_id: int, request: Request,
                          db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    body = await request.json()
    val = 1 if body.get("publish") else 0
    if val:
        assert_can_publish(db, business_id_for_animal(db, animal_id), "for_sale", animal_id)
    db.execute(text("UPDATE Animals SET PublishForSale = :v, LastUpdated = SYSUTCDATETIME() WHERE AnimalID = :aid"),
               {"v": val, "aid": animal_id})
    db.commit()
    return {"published": bool(val)}


# ─── POST publish-stud toggle ─────────────────────────────────────────────────

@router.post("/{animal_id}/publish-stud")
async def toggle_publish_stud(animal_id: int, request: Request,
                               db: Session = Depends(get_db),
                               current_user=Depends(get_current_user)):
    body = await request.json()
    val = 1 if body.get("publish") else 0
    if val:
        assert_can_publish(db, business_id_for_animal(db, animal_id), "stud", animal_id)
    db.execute(text("UPDATE Animals SET PublishStud = :v, LastUpdated = SYSUTCDATETIME() WHERE AnimalID = :aid"),
               {"v": val, "aid": animal_id})
    db.commit()
    return {"published": bool(val)}


# ─── DELETE animal ────────────────────────────────────────────────────────────

@router.delete("/{animal_id}")
async def delete_animal(animal_id: int,
                         db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    # Verify the current user has access to the animal's business
    row = db.execute(
        text("SELECT a.AnimalID FROM Animals a "
             "JOIN BusinessAccess ba ON ba.BusinessID = a.BusinessID "
             "WHERE a.AnimalID = :aid AND ba.PeopleID = :pid AND ba.Active = 1"),
        {"aid": animal_id, "pid": current_user.PeopleID}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Animal not found or access denied")

    # Delete related records first, then the animal
    for tbl in ("Photos", "Pricing", "AnimalRegistration", "Awards", "Ancestry"):
        try:
            db.execute(text(f"DELETE FROM {tbl} WHERE AnimalID = :aid"), {"aid": animal_id})
        except Exception:
            pass  # table may not exist for this animal
    db.execute(text("DELETE FROM Animals WHERE AnimalID = :aid"), {"aid": animal_id})
    db.commit()
    return {"deleted": animal_id}


@router.post("/{animal_id}/transfer")
async def transfer_animal(animal_id: int, request: Request,
                          db: Session = Depends(get_db),
                          current_user=Depends(get_current_user)):
    """Move an animal to another business the caller also has access to.

    Both ends are checked against BusinessAccess, the same way delete_animal
    verifies ownership, so an animal can only be moved between businesses the
    caller actually holds. Handing an animal to a third party is a different
    feature — it needs the receiving side to accept — and is not this endpoint.
    """
    body = await request.json()
    to_business_id = body.get("ToBusinessID")
    try:
        to_business_id = int(to_business_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="ToBusinessID is required.")

    # The caller must hold the animal's current business.
    row = db.execute(
        text("SELECT a.BusinessID FROM Animals a "
             "JOIN BusinessAccess ba ON ba.BusinessID = a.BusinessID "
             "WHERE a.AnimalID = :aid AND ba.PeopleID = :pid AND ba.Active = 1"),
        {"aid": animal_id, "pid": current_user.PeopleID}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Animal not found or access denied")

    from_business_id = row.BusinessID
    if from_business_id == to_business_id:
        raise HTTPException(status_code=400, detail="That animal is already with this business.")

    # ...and the business it is going to.
    dest = db.execute(
        text("SELECT b.BusinessID, b.BusinessName FROM Business b "
             "JOIN BusinessAccess ba ON ba.BusinessID = b.BusinessID "
             "WHERE b.BusinessID = :bid AND ba.PeopleID = :pid AND ba.Active = 1"),
        {"bid": to_business_id, "pid": current_user.PeopleID}
    ).fetchone()
    if not dest:
        raise HTTPException(status_code=403, detail="You do not have access to that business.")

    db.execute(
        text("UPDATE Animals SET BusinessID = :to WHERE AnimalID = :aid"),
        {"to": to_business_id, "aid": animal_id}
    )
    db.commit()
    return {
        "AnimalID": animal_id,
        "FromBusinessID": from_business_id,
        "ToBusinessID": to_business_id,
        "ToBusinessName": dest.BusinessName,
    }
