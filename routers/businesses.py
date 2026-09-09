from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db, SessionLocal
from routers.subscription_limits import DIRECTORY_VISIBLE_SQL
from routers.directory_regions import IN_DIRECTORY_REGION_SQL, DIRECTORY_COUNTRY_IDS
import models
import datetime

router = APIRouter(prefix="/api/businesses", tags=["businesses"])


def _seed_business_types():
    """Idempotent seed of business categories surfaced in the public /directory.
    Runs once on module load — safe to re-run because each insert is gated by a
    NOT EXISTS check on BusinessType."""
    seed_types = [
        "Hunger Relief Organization",
    ]
    try:
        with SessionLocal() as db:
            for bt in seed_types:
                db.execute(text("""
                    IF NOT EXISTS (SELECT 1 FROM businesstypelookup WHERE BusinessType = :bt)
                    INSERT INTO businesstypelookup (BusinessType) VALUES (:bt)
                """), {"bt": bt})
            db.commit()
    except Exception as e:
        print(f"[businesses] seed_business_types error: {e}")


_seed_business_types()


@router.get("/{business_id}/team")
def get_business_team(business_id: int, db: Session = Depends(get_db)):
    """
    All People with active BusinessAccess for this business. Used by:
      - event wizard attendees step (Import from my team)
      - event mailing list admin (Import contacts)
    """
    rows = db.execute(text("""
        SELECT p.PeopleID, p.PeopleFirstName, p.PeopleLastName, p.PeopleEmail,
               p.PeoplePhone, ba.AccessLevel
        FROM BusinessAccess ba
        JOIN People p ON p.PeopleID = ba.PeopleID
        WHERE ba.BusinessID = :bid AND ba.Active = 1
        ORDER BY p.PeopleLastName, p.PeopleFirstName
    """), {"bid": business_id}).mappings().all()
    return [
        {
            "PeopleID":    r["PeopleID"],
            "FirstName":   r["PeopleFirstName"],
            "LastName":    r["PeopleLastName"],
            "Email":       r["PeopleEmail"],
            "Phone":       r["PeoplePhone"],
            "AccessLevel": r["AccessLevel"],
        }
        for r in rows
    ]


def clean(val):
    """Return None if value is null, '0', or empty string."""
    if val is None:
        return None
    if str(val).strip() in ("0", ""):
        return None
    return val


def build_logo_url(logo):
    """Build full logo URL handling /uploads/ prefix or bare filename."""
    if not logo or str(logo).strip() in ("0", ""):
        return None
    if logo.startswith("http://") or logo.startswith("https://"):
        return logo
    if logo.startswith("/"):
        return "https://www.oatmealfarmnetwork.com" + logo
    return "https://www.oatmealfarmnetwork.com/uploads/" + logo


@router.get("/countries")
def get_countries(business_type_id: str = None, db: Session = Depends(get_db)):
    """The countries this site covers: USA, Canada, Greenland.

    Serves both the directory filter and the country picker on account
    creation, so the unfiltered branch returns all three even when one has no
    businesses yet -- deriving the list from existing rows would have made it
    impossible to register the first business in Greenland.
    """
    try:
        if business_type_id:
            rows = db.execute(text("""
                SELECT DISTINCT c.name
                FROM country c
                JOIN Address a ON a.country_id = c.country_id
                JOIN Business b ON b.AddressID = a.AddressID
                WHERE b.BusinessTypeID = :btid
                  AND c.name IS NOT NULL AND c.name <> ''
                  AND c.country_id IN (1228, 1039, 1086)
                ORDER BY c.name
            """), {"btid": int(business_type_id)}).fetchall()
        else:
            rows = db.execute(text("""
                SELECT name FROM country
                WHERE name IS NOT NULL AND name <> ''
                  AND country_id IN (1228, 1039, 1086)
                ORDER BY name
            """)).fetchall()
        return [r.name for r in rows]
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/states")
def get_states(country: str, db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text
        rows = db.execute(
            text("""SELECT sp.StateIndex, sp.name
                    FROM state_province sp
                    JOIN country c ON sp.country_id = c.country_id
                    WHERE c.name = :country
                    ORDER BY sp.name"""),
            {"country": country}
        ).fetchall()
        return [{"StateIndex": r.StateIndex, "name": r.name} for r in rows]
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/types")
def get_business_types(db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text
        rows = db.execute(
            text("SELECT BusinessTypeID, BusinessType FROM businesstypelookup ORDER BY BusinessType")
        ).fetchall()
        return [{"BusinessTypeID": r.BusinessTypeID, "BusinessType": r.BusinessType} for r in rows]
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create")
def create_account(payload: dict, db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text

        people_id = payload.get("PeopleID")
        if not people_id:
            raise HTTPException(status_code=400, detail="PeopleID is required")

        # 1. Create Address record
        country_id = payload.get("country_id") or None
        address = models.Address(
            AddressStreet  = payload.get("AddressStreet", ""),
            AddressCity    = payload.get("AddressCity", ""),
            AddressState   = payload.get("StateIndex", ""),
            AddressZip     = payload.get("AddressZip", ""),
            AddressCountry = payload.get("country", ""),
            country_id     = int(country_id) if country_id else None,
        )
        db.add(address)
        db.flush()

        # 2. Create Websites record if website provided
        websites_id = None
        website = payload.get("BusinessWebsite", "")
        if website:
            ws = models.Websites(Website=website)
            db.add(ws)
            db.flush()
            websites_id = ws.WebsitesID

        # 3. Create Business record
        business = models.Business(
            BusinessTypeID    = payload.get("BusinessTypeID"),
            BusinessName      = payload.get("BusinessName", ""),
            AddressID         = address.AddressID,
            WebsitesID        = websites_id,
            SubscriptionLevel = 0,
            AccessLevel       = 1,
        )
        db.add(business)
        db.flush()

        # 4. Create BusinessAccess record linking user to business.
        # Creator gets AccessLevelID=3 (admin) so they can manage their own team
        # via /auth/business-members, which requires level >= 3.
        access = models.BusinessAccess(
            BusinessID    = business.BusinessID,
            PeopleID      = int(people_id),
            AccessLevelID = 3,
            Active        = 1,
            CreatedAt     = datetime.datetime.utcnow(),
            Role          = "Owner",
        )
        db.add(access)

        # 5. Update People phone if provided
        phone = payload.get("PeoplePhone", "")
        if phone:
            db.execute(
                text("UPDATE People SET PeoplePhone = :phone WHERE PeopleID = :pid"),
                {"phone": phone, "pid": int(people_id)}
            )

        db.commit()
        return {"BusinessID": business.BusinessID, "message": "Account created successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug")
def debug_businesses(db: Session = Depends(get_db)):
    countries = db.query(models.Country.name).limit(20).all()
    sample = (
        db.query(models.Business.BusinessName, models.Country.name)
        .join(models.Address, models.Business.AddressID == models.Address.AddressID)
        .join(models.Country, models.Address.country_id == models.Country.country_id)
        .limit(10)
        .all()
    )
    return {
        "countries": [c[0] for c in countries],
        "sample_businesses": [{"name": b[0], "country": b[1]} for b in sample]
    }


@router.get("/subcategories")
def get_subcategories(BusinessTypeID: int = None, db: Session = Depends(get_db)):
    """Return distinct SpeciesCategory names for businesses of a given type."""
    try:
        if not BusinessTypeID:
            return []
        rows = db.execute(text("""
            SELECT DISTINCT sc.SpeciesCategory
            FROM Business b
            JOIN Animals a ON a.BusinessID = b.BusinessID
            JOIN speciescategory sc ON a.SpeciesCategoryID = sc.SpeciesCategoryID
            WHERE b.BusinessTypeID = :btype
              AND sc.SpeciesCategory IS NOT NULL
              AND sc.SpeciesCategory <> ''
            ORDER BY sc.SpeciesCategory
        """), {"btype": BusinessTypeID}).fetchall()
        return [r.SpeciesCategory for r in rows]
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
def search_businesses(q: str = "", limit: int = 1000, db: Session = Depends(get_db)):
    """Substring search on business name, across every category.

    Powers the directory landing-page search, which previously only filtered
    the category tiles and so could never surface an actual business.
    """
    term = (q or "").strip()
    if len(term) < 2:
        return {"query": term, "total": 0, "limit": limit, "businesses": []}

    # Escape LIKE metacharacters so a stray % or _ can't widen the match.
    escaped = (term.replace("\\", "\\\\")
                   .replace("%", "\\%")
                   .replace("_", "\\_")
                   .replace("[", "\\["))

    # Typing "alpacas" should also find "A Goode View Alpaca Farm": drop a
    # trailing plural 's' and match the stem, which still matches the plural.
    # Guarded on length so short words ("gas") aren't mangled into noise.
    if len(escaped) > 4 and escaped[-1] in ("s", "S"):
        escaped = escaped[:-1]

    params = {"pattern": f"%{escaped}%", "limit": max(1, min(int(limit or 1000), 2000))}
    named = ("b.BusinessName IS NOT NULL AND LTRIM(RTRIM(b.BusinessName)) <> ''")
    where = (f"b.BusinessName LIKE :pattern ESCAPE '\\' AND {named} "
             f"AND {DIRECTORY_VISIBLE_SQL} AND {IN_DIRECTORY_REGION_SQL}")

    try:
        total = db.execute(text(
            f"SELECT COUNT(*) FROM Business b WHERE {where}"), params).scalar() or 0

        # OUTER APPLY for the state lookup: the legacy join matches either a
        # numeric StateIndex or a name, which can hit more than one row and
        # duplicate the business.
        rows = db.execute(text(f"""
            SELECT TOP (:limit)
                b.BusinessID, b.BusinessName, b.BusinessTypeID, b.Logo,
                bt.BusinessType, a.AddressCity, st.name AS StateName
            FROM Business b
            LEFT JOIN businesstypelookup bt ON bt.BusinessTypeID = b.BusinessTypeID
            LEFT JOIN Address a ON a.AddressID = b.AddressID
            OUTER APPLY (
                SELECT TOP 1 sp.name FROM state_province sp
                WHERE a.AddressState = CAST(sp.StateIndex AS CHAR)
                   OR a.AddressState = sp.name
            ) st
            WHERE {where}
            ORDER BY b.BusinessName
        """), params).fetchall()

        return {
            "query": term,
            "total": total,
            "limit": params["limit"],
            "businesses": [{
                "BusinessID":     r.BusinessID,
                "BusinessName":   clean(r.BusinessName),
                "BusinessTypeID": r.BusinessTypeID,
                "BusinessType":   r.BusinessType,
                "AddressCity":    clean(r.AddressCity),
                "AddressState":   clean(r.StateName),
                "ProfileImage":   build_logo_url(r.Logo),
            } for r in rows],
        }
    except Exception:
        import traceback
        traceback.print_exc()
        return {"query": term, "total": 0, "limit": limit, "businesses": []}


@router.get("/")
def get_businesses(
    country: str = None,
    BusinessTypeID: int = None,
    state: str = None,
    species_category: str = None,
    db: Session = Depends(get_db)
):
    try:
        from sqlalchemy import text

        params = {}
        conditions = ["1=1", DIRECTORY_VISIBLE_SQL, IN_DIRECTORY_REGION_SQL]

        if BusinessTypeID:
            conditions.append("b.BusinessTypeID = :business_type_id")
            params["business_type_id"] = BusinessTypeID
        if country:
            conditions.append("c.name = :country")
            params["country"] = country
        if state:
            conditions.append("sp.name = :state")
            params["state"] = state
        if species_category:
            conditions.append("""EXISTS (
                SELECT 1 FROM Animals a2
                JOIN speciescategory sc2 ON a2.SpeciesCategoryID = sc2.SpeciesCategoryID
                WHERE a2.BusinessID = b.BusinessID
                  AND sc2.SpeciesCategory = :species_category
            )""")
            params["species_category"] = species_category

        where_clause = " AND ".join(conditions)

        sql = text(f"""
            SELECT
                b.BusinessID, b.BusinessName, b.BusinessEmail, b.BusinessPhone,
                b.BusinessTypeID, b.Logo,
                b.BusinessFacebook, b.BusinessInstagram, b.BusinessLinkedIn,
                b.BusinessX, b.BusinessPinterest, b.BusinessYouTube,
                b.BusinessTruthSocial, b.BusinessBlog,
                b.BusinessOtherSocial1, b.BusinessOtherSocial2,
                a.AddressStreet, a.AddressCity, a.AddressZip,
                sp.name AS StateName,
                c.name AS CountryName,
                bt.BusinessType,
                w.Website
            FROM Business b
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            LEFT JOIN businesstypelookup bt ON b.BusinessTypeID = bt.BusinessTypeID
            LEFT JOIN country c ON a.country_id = c.country_id
            LEFT JOIN state_province sp ON a.AddressState = CAST(sp.StateIndex AS CHAR)
                                        OR a.AddressState = sp.name
            LEFT JOIN Websites w ON b.WebsitesID = w.WebsitesID
            WHERE {where_clause}
            ORDER BY b.BusinessName
        """)

        results = db.execute(sql, params).fetchall()

        businesses = []
        for r in results:
            businesses.append({
                "BusinessID":           r.BusinessID,
                "BusinessName":         r.BusinessName,
                "BusinessEmail":        r.BusinessEmail,
                "BusinessPhone":        r.BusinessPhone,
                "BusinessTypeID":       r.BusinessTypeID,
                "BusinessType":         r.BusinessType,
                "AddressStreet":        clean(r.AddressStreet),
                "AddressCity":          clean(r.AddressCity),
                "AddressState":         clean(r.StateName),
                "AddressZip":           clean(r.AddressZip),
                "AddressCountry":       r.CountryName,
                "ProfileImage":         build_logo_url(r.Logo),
                "BusinessWebsite":      r.Website if r.Website else None,
                "BusinessFacebook":     r.BusinessFacebook,
                "BusinessInstagram":    r.BusinessInstagram,
                "BusinessLinkedIn":     r.BusinessLinkedIn,
                "BusinessX":            r.BusinessX,
                "BusinessPinterest":    r.BusinessPinterest,
                "BusinessYouTube":      r.BusinessYouTube,
                "BusinessTruthSocial":  r.BusinessTruthSocial,
                "BusinessBlog":         r.BusinessBlog,
                "BusinessOtherSocial1": r.BusinessOtherSocial1,
                "BusinessOtherSocial2": r.BusinessOtherSocial2,
            })

        return businesses

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── PROFILE endpoints ─────────────────────────────────────────────

@router.get("/profile/{business_id}")
def get_profile(business_id: int, db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text

        row = db.execute(text("""
            SELECT
                b.BusinessID, b.BusinessName, b.BusinessEmail,
                b.BusinessPhone, b.AddressID, b.WebsitesID,
                b.Contact1PeopleID, b.BusinessDescription, b.Logo,
                b.BusinessFacebook, b.BusinessInstagram, b.BusinessLinkedIn,
                b.BusinessX, b.BusinessPinterest, b.BusinessYouTube,
                b.BusinessTruthSocial, b.BusinessBlog,
                b.BusinessOtherSocial1, b.BusinessOtherSocial2,
                b.Cuisine, b.HeadChef, b.SeatingCapacity,
                b.RestaurantHours, b.YearOpened, b.SourcingPhilosophy,
                a.AddressStreet, a.AddressApt, a.AddressCity,
                a.AddressState, a.AddressZip, a.StateIndex, a.country_id,
                w.Website,
                p.PeopleFirstName, p.PeopleLastName, p.PeopleEmail,
                p.PeoplePhone AS ContactPhone,
                c.name AS country_name
            FROM Business b
            LEFT JOIN Address a ON b.AddressID = a.AddressID
            LEFT JOIN Websites w ON b.WebsitesID = w.WebsitesID
            LEFT JOIN People p ON b.Contact1PeopleID = p.PeopleID
            LEFT JOIN country c ON a.country_id = c.country_id
            WHERE b.BusinessID = :bid
        """), {"bid": business_id}).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Business not found")

        # Fetch phone/cell/fax from Phone table if exists
        phone_row = None
        if row.BusinessID:
            phone_row = db.execute(text("""
                SELECT Phone, CellPhone, Fax FROM Phone
                WHERE PhoneID = (SELECT PhoneID FROM Business WHERE BusinessID = :bid)
            """), {"bid": business_id}).fetchone()

        return {
            "BusinessID":       row.BusinessID,
            "BusinessName":     row.BusinessName,
            "BusinessEmail":    row.BusinessEmail,
            "BusinessWebsite":  row.Website,
            "AddressStreet":    row.AddressStreet,
            "AddressApt":       row.AddressApt,
            "AddressCity":      row.AddressCity,
            "AddressState":     row.AddressState,
            "AddressZip":       row.AddressZip,
            "StateIndex":       row.StateIndex,
            "country_id":       row.country_id,
            "country_name":     row.country_name or "USA",
            "ContactFirstName": row.PeopleFirstName,
            "ContactLastName":  row.PeopleLastName,
            "ContactEmail":     row.PeopleEmail or row.BusinessEmail,
            "BusinessPhone":    phone_row.Phone if phone_row else row.BusinessPhone,
            "BusinessCell":     phone_row.CellPhone if phone_row else None,
            "BusinessFax":      phone_row.Fax if phone_row else None,
            "WebsitesID":           row.WebsitesID,
            "AddressID":            row.AddressID,
            "Contact1PeopleID":     row.Contact1PeopleID,
            "BusinessDescription":  row.BusinessDescription,
            "Logo":                 build_logo_url(row.Logo),
            "BusinessFacebook":     row.BusinessFacebook,
            "BusinessInstagram":    row.BusinessInstagram,
            "BusinessLinkedIn":     row.BusinessLinkedIn,
            "BusinessX":            row.BusinessX,
            "BusinessPinterest":    row.BusinessPinterest,
            "BusinessYouTube":      row.BusinessYouTube,
            "BusinessTruthSocial":  row.BusinessTruthSocial,
            "BusinessBlog":         row.BusinessBlog,
            "BusinessOtherSocial1": row.BusinessOtherSocial1,
            "BusinessOtherSocial2": row.BusinessOtherSocial2,
            "Cuisine":              getattr(row, 'Cuisine', None),
            "HeadChef":             getattr(row, 'HeadChef', None),
            "SeatingCapacity":      getattr(row, 'SeatingCapacity', None),
            "RestaurantHours":      getattr(row, 'RestaurantHours', None),
            "YearOpened":           getattr(row, 'YearOpened', None),
            "SourcingPhilosophy":   getattr(row, 'SourcingPhilosophy', None),
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/profile/{business_id}")
def update_profile(business_id: int, payload: dict, db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text

        # Get current IDs
        ids = db.execute(text("""
            SELECT AddressID, WebsitesID, Contact1PeopleID,
                   PhoneID FROM Business WHERE BusinessID = :bid
        """), {"bid": business_id}).fetchone()

        if not ids:
            raise HTTPException(status_code=404, detail="Business not found")

        # 1. Update Address — create the row if the business has no AddressID yet,
        # or if the AddressID is dangling (points to a row that no longer exists)
        address_params = {
            "street":  (payload.get("AddressStreet") or "").strip(),
            "apt":     (payload.get("AddressApt")    or "").strip(),
            "city":    (payload.get("AddressCity")   or "").strip(),
            "state":   payload.get("StateIndex") or None,
            "zip":     (payload.get("AddressZip")    or "").strip(),
            "country": (payload.get("country_name")  or "USA").strip(),
        }
        address_exists = False
        if ids.AddressID:
            address_exists = db.execute(
                text("SELECT 1 FROM Address WHERE AddressID = :aid"),
                {"aid": ids.AddressID}
            ).fetchone() is not None

        if address_exists:
            db.execute(text("""
                UPDATE Address SET
                    AddressStreet = :street,
                    AddressApt    = :apt,
                    AddressCity   = :city,
                    StateIndex    = :state,
                    AddressZip    = :zip,
                    country_id    = (SELECT country_id FROM country WHERE name = :country)
                WHERE AddressID = :aid
            """), {**address_params, "aid": ids.AddressID})
        else:
            db.execute(text("""
                INSERT INTO Address (AddressStreet, AddressApt, AddressCity, StateIndex, AddressZip, country_id)
                VALUES (:street, :apt, :city, :state, :zip,
                        (SELECT country_id FROM country WHERE name = :country))
            """), address_params)
            new_address = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
            db.execute(text("UPDATE Business SET AddressID = :aid WHERE BusinessID = :bid"),
                       {"aid": int(new_address.id), "bid": business_id})

        # 2. Update or create Website
        website = (payload.get("BusinessWebsite") or "").strip()
        if website.lower().startswith("http://"):
            website = website[7:]
        if ids.WebsitesID:
            db.execute(text("UPDATE Websites SET Website = :w WHERE WebsitesID = :wid"),
                       {"w": website, "wid": ids.WebsitesID})
        elif website:
            db.execute(text("INSERT INTO Websites (Website) VALUES (:w)"), {"w": website})
            new_ws = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
            db.execute(text("UPDATE Business SET WebsitesID = :wid WHERE BusinessID = :bid"),
                       {"wid": int(new_ws.id), "bid": business_id})

        # 3. Update or insert Phone — link new row back to Business
        phone = (payload.get("BusinessPhone") or "").strip()
        cell  = (payload.get("BusinessCell")  or "").strip()
        fax   = (payload.get("BusinessFax")   or "").strip()
        if ids.PhoneID:
            db.execute(text("""
                UPDATE Phone SET Phone = :phone, CellPhone = :cell, Fax = :fax
                WHERE PhoneID = :pid
            """), {"phone": phone, "cell": cell, "fax": fax, "pid": ids.PhoneID})
        elif phone or cell or fax:
            db.execute(text("""
                INSERT INTO Phone (Phone, CellPhone, Fax) VALUES (:phone, :cell, :fax)
            """), {"phone": phone, "cell": cell, "fax": fax})
            new_phone = db.execute(text("SELECT SCOPE_IDENTITY() AS id")).fetchone()
            db.execute(text("UPDATE Business SET PhoneID = :pid WHERE BusinessID = :bid"),
                       {"pid": int(new_phone.id), "bid": business_id})

        # 4. Update Contact (People)
        contact_people_id = ids.Contact1PeopleID
        if not contact_people_id:
            # Fall back to the business owner from BusinessAccess
            access_row = db.execute(text("""
                SELECT TOP 1 PeopleID FROM BusinessAccess
                WHERE BusinessID = :bid AND Active = 1
                ORDER BY CreatedAt ASC
            """), {"bid": business_id}).fetchone()
            if access_row:
                contact_people_id = access_row.PeopleID
                db.execute(text("UPDATE Business SET Contact1PeopleID = :pid WHERE BusinessID = :bid"),
                           {"pid": contact_people_id, "bid": business_id})

        if contact_people_id:
            db.execute(text("""
                UPDATE People SET
                    PeopleFirstName = :fn,
                    PeopleLastName  = :ln,
                    PeopleEmail     = :email
                WHERE PeopleID = :pid
            """), {
                "fn":    (payload.get("ContactFirstName") or "").strip(),
                "ln":    (payload.get("ContactLastName")  or "").strip(),
                "email": (payload.get("ContactEmail")     or "").strip(),
                "pid":   contact_people_id,
            })

        # 5. Update Business name, description, and social links
        def s(key): return (payload.get(key) or "").strip() or None
        db.execute(text("""
            UPDATE Business SET
                BusinessName        = :name,
                BusinessEmail       = :email,
                BusinessDescription = :desc,
                BusinessFacebook    = :facebook,
                BusinessInstagram   = :instagram,
                BusinessLinkedIn    = :linkedin,
                BusinessX           = :x,
                BusinessPinterest   = :pinterest,
                BusinessYouTube     = :youtube,
                BusinessTruthSocial = :truth,
                BusinessBlog        = :blog,
                BusinessOtherSocial1= :other1,
                BusinessOtherSocial2= :other2,
                Cuisine             = :cuisine,
                HeadChef            = :chef,
                SeatingCapacity     = :capacity,
                RestaurantHours     = :hours,
                YearOpened          = :year_opened,
                SourcingPhilosophy  = :sourcing
            WHERE BusinessID = :bid
        """), {
            "name":      (payload.get("BusinessName")     or "").strip(),
            "email":     (payload.get("ContactEmail")     or "").strip(),
            "desc":      s("BusinessDescription"),
            "facebook":  s("BusinessFacebook"),
            "instagram": s("BusinessInstagram"),
            "linkedin":  s("BusinessLinkedIn"),
            "x":         s("BusinessX"),
            "pinterest": s("BusinessPinterest"),
            "youtube":   s("BusinessYouTube"),
            "truth":     s("BusinessTruthSocial"),
            "blog":      s("BusinessBlog"),
            "other1":    s("BusinessOtherSocial1"),
            "other2":    s("BusinessOtherSocial2"),
            "cuisine":   s("Cuisine"),
            "chef":      s("HeadChef"),
            "capacity":  payload.get("SeatingCapacity") or None,
            "hours":     s("RestaurantHours"),
            "year_opened": payload.get("YearOpened") or None,
            "sourcing":  s("SourcingPhilosophy"),
            "bid":       business_id,
        })

        db.commit()
        return {"message": "Profile updated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-logo/{business_id}")
async def upload_logo(business_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")
    try:
        import uuid
        from google.cloud import storage as gcs
        content  = await file.read()
        ext      = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
        filename = f"logos/{business_id}_{uuid.uuid4().hex[:8]}.{ext}"
        client   = gcs.Client()
        bucket   = client.bucket("oatmeal-farm-network-images")
        blob     = bucket.blob(filename)
        blob.upload_from_string(content, content_type=file.content_type)
        url = f"https://storage.googleapis.com/oatmeal-farm-network-images/{filename}"
        from sqlalchemy import text
        db.execute(text("UPDATE Business SET Logo = :url WHERE BusinessID = :bid"),
                   {"url": url, "bid": business_id})
        db.commit()
        return {"url": url}
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(500, f"Upload failed: {e}")


@router.delete("/delete/{business_id}")
def delete_business(business_id: int, db: Session = Depends(get_db)):
    try:
        from sqlalchemy import text

        # Get related IDs before deleting
        ids = db.execute(text("""
            SELECT WebsitesID, PhoneID, AddressID
            FROM Business WHERE BusinessID = :bid
        """), {"bid": business_id}).fetchone()

        if not ids:
            raise HTTPException(status_code=404, detail="Business not found")

        # 1. Delete BusinessAccess records
        db.execute(text("DELETE FROM BusinessAccess WHERE BusinessID = :bid"),
                   {"bid": business_id})

        # 2. Delete Business record
        db.execute(text("DELETE FROM Business WHERE BusinessID = :bid"),
                   {"bid": business_id})

        # 3. Delete Phone if exists
        if ids.PhoneID:
            db.execute(text("DELETE FROM Phone WHERE PhoneID = :pid"),
                       {"pid": ids.PhoneID})

        # 4. Delete Website if exists
        if ids.WebsitesID:
            db.execute(text("DELETE FROM Websites WHERE WebsitesID = :wid"),
                       {"wid": ids.WebsitesID})

        # 5. Delete Address if exists
        if ids.AddressID:
            db.execute(text("DELETE FROM Address WHERE AddressID = :aid"),
                       {"aid": ids.AddressID})

        db.commit()
        return {"message": "Account deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))