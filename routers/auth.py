from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from auth import create_access_token, get_current_user, hash_password, verify_password, verify_password_reset_token, create_password_reset_token
import models
from sqlalchemy import select, text

router = APIRouter(prefix="/auth", tags=["auth"])

# -------------------------
# Pydantic models
# -------------------------
class LoginRequest(BaseModel):
    Email: str
    Password: str

class SignupRequest(BaseModel):
    PeopleFirstName: str
    PeopleLastName: str
    Email: str
    Password: str

class ForgotPasswordRequest(BaseModel):
    Email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class UpdateLoginRequest(BaseModel):
    first_name: str = None
    last_name: str = None
    email: str = None
    current_password: str = None
    new_password: str = None


# -------------------------
# Public site settings (no auth required — login/signup pages need this)
# -------------------------
@router.get("/site-settings")
def get_site_settings(db: Session = Depends(get_db)):
    settings = db.query(models.SiteSettings).filter(models.SiteSettings.id == 1).first()
    if not settings:
        # Row missing — return safe defaults (open)
        return {"team_only_login": False, "signup_open": True}
    return {
        "team_only_login": bool(settings.team_only_login),
        "signup_open": bool(settings.signup_open),
    }


# -------------------------
# Signup
# -------------------------
@router.post("/signup")
def signup(request: SignupRequest, db: Session = Depends(get_db)):
    from datetime import datetime

    # Check if signup is currently open
    settings = db.query(models.SiteSettings).filter(models.SiteSettings.id == 1).first()
    if settings and not settings.signup_open:
        raise HTTPException(status_code=403, detail="Registration is currently closed.")

    email = request.Email.strip().lower()

    existing = db.query(models.People).filter(
        models.People.PeopleEmail == email
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with that email already exists.")

    new_user = models.People(
        PeopleFirstName=request.PeopleFirstName.strip(),
        PeopleLastName=request.PeopleLastName.strip(),
        PeopleEmail=email,
        PeoplePassword=hash_password(request.Password),
        PeopleActive=1,
        accesslevel=0,
        Subscriptionlevel=0,
        PeopleCreationDate=datetime.utcnow(),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    token = create_access_token(data={"sub": str(new_user.PeopleID)})

    return {
        "AccessToken": token,
        "token_type": "bearer",
        "PeopleID": new_user.PeopleID,
        "PeopleFirstName": new_user.PeopleFirstName,
        "PeopleLastName": new_user.PeopleLastName,
        "AccessLevel": new_user.accesslevel or 0,
    }


# -------------------------
# Forgot password  (sends a reset link — never exposes the stored hash)
# -------------------------
@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    import os, sendgrid
    from sendgrid.helpers.mail import Mail

    email = body.Email.strip().lower()

    user = db.query(models.People).filter(
        models.People.PeopleEmail == email
    ).first()

    # Always return the same response to avoid user enumeration
    if not user:
        return {"message": "If that email is registered you will receive a reset link.", "email": email}

    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
    FROM_EMAIL       = os.getenv("FROM_EMAIL", "john@oatmeal-ai.com")
    SITE_NAME        = os.getenv("SITE_NAME", "Livestock Of America")
    FRONTEND_URL     = os.getenv("FRONTEND_URL", "https://livestockofamerica.com")

    if not SENDGRID_API_KEY:
        raise HTTPException(status_code=503, detail="Email service not configured.")

    reset_token = create_password_reset_token(user.PeopleID)
    reset_link  = f"{FRONTEND_URL}/reset-password?token={reset_token}"

    html_body = f"""
    <font face="arial">
    Dear {user.PeopleFirstName},<br><br>
    We received a request to reset your {SITE_NAME} password.
    Click the link below to choose a new password. This link expires in 1 hour.<br><br>
    <a href="{reset_link}">Reset my password</a><br><br>
    If you did not request this, you can safely ignore this email.<br><br>
    Sincerely,<br><br>
    {SITE_NAME}
    </font>
    """

    try:
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        sg.send(Mail(
            from_email=FROM_EMAIL,
            to_emails=email,
            subject=f"Reset your {SITE_NAME} password",
            html_content=html_body,
        ))
    except Exception:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to send email. Please try again.")

    return {"message": "If that email is registered you will receive a reset link.", "email": email}


# -------------------------
# Reset password  (accepts the token from the reset email)
# -------------------------
@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    people_id = verify_password_reset_token(body.token)

    user = db.query(models.People).filter(models.People.PeopleID == people_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.PeoplePassword = hash_password(body.new_password)
    db.commit()
    return {"message": "Password has been reset. You can now log in."}


# -------------------------
# Login
# -------------------------
@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = db.query(models.People).filter(
            models.People.PeopleEmail == request.Email,
            models.People.PeopleActive == 1
        ).first()
        if not user or not verify_password(request.Password, user.PeoplePassword or ""):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password"
            )

        # If team-only login is active, reject accounts with accesslevel < 1
        settings = db.query(models.SiteSettings).filter(models.SiteSettings.id == 1).first()
        if settings and settings.team_only_login and (user.accesslevel or 0) < 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access is currently restricted to team members only."
            )

        token = create_access_token(data={"sub": str(user.PeopleID)})

        try:
            db.execute(text("UPDATE People SET OFNLastAccess = GETDATE() WHERE PeopleID = :id"), {"id": user.PeopleID})
            db.commit()
        except Exception:
            pass

        return {
            "AccessToken": token,
            "token_type": "bearer",
            "PeopleID": user.PeopleID,
            "PeopleFirstName": user.PeopleFirstName,
            "PeopleLastName": user.PeopleLastName,
            "AccessLevel": user.accesslevel or 0,
            "LKMAccessLevel": getattr(user, 'LKMAccessLevel', 0) or 0
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise


# -------------------------
# Get current user
# -------------------------
@router.get("/me")
def get_me(current_user=Depends(get_current_user)):
    return {
        "PeopleID": current_user.PeopleID,
        "PeopleFirstName": current_user.PeopleFirstName,
        "PeopleLastName": current_user.PeopleLastName,
        "PeopleEmail": current_user.PeopleEmail,
        "AccessLevel": current_user.accesslevel
    }


# -------------------------
# Update login info
# -------------------------
@router.put("/update-login")
def update_login(payload: UpdateLoginRequest, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(models.People).filter(models.People.PeopleID == current_user.PeopleID).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.new_password:
        if not payload.current_password or not verify_password(payload.current_password, user.PeoplePassword or ""):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        user.PeoplePassword = hash_password(payload.new_password)

    if payload.email and payload.email.strip().lower() != user.PeopleEmail:
        existing = db.query(models.People).filter(
            models.People.PeopleEmail == payload.email.strip().lower(),
            models.People.PeopleID != user.PeopleID
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="That email is already in use by another account")
        user.PeopleEmail = payload.email.strip().lower()

    if payload.first_name is not None:
        user.PeopleFirstName = payload.first_name.strip()
    if payload.last_name is not None:
        user.PeopleLastName = payload.last_name.strip()

    db.commit()
    return {
        "message": "Settings updated successfully",
        "PeopleFirstName": user.PeopleFirstName,
        "PeopleLastName": user.PeopleLastName,
        "PeopleEmail": user.PeopleEmail,
    }


# -------------------------
# My businesses
# -------------------------
@router.get("/my-businesses")
def GetMyBusinesses(PeopleID: int, Db: Session = Depends(get_db)):
    rows = (
        Db.query(models.Business, models.Address, models.BusinessTypeLookup)
        .join(models.BusinessAccess, models.Business.BusinessID == models.BusinessAccess.BusinessID)
        .outerjoin(models.Address, models.Business.AddressID == models.Address.AddressID)
        .outerjoin(models.BusinessTypeLookup, models.Business.BusinessTypeID == models.BusinessTypeLookup.BusinessTypeID)
        .filter(
            models.BusinessAccess.PeopleID == PeopleID,
            models.BusinessAccess.Active == 1
        )
        .all()
    )

    # Prefer a favorite scoped to (PeopleID, BusinessID); fall back to the
    # PeopleID-level favorite (BusinessID IS NULL) so older rows still surface.
    fav_rows = Db.execute(
        text("""
            SELECT am.BusinessID, am.AssociationID, a.AssociationName
            FROM associationmembers am
            LEFT JOIN associations a ON a.AssociationID = am.AssociationID
            WHERE am.PeopleID = :pid AND am.Favorite = 1
        """),
        {"pid": PeopleID},
    ).mappings().all()

    fav_by_biz = {}
    fav_global = None
    for fr in fav_rows:
        if fr["BusinessID"] is not None:
            fav_by_biz[fr["BusinessID"]] = (fr["AssociationID"], fr["AssociationName"])
        elif fav_global is None:
            fav_global = (fr["AssociationID"], fr["AssociationName"])

    result = []
    for B, A, BT in rows:
        fav = fav_by_biz.get(B.BusinessID) or fav_global
        result.append({
            "BusinessID": B.BusinessID,
            "BusinessName": B.BusinessName,
            "BusinessTypeID": B.BusinessTypeID,
            "BusinessType":   BT.BusinessType if BT else None,
            "AddressCity":    A.AddressCity    if A else None,
            "AddressState":   A.AddressState   if A else None,
            "AddressZip":     A.AddressZip     if A else None,
            "AddressCountry": A.AddressCountry if A else None,
            "FavoriteAssociationID":   fav[0] if fav else None,
            "FavoriteAssociationName": fav[1] if fav else None,
        })
    return result


# -------------------------
# Account home
# -------------------------
@router.get("/account-home")
def GetAccountHome(BusinessID: int, Db: Session = Depends(get_db)):
    Result = (
        Db.query(
            models.Business,
            models.BusinessTypeLookup,
            models.Address,
        )
        .outerjoin(models.BusinessTypeLookup, models.Business.BusinessTypeID == models.BusinessTypeLookup.BusinessTypeID)
        .outerjoin(models.Address, models.Business.AddressID == models.Address.AddressID)
        .filter(models.Business.BusinessID == BusinessID)
        .first()
    )

    if not Result:
        raise HTTPException(status_code=404, detail="Business not found")

    B, BT, A = Result

    try:
        sr = Db.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM Field               WHERE BusinessID=:b)                                                                           AS fields,
              (SELECT COUNT(*) FROM Animals             WHERE BusinessID=:b)                                                                           AS animals,
              (SELECT COUNT(*) FROM MarketplaceOrderItems WHERE SellerBusinessID=:b AND SellerStatus IN ('pending','confirmed','processing'))           AS pending_orders,
              (SELECT COUNT(*) FROM OFNEvents WHERE BusinessID=:b AND IsPublished=1 AND (EventStartDate IS NULL OR EventStartDate >= CAST(GETDATE() AS DATE))) AS upcoming_events,
              (SELECT COUNT(*) FROM blog                WHERE BusinessID=:b AND IsPublished=1)                                                         AS blog_posts,
              (SELECT COUNT(*) FROM SFProducts          WHERE BusinessID=:b AND Publishproduct=1)                                                      AS products,
              (SELECT COUNT(*) FROM Services            WHERE BusinessID=:b)                                                                           AS services,
              (SELECT COUNT(*) FROM Produce             WHERE BusinessID=:b AND IsActive=1)                                                            AS produce,
              (CASE WHEN EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='OFNAggregatorB2BOrder')
                    THEN (SELECT COUNT(*) FROM OFNAggregatorB2BOrder WHERE BusinessID=:b AND Status IN ('placed','confirmed','picking','dispatched'))
                    ELSE 0 END)                                                                                                                        AS aggregator_b2b_open,
              (CASE WHEN EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='OFNAggregatorFarm')
                    THEN (SELECT COUNT(*) FROM OFNAggregatorFarm WHERE BusinessID=:b AND Status='active')
                    ELSE 0 END)                                                                                                                        AS aggregator_farms
        """), {"b": BusinessID}).fetchone()
        stats = {
            "fields":              sr.fields              or 0,
            "animals":             sr.animals             or 0,
            "pending_orders":      sr.pending_orders      or 0,
            "upcoming_events":     sr.upcoming_events     or 0,
            "blog_posts":          sr.blog_posts          or 0,
            "products":            sr.products            or 0,
            "services":            sr.services            or 0,
            "produce":             sr.produce             or 0,
            "aggregator_b2b_open": sr.aggregator_b2b_open or 0,
            "aggregator_farms":    sr.aggregator_farms    or 0,
        }
    except Exception:
        stats = {"fields": 0, "animals": 0, "pending_orders": 0, "upcoming_events": 0,
                 "blog_posts": 0, "products": 0, "services": 0, "produce": 0,
                 "aggregator_b2b_open": 0, "aggregator_farms": 0}

    return {
        "BusinessID": B.BusinessID,
        "BusinessName": B.BusinessName,
        "BusinessEmail": B.BusinessEmail,
        "BusinessTypeID": BT.BusinessTypeID if BT else None,
        "BusinessType": BT.BusinessType if BT else None,
        "SubscriptionLevel": B.SubscriptionLevel,
        "SubscriptionEndDate": str(B.SubscriptionEndDate) if hasattr(B, 'SubscriptionEndDate') else None,
        "AddressCity": A.AddressCity if A else None,
        "AddressState": A.AddressState if A else None,
        "AddressStreet": A.AddressStreet if A else None,
        "AddressZip": A.AddressZip if A else None,
        "stats": stats,
    }


# -------------------------
# Business types
# -------------------------
@router.get("/business-types")
def GetBusinessTypes(Db: Session = Depends(get_db)):
    Types = Db.query(models.BusinessTypeLookup).order_by(models.BusinessTypeLookup.BusinessType).all()
    return [{"BusinessTypeID": T.BusinessTypeID, "BusinessType": T.BusinessType} for T in Types]


@router.put("/change-business-type")
def ChangeBusinessType(BusinessID: int, BusinessTypeID: int, Db: Session = Depends(get_db)):
    Business = Db.query(models.Business).filter(models.Business.BusinessID == BusinessID).first()
    if not Business:
        raise HTTPException(status_code=404, detail="Business not found")
    Business.BusinessTypeID = BusinessTypeID
    Db.commit()
    return {"status": "success"}


# -------------------------
# Animals endpoint (optimized)
# -------------------------
@router.get("/animals")
def GetAnimals(BusinessID: int, Db: Session = Depends(get_db)):
    rows = Db.execute(text("""
        SELECT
            a.AnimalID,
            a.FullName,
            a.SpeciesID,
            a.PublishForSale,
            a.PublishStud,
            p.Price,
            p.StudFee,
            p.SalePrice,
            sa.PluralTerm AS SpeciesName,
            sc.SpeciesCategory AS CategoryName,
            sc.SpeciesCategoryOrder
        FROM Animals a
        LEFT JOIN SpeciesAvailable sa ON sa.SpeciesID = a.SpeciesID
        LEFT JOIN Pricing p ON p.AnimalID = a.AnimalID
        LEFT JOIN SpeciesCategory sc ON sc.SpeciesCategoryID = a.SpeciesCategoryID
        WHERE a.BusinessID = :bid
        ORDER BY sa.PluralTerm, sc.SpeciesCategoryOrder, a.FullName
    """), {"bid": BusinessID}).fetchall()

    return [
        {
            "AnimalID": r.AnimalID,
            "FullName": r.FullName,
            "SpeciesID": r.SpeciesID,
            "SpeciesName": r.SpeciesName or "Unknown",
            "Category": r.CategoryName or "",
            "Price": float(r.Price) if r.Price else 0,
            "StudFee": float(r.StudFee) if r.StudFee else 0,
            "SalePrice": float(r.SalePrice) if r.SalePrice else 0,
            "PublishForSale": r.PublishForSale,
            "PublishStud": r.PublishStud,
        }
        for r in rows
    ]


# -------------------------
# Species list
# -------------------------
@router.get("/species")
def get_species_list(db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT MIN(SpeciesID) AS SpeciesID, MIN(SingularTerm) AS SingularTerm, PluralTerm "
        "FROM SpeciesAvailable GROUP BY PluralTerm ORDER BY PluralTerm"
    )).fetchall()
    return [{"id": r.SpeciesID, "singular": r.SingularTerm, "plural": r.PluralTerm} for r in rows]


# -------------------------
# Species breeds
# -------------------------
@router.get("/species/{species_id}/breeds")
def get_species_breeds(species_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import text
    try:
        rows = db.execute(
            text("SELECT BreedLookupID, Breed FROM SpeciesBreedLookupTable WHERE SpeciesID = :sid AND LEFT(Breed,1) LIKE '[A-Z]' ORDER BY Breed"),
            {"sid": species_id}
        ).fetchall()
        return [{"id": r.BreedLookupID, "name": r.Breed} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Species registration types
# -------------------------
@router.get("/species/{species_id}/registration-types")
def get_registration_types(species_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import text
    try:
        rows = db.execute(
            text("SELECT SpeciesRegistrationType FROM SpeciesRegistrationTypeLookupTable "
                 "WHERE SpeciesID = :sid ORDER BY SpeciesRegistrationType"),
            {"sid": species_id}
        ).fetchall()
        return [{"type": r.SpeciesRegistrationType} for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Add animal
# -------------------------
def _upload_animal_photo(file_bytes: bytes, original_filename: str) -> str:
    """Upload a single animal photo to GCS Animals/ and return its public URL."""
    import uuid, os
    from urllib.parse import quote
    from google.cloud import storage as _gcs
    ext  = os.path.splitext(original_filename)[1].lower() or ".webp"
    fname = f"{uuid.uuid4().hex}{ext}"
    bucket = _gcs.Client().bucket("oatmeal-farm-network-images")
    blob   = bucket.blob(f"Animals/{fname}")
    ct_map = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    blob.upload_from_string(file_bytes, content_type=ct_map.get(ext, "image/webp"))
    return f"https://storage.googleapis.com/oatmeal-farm-network-images/Animals/{quote(fname, safe='')}"


def _upload_animal_doc(file_bytes: bytes, original_filename: str) -> str:
    """Upload a single animal document (PDF or image) to GCS AnimalDocs/."""
    import uuid, os
    from urllib.parse import quote
    from google.cloud import storage as _gcs
    ext  = os.path.splitext(original_filename)[1].lower() or ".pdf"
    fname = f"{uuid.uuid4().hex}{ext}"
    bucket = _gcs.Client().bucket("oatmeal-farm-network-images")
    blob   = bucket.blob(f"AnimalDocs/{fname}")
    ct_map = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".png": "image/png", ".webp": "image/webp"}
    blob.upload_from_string(file_bytes, content_type=ct_map.get(ext, "application/octet-stream"))
    return f"https://storage.googleapis.com/oatmeal-farm-network-images/AnimalDocs/{quote(fname, safe='')}"


# ── People search (for testimonials etc.) ─────────────────────────────────────

@router.get("/people/search")
def search_people(q: str, db: Session = Depends(get_db)):
    from sqlalchemy import text
    if not q or len(q.strip()) < 2:
        return []
    term = f"%{q.strip()}%"
    rows = db.execute(text("""
        SELECT TOP 20
            p.PeopleID,
            p.PeopleFirstName,
            p.PeopleLastName,
            a.AddressCity  AS City,
            a.AddressState AS State,
            b.BusinessID,
            b.BusinessName,
            b.BusinessBlog AS ExternalWebsite,
            bw.Slug        AS OFNSlug
        FROM People p
        LEFT JOIN Address a ON a.AddressID = p.AddressID
        LEFT JOIN BusinessAccess ba ON ba.PeopleID = p.PeopleID AND ba.Active = 1
        LEFT JOIN Business b ON b.BusinessID = ba.BusinessID
        LEFT JOIN BusinessWebsite bw ON bw.BusinessID = b.BusinessID
        WHERE p.PeopleActive = 1
          AND (p.PeopleFirstName LIKE :q OR p.PeopleLastName LIKE :q
               OR (p.PeopleFirstName + ' ' + p.PeopleLastName) LIKE :q)
        ORDER BY p.PeopleFirstName, p.PeopleLastName
    """), {"q": term}).fetchall()
    results = []
    for r in rows:
        d = dict(r._mapping)
        # Build Website URL: prefer OFN site, then external website, then ranch profile
        if d.get("OFNSlug"):
            d["Website"] = f"https://www.OatmealFarmNetwork.com/sites/{d['OFNSlug']}"
        elif d.get("ExternalWebsite"):
            d["Website"] = d["ExternalWebsite"]
        elif d.get("BusinessID"):
            d["Website"] = f"https://www.OatmealFarmNetwork.com/marketplaces/livestock/ranch/{d['BusinessID']}"
        else:
            d["Website"] = ""
        del d["OFNSlug"]
        del d["ExternalWebsite"]
        results.append(d)
    return results


@router.post("/animals/add")
async def add_animal(
    request: Request,
    db: Session = Depends(get_db),
):
    from sqlalchemy import text
    try:
        form = await request.form()
        def f(key): return form.get(key) or None
        def n(key): v = form.get(key); return float(v) if v else None
        def i(key): v = form.get(key); return int(v) if v else None

        business_id = i("BusinessID")

        # Look up PeopleID from BusinessAccess
        ba_row = db.execute(text(
            "SELECT TOP 1 PeopleID FROM BusinessAccess WHERE BusinessID = :bid AND Active = 1"
        ), {"bid": business_id}).fetchone()
        people_id = ba_row.PeopleID if ba_row else None

        db.execute(text("""
            INSERT INTO Animals (
                BusinessID, PeopleID, FullName, SpeciesID, NumberofAnimals, SpeciesCategoryID,
                DOBDay, DOBMonth, DOBYear,
                BreedID, BreedID2, BreedID3, BreedID4,
                Height, Weight, Gaited, Warmblooded, Horns, Temperment,
                Description, AncestryDescription,
                PublishForSale, CoOwnerName1, CoOwnerLink1, CoOwnerBusiness1,
                CoOwnerName2, CoOwnerLink2, CoOwnerBusiness2,
                CoOwnerName3, CoOwnerLink3, CoOwnerBusiness3,
                PercentPeruvian, PercentChilean, PercentBolivian,
                PercentUnknownOther, PercentAccoyo
            ) VALUES (
                :business_id, :people_id, :name, :species_id, :num_animals, :species_category_id,
                :dob_day, :dob_month, :dob_year,
                :breed1, :breed2, :breed3, :breed4,
                :height, :weight, :gaited, :warmblood, :horns, :temperament,
                :description, :ancestry_desc,
                :for_sale, :co_name1, :co_link1, :co_biz1,
                :co_name2, :co_link2, :co_biz2,
                :co_name3, :co_link3, :co_biz3,
                :pct_peruvian, :pct_chilean, :pct_bolivian,
                :pct_unknown, :pct_accoyo
            )
        """), {
            "business_id": business_id, "people_id": people_id,
            "name": f("Name"), "species_id": i("SpeciesID"),
            "num_animals": i("NumberOfAnimals"), "species_category_id": i("SpeciesCategoryID"),
            "dob_day": i("DOBDay"), "dob_month": i("DOBMonth"), "dob_year": i("DOBYear"),
            "breed1": i("BreedID"), "breed2": i("BreedID2"), "breed3": i("BreedID3"), "breed4": i("BreedID4"),
            "height": n("Height"), "weight": n("Weight"), "gaited": f("Gaited"),
            "warmblood": f("Warmblood"), "horns": f("Horns"), "temperament": i("Temperament"),
            "description": f("Description"), "ancestry_desc": f("AncestryDescription"),
            "for_sale": 1 if f("ForSale") == "Yes" else 0,
            "co_name1": f("CoOwnerName1"), "co_link1": f("CoOwnerLink1"), "co_biz1": f("CoOwnerBusiness1"),
            "co_name2": f("CoOwnerName2"), "co_link2": f("CoOwnerLink2"), "co_biz2": f("CoOwnerBusiness2"),
            "co_name3": f("CoOwnerName3"), "co_link3": f("CoOwnerLink3"), "co_biz3": f("CoOwnerBusiness3"),
            "pct_peruvian": f("PercentPeruvian"), "pct_chilean": f("PercentChilean"),
            "pct_bolivian": f("PercentBolivian"), "pct_unknown": f("PercentUnknownOther"),
            "pct_accoyo": f("PercentAccoyo"),
        })
        new_id = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
        animal_id = int(new_id.id)

        # Create Pricing row
        # ForSale is stored as PublishForSale on the Animals table (set above),
        # NOT in the Pricing table.
        db.execute(text("""
            INSERT INTO Pricing (
                AnimalID, Price, StudFee, EmbryoPrice, SemenPrice,
                Free, Sold, PriceComments, Financeterms
            ) VALUES (
                :aid, :price, :stud, :embryo, :semen,
                :free, 0, :comments, :terms
            )
        """), {
            "aid":      animal_id,
            "price":    n("Price"),
            "stud":     n("StudFee"),
            "embryo":   n("EmbryoPrice"),
            "semen":    n("SemenPrice"),
            "free":     1 if f("Free") == "Yes" else 0,
            "comments": f("PriceComments"),
            "terms":    f("Financeterms"),
        })

        # Upload photos to GCS and create Photos row
        photo_urls = {}
        photo_captions = {}
        for idx in range(1, 9):
            file_field = form.get(f"Photo{idx}")
            if file_field and hasattr(file_field, "read"):
                try:
                    data = await file_field.read()
                    if data:
                        url = _upload_animal_photo(data, file_field.filename)
                        photo_urls[f"Photo{idx}"] = url
                except Exception:
                    pass
            cap = form.get(f"Caption{idx}")
            if cap:
                photo_captions[f"PhotoCaption{idx}"] = cap

        # Resolve cover slot → ListPageImage url (if slot was uploaded this request)
        cover_slot_raw = form.get("CoverPhotoSlot")
        list_page_image = None
        try:
            cover_slot = int(cover_slot_raw) if cover_slot_raw else 0
        except (TypeError, ValueError):
            cover_slot = 0
        if 1 <= cover_slot <= 8:
            list_page_image = photo_urls.get(f"Photo{cover_slot}")
        # Fallback: if cover slot had no new upload, use first uploaded photo
        if not list_page_image and photo_urls:
            first_key = sorted(photo_urls.keys())[0]
            list_page_image = photo_urls[first_key]

        insert_cols = {}
        insert_cols.update(photo_urls)
        insert_cols.update(photo_captions)
        if list_page_image:
            insert_cols["ListPageImage"] = list_page_image

        # Document uploads: registration certificate → ARI column, histogram → Histogram column
        for form_field, db_col in (("AriDoc", "ARI"), ("HistogramDoc", "Histogram")):
            f_doc = form.get(form_field)
            if f_doc and hasattr(f_doc, "read"):
                try:
                    data = await f_doc.read()
                    if data:
                        insert_cols[db_col] = _upload_animal_doc(data, f_doc.filename or f"{db_col}.pdf")
                except Exception:
                    pass

        if insert_cols:
            cols   = ", ".join(insert_cols.keys())
            params = {k.lower(): v for k, v in insert_cols.items()}
            vals   = ", ".join(f":{k.lower()}" for k in insert_cols)
            db.execute(text(
                f"INSERT INTO Photos (AnimalID, {cols}) VALUES (:aid, {vals})"
            ), {"aid": animal_id, **params})
        else:
            # Always create an empty Photos row so the edit page can update it
            db.execute(text("INSERT INTO Photos (AnimalID) VALUES (:aid)"), {"aid": animal_id})

        import json as _json

        # Insert fiber samples if provided
        fiber_json = form.get("FiberSamples")
        if fiber_json:
            def _nf(v): return float(v) if v else None
            def _ni(v): return int(v) if v else None
            for s in _json.loads(fiber_json):
                year = _ni(s.get("sampleYear"))
                avg  = _nf(s.get("afd"))
                if year or avg:
                    db.execute(text("""
                        INSERT INTO Fiber (
                            AnimalID, SampleDateYear, Average, CF, StandardDev,
                            CrimpPerInch, COV, Length, GreaterThan30,
                            ShearWeight, Curve, BlanketWeight
                        ) VALUES (
                            :aid, :year, :avg, :cf, :sd, :cpi, :cov,
                            :length, :gt30, :sw, :curve, :bw
                        )
                    """), {
                        "aid":    animal_id,
                        "year":   year,
                        "avg":    avg,
                        "cf":     _nf(s.get("cf")),
                        "sd":     _nf(s.get("sd")),
                        "cpi":    _nf(s.get("crimpsPerInch")),
                        "cov":    _nf(s.get("cov")),
                        "length": _nf(s.get("stapleLength")),
                        "gt30":   _nf(s.get("gt30")),
                        "sw":     _nf(s.get("shearWeight")),
                        "curve":  _nf(s.get("curve")),
                        "bw":     _nf(s.get("blanketWeight")),
                    })

        # Insert colors if provided
        color1, color2 = f("Color1"), f("Color2")
        color3, color4 = f("Color3"), f("Color4")
        if any([color1, color2, color3, color4]):
            db.execute(text("""
                INSERT INTO Colors (AnimalID, Color1, Color2, Color3, Color4)
                VALUES (:aid, :c1, :c2, :c3, :c4)
            """), {"aid": animal_id, "c1": color1, "c2": color2, "c3": color3, "c4": color4})

        # Insert ancestry if provided
        anc_json = form.get("AncestryJSON")
        if anc_json:
            anc = _json.loads(anc_json)
            ANC_MAP = {
                "sire":        "Sire",
                "dam":         "Dam",
                "sireSire":    "SireSire",
                "sireDam":     "SireDam",
                "damSire":     "DamSire",
                "damDam":      "DamDam",
                "sireSireSire":"SireSireSire",
                "sireSireDam": "SireSireDam",
                "sireDamSire": "SireDamSire",
                "sireDamDam":  "SireDamDam",
                "damSireSire": "DamSireSire",
                "damSireDam":  "DamSireDam",
                "damDamSire":  "DamDamSire",
                "damDamDam":   "DamDamDam",
            }
            # Only proceed if any ancestor has data
            has_anc = any(
                (anc.get(k) or {}).get("name") or (anc.get(k) or {}).get("color")
                for k in ANC_MAP
            )
            if has_anc:
                all_fields = []
                params = {"aid": animal_id}
                for js_key, db_prefix in ANC_MAP.items():
                    val = anc.get(js_key) or {}
                    all_fields += [db_prefix, f"{db_prefix}Color", f"{db_prefix}Link", f"{db_prefix}ARI"]
                    params[db_prefix]           = val.get("name")  or None
                    params[f"{db_prefix}Color"] = val.get("color") or None
                    params[f"{db_prefix}Link"]  = val.get("link")  or None
                    params[f"{db_prefix}ARI"]   = val.get("ari")   or None
                existing = db.execute(text("SELECT COUNT(*) FROM Ancestors WHERE AnimalID = :aid"),
                                      {"aid": animal_id}).scalar()
                if existing:
                    set_clause = ", ".join(f"{fld} = :{fld}" for fld in all_fields)
                    db.execute(text(f"UPDATE Ancestors SET {set_clause} WHERE AnimalID = :aid"), params)
                else:
                    cols = ", ".join(["AnimalID"] + all_fields)
                    vals = ", ".join([":aid"] + [f":{fld}" for fld in all_fields])
                    db.execute(text(f"INSERT INTO Ancestors ({cols}) VALUES ({vals})"), params)

        # Insert awards if provided
        awards_json = form.get("AwardsJSON")
        if awards_json:
            for aw in _json.loads(awards_json):
                if aw.get("year") or aw.get("show") or aw.get("placing"):
                    db.execute(text("""
                        INSERT INTO awards (AnimalID, AwardYear, ShowName, Type, Placing, Awardcomments)
                        VALUES (:aid, :year, :show, :aclass, :placing, :comments)
                    """), {
                        "aid":      animal_id,
                        "year":     aw.get("year")        or None,
                        "show":     aw.get("show")        or None,
                        "aclass":   aw.get("class")       or None,
                        "placing":  aw.get("placing")     or None,
                        "comments": aw.get("description") or None,
                    })

        # Insert registrations if provided
        regs_json = form.get("RegistrationsJSON")
        if regs_json:
            for reg in _json.loads(regs_json):
                if (reg.get("number") or "").strip():
                    db.execute(text("""
                        INSERT INTO animalregistration (AnimalID, RegType, RegNumber)
                        VALUES (:aid, :reg_type, :reg_num)
                    """), {
                        "aid":      animal_id,
                        "reg_type": reg.get("type")   or None,
                        "reg_num":  reg.get("number") or None,
                    })

        db.commit()
        return {"message": "Animal added successfully", "AnimalID": animal_id}
    except Exception as e:
        db.rollback()
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Testimonials ──────────────────────────────────────────────────────────────

@router.get("/testimonials")
def get_testimonials(BusinessID: int, db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT TestimonialsID, CustomerName AS AuthorName,
               Testimonial AS Content, Rating,
               City, State, Organization, URL AS Website,
               TestimonialDate, PeopleID, Name,
               AnimalID, AnimalName, TestimonialsType
        FROM Testimonials
        WHERE CustID = :bid
        ORDER BY testimonialsOrder, TestimonialsID DESC
    """), {"bid": BusinessID}).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/testimonials/add")
async def add_testimonial(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import text
    body = await request.json()
    # Get next sort order
    max_order = db.execute(text(
        "SELECT ISNULL(MAX(testimonialsOrder), 0) FROM Testimonials WHERE CustID = :bid"
    ), {"bid": body.get("BusinessID")}).scalar()
    db.execute(text("""
        INSERT INTO Testimonials (
            CustID, CustomerName, Testimonial, Rating,
            City, State, Organization, URL,
            TestimonialDate, PeopleID, testimonialsOrder
        ) VALUES (
            :cust_id, :customer_name, :testimonial, :rating,
            :city, :state, :organization, :url,
            :testimonial_date, :people_id, :sort_order
        )
    """), {
        "cust_id": body.get("BusinessID"),
        "customer_name": body.get("AuthorName"),
        "testimonial": body.get("Content"),
        "rating": body.get("Rating"),
        "city": body.get("City") or None,
        "state": body.get("State") or None,
        "organization": body.get("Organization") or None,
        "url": body.get("Website") or None,
        "testimonial_date": body.get("TestimonialDate") or None,
        "people_id": body.get("PeopleID") or None,
        "sort_order": (max_order or 0) + 1,
    })
    db.commit()
    return {"message": "Testimonial added"}


@router.post("/testimonials/request")
async def request_testimonial(request: Request, db: Session = Depends(get_db)):
    """Email a customer inviting them to leave a testimonial for the business.
    (Frontend TestimonialsRequest.jsx POSTs { BusinessID, email, name } here — this
    endpoint previously did not exist, causing the reported 404.)"""
    from sqlalchemy import text
    body = await request.json()
    business_id = body.get("BusinessID")
    email = (body.get("email") or "").strip().lower()
    name  = (body.get("name") or "").strip()
    if not business_id or not email:
        raise HTTPException(status_code=400, detail="BusinessID and recipient email are required.")

    biz = db.execute(text("SELECT BusinessName FROM Business WHERE BusinessID = :bid"),
                     {"bid": business_id}).fetchone()
    business_name = (biz.BusinessName if biz else None) or "our farm"

    try:
        from routers.services import SENDGRID_API_KEY, SENDGRID_URL, FROM_EMAIL
        import httpx
        link = f"https://oatmealfarmnetwork.com/testimonial?BusinessID={business_id}"
        html = (
            f"<p>Hi {name or 'there'},</p>"
            f"<p><strong>{business_name}</strong> would love your feedback! "
            "Would you take a moment to share a short testimonial about your experience?</p>"
            f"<p><a href='{link}'>Leave a testimonial</a></p>"
            "<p>Thank you!</p>"
            f"<p>— {business_name}, via Oatmeal Farm Network</p>"
        )
        payload = {
            "personalizations": [{"to": [{"email": email}]}],
            "from": {"email": FROM_EMAIL, "name": business_name},
            "subject": f"{business_name} would love your feedback",
            "content": [{"type": "text/html", "value": html}],
        }
        headers = {"Authorization": "Bearer " + SENDGRID_API_KEY, "Content-Type": "application/json"}
        resp = httpx.post(SENDGRID_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code >= 400:
            print(f"[testimonials/request] SendGrid returned {resp.status_code}: {resp.text[:300]}")
            raise HTTPException(status_code=502, detail="Could not send the request email.")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[testimonials/request] email error: {e}")
        raise HTTPException(status_code=502, detail="Could not send the request email.")

    return {"message": "Testimonial request sent"}


@router.post("/testimonials/update")
async def update_testimonial(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import text
    body = await request.json()
    tid = body.get("TestimonialsID")
    if not tid:
        return {"error": "TestimonialsID required"}
    db.execute(text("""
        UPDATE Testimonials SET
            CustomerName = :customer_name,
            Testimonial = :testimonial,
            Rating = :rating,
            City = :city,
            State = :state,
            Organization = :organization,
            URL = :url,
            TestimonialDate = :testimonial_date,
            PeopleID = :people_id
        WHERE TestimonialsID = :tid
    """), {
        "customer_name": body.get("AuthorName"),
        "testimonial": body.get("Content"),
        "rating": body.get("Rating"),
        "city": body.get("City") or None,
        "state": body.get("State") or None,
        "organization": body.get("Organization") or None,
        "url": body.get("Website") or None,
        "testimonial_date": body.get("TestimonialDate") or None,
        "people_id": body.get("PeopleID") or None,
        "tid": tid,
    })
    db.commit()
    return {"message": "Testimonial updated"}


# ── Animal Packages ─────────────────────────────────────────────
@router.get("/packages")
def get_packages(BusinessID: int, db: Session = Depends(get_db)):
    from sqlalchemy import text
    # Ensure tables exist
    db.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'AnimalPackage')
        BEGIN
            CREATE TABLE AnimalPackage (
                PackageID INT IDENTITY(1,1) PRIMARY KEY,
                BusinessID INT NOT NULL,
                Title NVARCHAR(200) NOT NULL,
                Description NVARCHAR(MAX),
                PackagePrice DECIMAL(10,2),
                CreatedAt DATETIME DEFAULT GETDATE()
            )
        END
    """))
    db.execute(text("""
        IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'AnimalPackageItem')
        BEGIN
            CREATE TABLE AnimalPackageItem (
                PackageItemID INT IDENTITY(1,1) PRIMARY KEY,
                PackageID INT NOT NULL,
                AnimalID INT NOT NULL,
                IncludeType VARCHAR(20) NOT NULL DEFAULT 'sale',
                FOREIGN KEY (PackageID) REFERENCES AnimalPackage(PackageID)
            )
        END
    """))
    db.commit()

    rows = db.execute(text("""
        SELECT p.PackageID, p.Title, p.Description, p.PackagePrice, p.CreatedAt
        FROM AnimalPackage p
        WHERE p.BusinessID = :bid
        ORDER BY p.CreatedAt DESC
    """), {"bid": BusinessID}).fetchall()

    packages = []
    for r in rows:
        items = db.execute(text("""
            SELECT pi.PackageItemID, pi.AnimalID, pi.IncludeType,
                   a.FullName,
                   pr.Price, pr.SalePrice, pr.StudFee
            FROM AnimalPackageItem pi
            JOIN Animals a ON a.AnimalID = pi.AnimalID
            LEFT JOIN Pricing pr ON pr.AnimalID = pi.AnimalID
            WHERE pi.PackageID = :pid
        """), {"pid": r.PackageID}).fetchall()

        pkg_items = []
        for it in items:
            price = float(it.SalePrice) if it.SalePrice else (float(it.Price) if it.Price else 0)
            stud_fee = float(it.StudFee) if it.StudFee else 0
            pkg_items.append({
                "PackageItemID": it.PackageItemID,
                "AnimalID": it.AnimalID,
                "FullName": it.FullName,
                "IncludeType": it.IncludeType,
                "Price": price,
                "StudFee": stud_fee,
            })

        packages.append({
            "PackageID": r.PackageID,
            "Title": r.Title,
            "Description": r.Description,
            "PackagePrice": float(r.PackagePrice) if r.PackagePrice else 0,
            "CreatedAt": str(r.CreatedAt) if r.CreatedAt else None,
            "Items": pkg_items,
        })
    return packages


@router.post("/packages/save")
async def save_package(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import text
    body = await request.json()
    pkg_id = body.get("PackageID")
    biz_id = body.get("BusinessID")
    title = body.get("Title")
    desc = body.get("Description") or None
    price = body.get("PackagePrice")
    items = body.get("Items", [])

    if pkg_id:
        db.execute(text("""
            UPDATE AnimalPackage SET Title = :title, Description = :desc, PackagePrice = :price
            WHERE PackageID = :pid
        """), {"title": title, "desc": desc, "price": price, "pid": pkg_id})
        db.execute(text("DELETE FROM AnimalPackageItem WHERE PackageID = :pid"), {"pid": pkg_id})
    else:
        result = db.execute(text("""
            INSERT INTO AnimalPackage (BusinessID, Title, Description, PackagePrice)
            OUTPUT INSERTED.PackageID
            VALUES (:bid, :title, :desc, :price)
        """), {"bid": biz_id, "title": title, "desc": desc, "price": price})
        pkg_id = result.fetchone()[0]

    for it in items:
        db.execute(text("""
            INSERT INTO AnimalPackageItem (PackageID, AnimalID, IncludeType)
            VALUES (:pid, :aid, :itype)
        """), {"pid": pkg_id, "aid": it["AnimalID"], "itype": it.get("IncludeType", "sale")})

    db.commit()
    return {"message": "Package saved", "PackageID": pkg_id}


@router.post("/packages/delete")
async def delete_package(request: Request, db: Session = Depends(get_db)):
    from sqlalchemy import text
    body = await request.json()
    pid = body.get("PackageID")
    db.execute(text("DELETE FROM AnimalPackageItem WHERE PackageID = :pid"), {"pid": pid})
    db.execute(text("DELETE FROM AnimalPackage WHERE PackageID = :pid"), {"pid": pid})
    db.commit()
    return {"message": "Package deleted"}


# -------------------------
# Business team management (BusinessAccess CRUD)
# -------------------------
class BusinessMemberAddRequest(BaseModel):
    BusinessID: int
    Email: str
    PeopleFirstName: str = ""
    PeopleLastName: str = ""
    AccessLevelID: int = 1
    Role: str = "Staff"


class BusinessMemberUpdateRequest(BaseModel):
    AccessLevelID: int = None
    Role: str = None
    Active: int = None
    PeopleFirstName: str = None
    PeopleLastName: str = None
    PeopleEmail: str = None


def _require_business_owner(db: Session, people_id: int, business_id: int):
    access = db.query(models.BusinessAccess).filter(
        models.BusinessAccess.BusinessID == business_id,
        models.BusinessAccess.PeopleID == people_id,
        models.BusinessAccess.Active == 1,
    ).first()
    if not access or (access.AccessLevelID or 0) < 3:
        raise HTTPException(status_code=403, detail="Team management requires AccessLevelID >= 3 on this business.")
    return access


@router.get("/business-members")
def list_business_members(BusinessID: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_business_owner(db, current_user.PeopleID, BusinessID)
    rows = db.execute(text("""
        SELECT
            ba.BusinessAccessID,
            ba.BusinessID,
            ba.PeopleID,
            ba.AccessLevelID,
            ba.Active,
            ba.CreatedAt,
            ba.RevokedAt,
            ba.Role,
            p.PeopleFirstName,
            p.PeopleLastName,
            p.PeopleEmail,
            p.PeoplePhone
        FROM BusinessAccess ba
        LEFT JOIN People p ON p.PeopleID = ba.PeopleID
        WHERE ba.BusinessID = :bid
        ORDER BY ba.Active DESC, ba.CreatedAt DESC
    """), {"bid": BusinessID}).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/business-members")
def add_business_member(payload: BusinessMemberAddRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    import datetime
    _require_business_owner(db, current_user.PeopleID, payload.BusinessID)

    email = (payload.Email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    person = db.query(models.People).filter(models.People.PeopleEmail == email).first()
    if not person:
        if not payload.PeopleFirstName.strip() or not payload.PeopleLastName.strip():
            raise HTTPException(status_code=400, detail="No account found with that email. Provide first and last name to create one.")
        person = models.People(
            PeopleFirstName=payload.PeopleFirstName.strip(),
            PeopleLastName=payload.PeopleLastName.strip(),
            PeopleEmail=email,
            PeoplePassword="",
            PeopleActive=1,
            accesslevel=0,
            Subscriptionlevel=0,
            PeopleCreationDate=datetime.datetime.utcnow(),
        )
        db.add(person)
        db.flush()

    existing = db.query(models.BusinessAccess).filter(
        models.BusinessAccess.BusinessID == payload.BusinessID,
        models.BusinessAccess.PeopleID == person.PeopleID,
    ).first()

    if existing:
        existing.AccessLevelID = payload.AccessLevelID
        existing.Role = payload.Role
        existing.Active = 1
        existing.RevokedAt = None
        if not existing.CreatedAt:
            existing.CreatedAt = datetime.datetime.utcnow()
        db.commit()
        db.refresh(existing)
        access = existing
    else:
        access = models.BusinessAccess(
            BusinessID=payload.BusinessID,
            PeopleID=person.PeopleID,
            AccessLevelID=payload.AccessLevelID,
            Active=1,
            CreatedAt=datetime.datetime.utcnow(),
            Role=payload.Role,
        )
        db.add(access)
        db.commit()
        db.refresh(access)

    return {
        "BusinessAccessID": access.BusinessAccessID,
        "BusinessID": access.BusinessID,
        "PeopleID": person.PeopleID,
        "PeopleFirstName": person.PeopleFirstName,
        "PeopleLastName": person.PeopleLastName,
        "PeopleEmail": person.PeopleEmail,
        "AccessLevelID": access.AccessLevelID,
        "Role": access.Role,
        "Active": access.Active,
        "CreatedAt": str(access.CreatedAt) if access.CreatedAt else None,
    }


@router.put("/business-members/{business_access_id}")
def update_business_member(business_access_id: int, payload: BusinessMemberUpdateRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    import datetime
    access = db.query(models.BusinessAccess).filter(
        models.BusinessAccess.BusinessAccessID == business_access_id
    ).first()
    if not access:
        raise HTTPException(status_code=404, detail="Team member not found.")
    _require_business_owner(db, current_user.PeopleID, access.BusinessID)

    if payload.AccessLevelID is not None:
        access.AccessLevelID = payload.AccessLevelID
    if payload.Role is not None:
        access.Role = payload.Role
    if payload.Active is not None:
        access.Active = payload.Active
        if payload.Active == 0 and not access.RevokedAt:
            access.RevokedAt = datetime.datetime.utcnow()
        elif payload.Active == 1:
            access.RevokedAt = None

    # Update the linked person's name/email if provided. These live on the
    # People record (shared across the person's whole account), not BusinessAccess.
    person = None
    if (payload.PeopleFirstName is not None
            or payload.PeopleLastName is not None
            or payload.PeopleEmail is not None):
        person = db.query(models.People).filter(
            models.People.PeopleID == access.PeopleID
        ).first()
        if person:
            if payload.PeopleFirstName is not None:
                person.PeopleFirstName = payload.PeopleFirstName.strip()
            if payload.PeopleLastName is not None:
                person.PeopleLastName = payload.PeopleLastName.strip()
            if payload.PeopleEmail is not None:
                new_email = payload.PeopleEmail.strip().lower()
                if new_email and new_email != (person.PeopleEmail or "").lower():
                    clash = db.query(models.People).filter(
                        models.People.PeopleEmail == new_email,
                        models.People.PeopleID != person.PeopleID,
                    ).first()
                    if clash:
                        raise HTTPException(status_code=409, detail="That email is already in use by another account.")
                    person.PeopleEmail = new_email

    db.commit()
    db.refresh(access)
    if person is None:
        person = db.query(models.People).filter(
            models.People.PeopleID == access.PeopleID
        ).first()
    return {
        "BusinessAccessID": access.BusinessAccessID,
        "AccessLevelID": access.AccessLevelID,
        "Role": access.Role,
        "Active": access.Active,
        "PeopleFirstName": person.PeopleFirstName if person else None,
        "PeopleLastName": person.PeopleLastName if person else None,
        "PeopleEmail": person.PeopleEmail if person else None,
    }


@router.delete("/business-members/{business_access_id}")
def remove_business_member(business_access_id: int, hard: bool = False, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    import datetime
    access = db.query(models.BusinessAccess).filter(
        models.BusinessAccess.BusinessAccessID == business_access_id
    ).first()
    if not access:
        raise HTTPException(status_code=404, detail="Team member not found.")
    _require_business_owner(db, current_user.PeopleID, access.BusinessID)

    if access.PeopleID == current_user.PeopleID:
        raise HTTPException(status_code=400, detail="You can't remove yourself. Ask another owner to do it.")

    if hard:
        db.delete(access)
        db.commit()
        return {"message": "Team member permanently deleted", "BusinessAccessID": business_access_id}

    access.Active = 0
    access.RevokedAt = datetime.datetime.utcnow()
    db.commit()
    return {"message": "Team member removed", "BusinessAccessID": business_access_id}


# -------------------------
# LKM team management (People.LKMAccessLevel) — separate from Oatmeal AI access
# -------------------------
class LKMMemberAddRequest(BaseModel):
    Email: str
    PeopleFirstName: str = ""
    PeopleLastName: str = ""
    LKMAccessLevel: int = 1


class LKMMemberUpdateRequest(BaseModel):
    LKMAccessLevel: int = None
    PeopleFirstName: str = None
    PeopleLastName: str = None


def _require_lkm_admin(current_user):
    level = getattr(current_user, 'LKMAccessLevel', 0) or 0
    if level < 3:
        raise HTTPException(status_code=403, detail="LKM team management requires LKMAccessLevel >= 3.")


@router.get("/lkm-members")
def list_lkm_members(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_lkm_admin(current_user)
    rows = db.execute(text("""
        SELECT PeopleID, PeopleFirstName, PeopleLastName, PeopleEmail, PeoplePhone,
               LKMAccessLevel, PeopleActive, PeopleCreationDate
        FROM People
        WHERE LKMAccessLevel IS NOT NULL AND LKMAccessLevel > 0
        ORDER BY LKMAccessLevel DESC, PeopleLastName, PeopleFirstName
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/lkm-members")
def add_lkm_member(payload: LKMMemberAddRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    import datetime
    _require_lkm_admin(current_user)

    email = (payload.Email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    person = db.query(models.People).filter(models.People.PeopleEmail == email).first()
    if not person:
        if not payload.PeopleFirstName.strip() or not payload.PeopleLastName.strip():
            raise HTTPException(status_code=400, detail="No account found with that email. Provide first and last name to create one.")
        person = models.People(
            PeopleFirstName=payload.PeopleFirstName.strip(),
            PeopleLastName=payload.PeopleLastName.strip(),
            PeopleEmail=email,
            PeoplePassword="",
            PeopleActive=1,
            accesslevel=0,
            Subscriptionlevel=0,
            LKMAccessLevel=payload.LKMAccessLevel,
            PeopleCreationDate=datetime.datetime.utcnow(),
        )
        db.add(person)
        db.flush()
    else:
        person.LKMAccessLevel = payload.LKMAccessLevel

    db.commit()
    db.refresh(person)
    return {
        "PeopleID": person.PeopleID,
        "PeopleFirstName": person.PeopleFirstName,
        "PeopleLastName": person.PeopleLastName,
        "PeopleEmail": person.PeopleEmail,
        "LKMAccessLevel": person.LKMAccessLevel or 0,
    }


@router.put("/lkm-members/{people_id}")
def update_lkm_member(people_id: int, payload: LKMMemberUpdateRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_lkm_admin(current_user)
    person = db.query(models.People).filter(models.People.PeopleID == people_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found.")
    if payload.LKMAccessLevel is not None:
        person.LKMAccessLevel = payload.LKMAccessLevel
    if payload.PeopleFirstName is not None:
        person.PeopleFirstName = payload.PeopleFirstName.strip()
    if payload.PeopleLastName is not None:
        person.PeopleLastName = payload.PeopleLastName.strip()
    db.commit()
    db.refresh(person)
    return {
        "PeopleID": person.PeopleID,
        "PeopleFirstName": person.PeopleFirstName,
        "PeopleLastName": person.PeopleLastName,
        "LKMAccessLevel": person.LKMAccessLevel or 0,
    }


@router.delete("/lkm-members/{people_id}")
def remove_lkm_member(people_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_lkm_admin(current_user)
    if people_id == current_user.PeopleID:
        raise HTTPException(status_code=400, detail="You can't remove your own access. Ask another LKM admin to do it.")
    person = db.query(models.People).filter(models.People.PeopleID == people_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found.")
    person.LKMAccessLevel = 0
    db.commit()
    return {"message": "LKM access removed", "PeopleID": people_id}


class LKMPasswordResetRequest(BaseModel):
    NewPassword: str


@router.put("/lkm-members/{people_id}/password")
def reset_lkm_member_password(people_id: int, payload: LKMPasswordResetRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _require_lkm_admin(current_user)
    new_pw = (payload.NewPassword or "").strip()
    if len(new_pw) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    person = db.query(models.People).filter(models.People.PeopleID == people_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found.")
    person.PeoplePassword = hash_password(new_pw)
    db.commit()
    return {"message": "Password reset", "PeopleID": people_id}