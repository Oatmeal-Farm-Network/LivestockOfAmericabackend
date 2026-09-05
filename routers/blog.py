from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from auth import get_current_user
from business_access import require_business
from pydantic import BaseModel
from typing import Optional
import re
import uuid
from datetime import datetime
from routers.translation import translate_fields, translate_list

router = APIRouter(prefix="/api/blog", tags=["blog"])


def _slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s]+', '-', s)
    return s[:200]


# Run schema migration only once per process lifetime
_schema_migrated = False

def _ensure_schema(db: Session):
    """
    Add any missing columns to blog / blogcategories / blogphotos.
    Runs once per server process; each ALTER is wrapped independently
    so a single failure never aborts the rest.
    """
    global _schema_migrated
    if _schema_migrated:
        return

    # Create blogauthors table if it doesn't exist
    try:
        db.execute(text("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'blogauthors')
            CREATE TABLE blogauthors (
                AuthorID  INT IDENTITY(1,1) PRIMARY KEY,
                BusinessID INT NULL,
                Name      NVARCHAR(200) NOT NULL,
                Bio       NVARCHAR(MAX) NULL,
                AvatarURL NVARCHAR(500) NULL,
                AuthorLink NVARCHAR(500) NULL,
                Slug      NVARCHAR(200) NULL,
                CreatedAt DATETIME NULL,
                UpdatedAt DATETIME NULL
            )
        """))
        db.commit()
    except Exception:
        db.rollback()

    additions = [
        # blog
        ("blog", "BusinessID",  "INT NULL"),
        ("blog", "Title",       "NVARCHAR(500) NULL"),
        ("blog", "Slug",        "NVARCHAR(500) NULL"),
        ("blog", "CoverImage",  "NVARCHAR(500) NULL"),
        ("blog", "Content",     "NVARCHAR(MAX) NULL"),
        ("blog", "IsPublished", "BIT NOT NULL DEFAULT 0"),
        ("blog", "IsFeatured",  "BIT NOT NULL DEFAULT 0"),
        ("blog", "CreatedAt",    "DATETIME NULL"),
        ("blog", "UpdatedAt",    "DATETIME NULL"),
        ("blog", "PublishedAt",        "DATETIME NULL"),
        ("blog", "CustomCatID",        "INT NULL"),
        ("blog", "AuthorID",           "INT NULL"),       # FK to blogauthors
        ("blog", "ShowOnDirectory",    "BIT NOT NULL DEFAULT 1"),
        ("blog", "ShowOnWebsite",      "BIT NOT NULL DEFAULT 1"),
        # blogcategories
        ("blogcategories", "BusinessID", "INT NULL"),
        ("blogcategories", "IsGlobal",   "BIT NOT NULL DEFAULT 0"),
        ("blogcategories", "IsActive",   "BIT NOT NULL DEFAULT 1"),
        ("blogcategories", "CreatedAt",  "DATETIME NULL"),
        # blogphotos
        ("blogphotos", "BlogID",       "INT NULL"),
        ("blogphotos", "ImageCaption", "NVARCHAR(500) NULL"),
    ]
    for table, col, col_def in additions:
        try:
            # Check existence and ALTER in two separate round-trips —
            # SQL Server / pyodbc does not reliably execute
            # IF NOT EXISTS + DDL as a single batch.
            exists = db.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :t AND COLUMN_NAME = :c"
            ), {"t": table, "c": col}).scalar()
            if not exists:
                db.execute(text(f"ALTER TABLE [{table}] ADD [{col}] {col_def}"))
                db.commit()
        except Exception:
            db.rollback()

    # Fix any legacy NOT NULL columns that lack a DEFAULT — so our INSERTs
    # don't have to supply them.
    legacy_defaults = [
        ('blogcategories', 'BlogCategoryDisplay', 'DF_blogcategories_display', '1'),
        ('blog',           'BlogDisplay',         'DF_blog_display',           '1'),
    ]
    for tbl, col, constraint_name, default_val in legacy_defaults:
        try:
            has_col = db.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=:t AND COLUMN_NAME=:c"
            ), {"t": tbl, "c": col}).scalar()
            if has_col:
                has_default = db.execute(text(
                    "SELECT COUNT(*) FROM sys.default_constraints "
                    "WHERE parent_object_id = OBJECT_ID(:t) "
                    "AND COL_NAME(parent_object_id, parent_column_id) = :c"
                ), {"t": tbl, "c": col}).scalar()
                if not has_default:
                    db.execute(text(
                        f"ALTER TABLE [{tbl}] ADD CONSTRAINT [{constraint_name}] "
                        f"DEFAULT {default_val} FOR [{col}]"
                    ))
                    db.commit()
        except Exception:
            db.rollback()

    # Seed global categories — use per-row existence check to prevent duplicates
    # on concurrent startup or repeated reloads.
    global_seeds = [
        ('General',        1),
        ('Farm News',      2),
        ('Recipes',        3),
        ('Seasonal',       4),
        ('Events',         5),
        ('Education',      6),
        ('Market Updates', 7),
        ('Community',      8),
    ]
    try:
        # First, remove any duplicate global categories keeping the lowest ID per name
        db.execute(text("""
            DELETE FROM blogcategories
            WHERE IsGlobal = 1
              AND BlogCatID NOT IN (
                SELECT MIN(BlogCatID)
                FROM blogcategories
                WHERE IsGlobal = 1
                GROUP BY BlogCategoryName
              )
        """))
        db.commit()
        # Then insert any missing seed categories
        for name, order in global_seeds:
            exists = db.execute(text(
                "SELECT COUNT(*) FROM blogcategories "
                "WHERE IsGlobal = 1 AND BlogCategoryName = :name"
            ), {"name": name}).scalar()
            if not exists:
                db.execute(text("""
                    INSERT INTO blogcategories
                        (BusinessID, IsGlobal, BlogCategoryName, BlogCategoryOrder, IsActive, CreatedAt)
                    VALUES (NULL, 1, :name, :ord, 1, GETDATE())
                """), {"name": name, "ord": order})
        db.commit()
    except Exception:
        db.rollback()

    _schema_migrated = True


class PostIn(BaseModel):
    title: str
    content: Optional[str] = None
    cover_image: Optional[str] = None
    author: Optional[str] = None
    author_link: Optional[str] = None
    author_id: Optional[int] = None      # FK to blogauthors
    blog_cat_id: Optional[int] = None
    custom_cat_id: Optional[int] = None
    is_published: bool = False
    is_featured: bool = False
    published_at: Optional[str] = None
    show_on_directory: bool = True
    show_on_website: bool = True


class AuthorIn(BaseModel):
    name: str
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    author_link: Optional[str] = None


def _author_row(r) -> dict:
    return {
        "author_id":   r.AuthorID,
        "business_id": r.BusinessID,
        "name":        r.Name,
        "bio":         r.Bio,
        "avatar_url":  r.AvatarURL or '',
        "author_link": r.AuthorLink or '',
        "slug":        r.Slug or '',
        "created_at":  str(r.CreatedAt) if r.CreatedAt else None,
        "updated_at":  str(r.UpdatedAt) if r.UpdatedAt else None,
    }


class CategoryIn(BaseModel):
    name: str
    description: Optional[str] = None
    order: int = 0


class PhotoIn(BaseModel):
    blog_id: int
    image: str
    image_title: Optional[str] = None
    image_caption: Optional[str] = None
    photo_order: int = 0


def _post_row(r) -> dict:
    return {
        "blog_id":       r.BlogID,
        "business_id":   r.BusinessID,
        "blog_cat_id":   r.BlogCatID,
        "custom_cat_id": getattr(r, "CustomCatID", None),
        "author_id":     getattr(r, "AuthorID", None),
        "title":         r.Title,
        "slug":          r.Slug,
        "author":        r.Author,
        "author_link":   r.AuthorLink,
        "cover_image":   r.CoverImage,
        "content":       r.Content,
        "is_published":  bool(r.IsPublished),
        "is_featured":        bool(r.IsFeatured),
        "show_on_directory":  bool(getattr(r, "ShowOnDirectory", True)),
        "show_on_website":    bool(getattr(r, "ShowOnWebsite",   True)),
        "published_at":  str(getattr(r, "PublishedAt", None)) if getattr(r, "PublishedAt", None) else None,
        "created_at":    str(r.CreatedAt) if r.CreatedAt else None,
        "updated_at":    str(r.UpdatedAt) if r.UpdatedAt else None,
    }


# ── Global / network categories ─────────────────────────────────

@router.get("/categories/global")
def list_global_categories(db: Session = Depends(get_db)):
    """Return network-wide categories (IsGlobal=1)."""
    _ensure_schema(db)
    rows = db.execute(text("""
        SELECT BlogCatID, BlogCategoryName, BlogCategoryDescription, BlogCategoryOrder
        FROM blogcategories
        WHERE IsGlobal = 1 AND IsActive = 1
        ORDER BY BlogCategoryOrder, BlogCategoryName
    """)).fetchall()
    return [{"id": r.BlogCatID, "name": r.BlogCategoryName,
             "description": r.BlogCategoryDescription, "order": r.BlogCategoryOrder}
            for r in rows]


# ── Business-specific custom categories ─────────────────────────

@router.get("/categories/custom")
def list_custom_categories(business_id: int, db: Session = Depends(get_db)):
    """Return a business's own custom categories."""
    _ensure_schema(db)
    rows = db.execute(text("""
        SELECT BlogCatID, BlogCategoryName, BlogCategoryDescription, BlogCategoryOrder, IsActive
        FROM blogcategories
        WHERE BusinessID = :bid AND IsGlobal = 0
        ORDER BY BlogCategoryOrder, BlogCategoryName
    """), {"bid": business_id}).fetchall()
    return [{"id": r.BlogCatID, "name": r.BlogCategoryName,
             "description": r.BlogCategoryDescription,
             "order": r.BlogCategoryOrder, "is_active": bool(r.IsActive)}
            for r in rows]


@router.post("/categories/custom", dependencies=[Depends(require_business)])
def create_custom_category(business_id: int, body: CategoryIn, db: Session = Depends(get_db)):
    """Add a custom category for a business."""
    _ensure_schema(db)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Category name required")
    existing = db.execute(text("""
        SELECT BlogCatID FROM blogcategories
        WHERE BusinessID = :bid AND IsGlobal = 0 AND LOWER(BlogCategoryName) = LOWER(:name)
    """), {"bid": business_id, "name": name}).fetchone()
    if existing:
        raise HTTPException(409, "Category already exists")
    result = db.execute(text("""
        INSERT INTO blogcategories (BusinessID, IsGlobal, BlogCategoryName, BlogCategoryDescription,
                                    BlogCategoryOrder, IsActive, CreatedAt)
        OUTPUT INSERTED.BlogCatID
        VALUES (:bid, 0, :name, :desc, :ord, 1, GETDATE())
    """), {"bid": business_id, "name": name, "desc": body.description, "ord": body.order})
    cat_id = result.fetchone()[0]
    db.commit()
    return {"id": cat_id, "name": name}


@router.put("/categories/custom/{cat_id}", dependencies=[Depends(require_business)])
def update_custom_category(cat_id: int, business_id: int, body: CategoryIn,
                            db: Session = Depends(get_db)):
    """Rename / reorder a custom category."""
    _ensure_schema(db)
    result = db.execute(text("""
        UPDATE blogcategories
        SET BlogCategoryName=:name, BlogCategoryDescription=:desc, BlogCategoryOrder=:ord
        WHERE BlogCatID=:cid AND BusinessID=:bid AND IsGlobal=0
    """), {"cid": cat_id, "bid": business_id, "name": body.name.strip(),
           "desc": body.description, "ord": body.order})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Category not found")
    return {"id": cat_id, "name": body.name.strip()}


@router.delete("/categories/custom/{cat_id}", dependencies=[Depends(require_business)])
def delete_custom_category(cat_id: int, business_id: int, db: Session = Depends(get_db)):
    """Delete a custom category (must belong to this business)."""
    _ensure_schema(db)
    result = db.execute(text("""
        DELETE FROM blogcategories WHERE BlogCatID=:cid AND BusinessID=:bid AND IsGlobal=0
    """), {"cid": cat_id, "bid": business_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Category not found")
    return {"deleted": cat_id}


# ── Public post endpoints ────────────────────────────────────────

# country.country_id for the countries Livestock of America covers.
NORTH_AMERICA_COUNTRY_IDS = (1228, 1039, 1140, 1086)  # USA, Canada, Mexico, Greenland

# Whether a post's business sits in North America. Three signals are checked
# because no single one is reliable across the legacy data:
#
#   Address.country_id      cleanest and best populated, but blank on ~40 rows
#   Address.AddressCountry  free text: 'USA', 'CANADA', 'CAN', 'United States
#                           (USA)', and 45 rows holding a bare id like '1039'.
#                           Matched against an explicit list rather than a
#                           LIKE, so 'United Kingdom' cannot slip through.
#   Address.StateIndex      resolved through state_province; this is what
#                           rescues the Oregon businesses that carry a state
#                           but no country at all.
#
# EXISTS rather than a join: a join on state_province would risk duplicating
# post rows, and this only ever needs a yes/no answer.
_NORTH_AMERICA_SQL = """
EXISTS (
    SELECT 1 FROM Address na_a
    WHERE na_a.AddressID = biz.AddressID
      AND (
            TRY_CAST(na_a.country_id AS INT) IN (1228, 1039, 1140, 1086)
         OR TRY_CAST(na_a.AddressCountry AS INT) IN (1228, 1039, 1140, 1086)
         OR UPPER(LTRIM(RTRIM(na_a.AddressCountry))) IN (
                'USA','US','U.S.','U.S.A.','UNITED STATES','UNITED STATES OF AMERICA',
                'UNITED STATES (USA)','AMERICA',
                'CANADA','CAN','CA',
                'MEXICO','MEX','MX',
                'GREENLAND','GRL','GL','KALAALLIT NUNAAT'
            )
         OR EXISTS (
                SELECT 1 FROM state_province na_sp
                WHERE na_sp.StateIndex = na_a.StateIndex
                  AND TRY_CAST(na_sp.country_id AS INT) IN (1228, 1039, 1140, 1086)
            )
      )
)
"""


@router.get("/posts")
def list_public_posts(
    business_id: Optional[int] = None,
    blog_cat_id: Optional[int] = None,
    category_name: Optional[str] = None,
    featured_only: bool = False,
    show_on_website: bool = False,
    north_america: bool = False,
    limit: int = 20,
    offset: int = 0,
    lang: str = "en",
    db: Session = Depends(get_db)
):
    """List posts, optionally filtered by business, category, or featured.
    By default filters to IsPublished=1 (public directory/listing).
    Pass show_on_website=true to instead filter by ShowOnWebsite=1 (the
    per-business custom website toggle used by WebsiteBuilder).

    Pass north_america=true to keep only posts whose business is in the USA,
    Canada, Mexico or Greenland. Opt-in rather than always-on so a business
    viewing its own blog still sees its posts wherever it is based. A business
    with no resolvable country is treated as outside the region and excluded."""
    _ensure_schema(db)
    where = ["b.ShowOnWebsite = 1"] if show_on_website else ["b.IsPublished = 1"]
    params: dict = {"limit": limit, "offset": offset}

    if business_id is not None:
        where.append("b.BusinessID = :bid")
        params["bid"] = business_id
    if blog_cat_id is not None:
        where.append("b.BlogCatID = :cid")
        params["cid"] = blog_cat_id
    if category_name:
        where.append("(bc.BlogCategoryName = :cname OR cc.BlogCategoryName = :cname)")
        params["cname"] = category_name
    if featured_only:
        where.append("b.IsFeatured = 1")
    if north_america:
        where.append(_NORTH_AMERICA_SQL)

    where_sql = " AND ".join(where)
    rows = db.execute(text(f"""
        SELECT b.BlogID, b.BusinessID, b.BlogCatID, b.CustomCatID,
               b.Title, b.Slug, b.Author, b.AuthorLink, b.AuthorID, b.CoverImage, b.Content,
               b.IsPublished, b.IsFeatured, b.ShowOnDirectory, b.ShowOnWebsite, b.PublishedAt, b.CreatedAt, b.UpdatedAt,
               biz.BusinessName,
               bc.BlogCategoryName,
               cc.BlogCategoryName AS CustomCategoryName
        FROM blog b
        JOIN Business biz ON biz.BusinessID = b.BusinessID
        LEFT JOIN blogcategories bc ON bc.BlogCatID = b.BlogCatID
        LEFT JOIN blogcategories cc ON cc.BlogCatID = b.CustomCatID
        WHERE {where_sql}
        ORDER BY b.IsFeatured DESC, b.CreatedAt DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """), params).fetchall()

    _BLOG_FIELDS = ["Title", "Content"]
    posts = [
        {
            **_post_row(r),
            "business_name":      r.BusinessName,
            "category_name":      r.BlogCategoryName,
            "custom_category_name": r.CustomCategoryName,
        }
        for r in rows
    ]
    return translate_list(posts, _BLOG_FIELDS, lang, db)


@router.get("/posts/{blog_id}")
def get_post(blog_id: int, lang: str = "en", db: Session = Depends(get_db)):
    """Get a single published post with its photos."""
    _ensure_schema(db)
    row = db.execute(text("""
        SELECT b.BlogID, b.BusinessID, b.BlogCatID, b.CustomCatID,
               b.Title, b.Slug, b.Author, b.AuthorLink, b.AuthorID, b.CoverImage, b.Content,
               b.IsPublished, b.IsFeatured, b.ShowOnDirectory, b.ShowOnWebsite, b.PublishedAt, b.CreatedAt, b.UpdatedAt,
               biz.BusinessName,
               bc.BlogCategoryName,
               cc.BlogCategoryName AS CustomCategoryName
        FROM blog b
        JOIN Business biz ON biz.BusinessID = b.BusinessID
        LEFT JOIN blogcategories bc ON bc.BlogCatID = b.BlogCatID
        LEFT JOIN blogcategories cc ON cc.BlogCatID = b.CustomCatID
        WHERE b.BlogID = :id AND b.IsPublished = 1
    """), {"id": blog_id}).fetchone()
    if not row:
        raise HTTPException(404, "Post not found")

    photos = db.execute(text("""
        SELECT PhotoID, PhotoOrder, Image, ImageTitle, ImageCaption
        FROM blogphotos WHERE BlogID = :id ORDER BY PhotoOrder
    """), {"id": blog_id}).fetchall()

    post = {
        **_post_row(row),
        "business_name":        row.BusinessName,
        "category_name":        row.BlogCategoryName,
        "custom_category_name": row.CustomCategoryName,
        "photos": [
            {"photo_id": p.PhotoID, "order": p.PhotoOrder,
             "image": p.Image, "title": p.ImageTitle, "caption": p.ImageCaption}
            for p in photos
        ],
    }
    return translate_fields(post, ["Title", "Content"], lang, db)


# ── Management endpoints ─────────────────────────────────────────

@router.get("/manage", dependencies=[Depends(require_business)])
def manage_list(business_id: int, db: Session = Depends(get_db)):
    """List all posts (published + drafts) for a business."""
    _ensure_schema(db)
    rows = db.execute(text("""
        SELECT b.BlogID, b.BusinessID, b.BlogCatID, b.CustomCatID,
               b.Title, b.Slug, b.Author, b.AuthorLink, b.AuthorID, b.CoverImage, b.Content,
               b.IsPublished, b.IsFeatured, b.ShowOnDirectory, b.ShowOnWebsite, b.PublishedAt, b.CreatedAt, b.UpdatedAt,
               bc.BlogCategoryName,
               cc.BlogCategoryName AS CustomCategoryName
        FROM blog b
        LEFT JOIN blogcategories bc ON bc.BlogCatID = b.BlogCatID
        LEFT JOIN blogcategories cc ON cc.BlogCatID = b.CustomCatID
        WHERE b.BusinessID = :bid
        ORDER BY COALESCE(b.PublishedAt, b.CreatedAt) DESC
    """), {"bid": business_id}).fetchall()
    return [
        {**_post_row(r), "category_name": r.BlogCategoryName,
         "custom_category_name": r.CustomCategoryName}
        for r in rows
    ]


@router.post("/manage", dependencies=[Depends(require_business)])
def create_post(business_id: int, body: PostIn, db: Session = Depends(get_db)):
    """Create a new blog post."""
    _ensure_schema(db)
    slug = _slugify(body.title)
    now = datetime.utcnow()
    result = db.execute(text("""
        INSERT INTO blog
            (BusinessID, BlogCatID, CustomCatID, Title, Slug, Author, AuthorLink, AuthorID,
             CoverImage, Content, IsPublished, IsFeatured, ShowOnDirectory, ShowOnWebsite,
             PublishedAt, CreatedAt, UpdatedAt)
        OUTPUT INSERTED.BlogID
        VALUES
            (:bid, :cat, :ccat, :title, :slug, :author, :author_link, :author_id,
             :cover, :content, :pub, :feat, :dir, :web, :published_at, :now, :now)
    """), {
        "bid":          business_id,
        "cat":          body.blog_cat_id,
        "ccat":         body.custom_cat_id,
        "title":        body.title,
        "slug":         slug,
        "author":       body.author,
        "author_link":  body.author_link,
        "author_id":    body.author_id,
        "cover":        body.cover_image,
        "content":      body.content,
        "pub":          1 if body.is_published else 0,
        "feat":         1 if body.is_featured else 0,
        "dir":          1 if body.show_on_directory else 0,
        "web":          1 if body.show_on_website else 0,
        "published_at": body.published_at or None,
        "now":          now,
    })
    blog_id = result.fetchone()[0]
    db.commit()
    return {"blog_id": blog_id, "slug": slug}


@router.put("/manage/{blog_id}", dependencies=[Depends(require_business)])
def update_post(blog_id: int, business_id: int, body: PostIn, db: Session = Depends(get_db)):
    """Update a blog post."""
    _ensure_schema(db)
    slug = _slugify(body.title)
    result = db.execute(text("""
        UPDATE blog
        SET BlogCatID=:cat, CustomCatID=:ccat, Title=:title, Slug=:slug,
            Author=:author, AuthorLink=:author_link, AuthorID=:author_id, CoverImage=:cover,
            Content=:content, IsPublished=:pub, IsFeatured=:feat,
            ShowOnDirectory=:dir, ShowOnWebsite=:web,
            PublishedAt=:published_at, UpdatedAt=:now
        WHERE BlogID=:id AND BusinessID=:bid
    """), {
        "id":           blog_id,
        "bid":          business_id,
        "cat":          body.blog_cat_id,
        "ccat":         body.custom_cat_id,
        "title":        body.title,
        "slug":         slug,
        "author":       body.author,
        "author_link":  body.author_link,
        "author_id":    body.author_id,
        "cover":        body.cover_image,
        "content":      body.content,
        "pub":          1 if body.is_published else 0,
        "feat":         1 if body.is_featured else 0,
        "dir":          1 if body.show_on_directory else 0,
        "web":          1 if body.show_on_website else 0,
        "published_at": body.published_at or None,
        "now":          datetime.utcnow(),
    })
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Post not found")
    return {"blog_id": blog_id, "slug": slug}


@router.delete("/manage/{blog_id}", dependencies=[Depends(require_business)])
def delete_post(blog_id: int, business_id: int, db: Session = Depends(get_db)):
    """Delete a blog post and its photos."""
    _ensure_schema(db)
    db.execute(text("DELETE FROM blogphotos WHERE BlogID=:id"), {"id": blog_id})
    result = db.execute(text(
        "DELETE FROM blog WHERE BlogID=:id AND BusinessID=:bid"
    ), {"id": blog_id, "bid": business_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Post not found")
    return {"deleted": blog_id}


# ── Photo management ─────────────────────────────────────────────

@router.get("/manage/{blog_id}/photos", dependencies=[Depends(require_business)])
def list_photos(blog_id: int, business_id: int, db: Session = Depends(get_db)):
    """List photos for a post (verifies business ownership)."""
    _ensure_schema(db)
    owner = db.execute(text(
        "SELECT BlogID FROM blog WHERE BlogID=:id AND BusinessID=:bid"
    ), {"id": blog_id, "bid": business_id}).fetchone()
    if not owner:
        raise HTTPException(404, "Post not found")
    rows = db.execute(text("""
        SELECT PhotoID, BlogID, PhotoOrder, Image, ImageTitle, ImageCaption
        FROM blogphotos WHERE BlogID=:id ORDER BY PhotoOrder
    """), {"id": blog_id}).fetchall()
    return [{"photo_id": r.PhotoID, "blog_id": r.BlogID, "order": r.PhotoOrder,
             "image": r.Image, "title": r.ImageTitle, "caption": r.ImageCaption}
            for r in rows]


@router.post("/manage/{blog_id}/photos", dependencies=[Depends(require_business)])
def add_photo(blog_id: int, business_id: int, body: PhotoIn,
              db: Session = Depends(get_db)):
    """Add a photo to a post."""
    _ensure_schema(db)
    owner = db.execute(text(
        "SELECT BlogID FROM blog WHERE BlogID=:id AND BusinessID=:bid"
    ), {"id": blog_id, "bid": business_id}).fetchone()
    if not owner:
        raise HTTPException(404, "Post not found")
    result = db.execute(text("""
        INSERT INTO blogphotos (BlogID, PhotoOrder, Image, ImageTitle, ImageCaption)
        OUTPUT INSERTED.PhotoID
        VALUES (:bid, :ord, :img, :title, :cap)
    """), {"bid": blog_id, "ord": body.photo_order,
           "img": body.image, "title": body.image_title, "cap": body.image_caption})
    photo_id = result.fetchone()[0]
    db.commit()
    return {"photo_id": photo_id}


@router.delete("/manage/{blog_id}/photos/{photo_id}", dependencies=[Depends(require_business)])
def delete_photo(blog_id: int, photo_id: int, business_id: int,
                 db: Session = Depends(get_db)):
    """Delete a photo."""
    _ensure_schema(db)
    owner = db.execute(text(
        "SELECT BlogID FROM blog WHERE BlogID=:id AND BusinessID=:bid"
    ), {"id": blog_id, "bid": business_id}).fetchone()
    if not owner:
        raise HTTPException(404, "Post not found")
    db.execute(text(
        "DELETE FROM blogphotos WHERE PhotoID=:pid AND BlogID=:bid"
    ), {"pid": photo_id, "bid": blog_id})
    db.commit()
    return {"deleted": photo_id}


# ── Author management ────────────────────────────────────────────

@router.get("/authors")
def list_authors(business_id: int, db: Session = Depends(get_db)):
    """List all authors for a business."""
    _ensure_schema(db)
    rows = db.execute(text("""
        SELECT AuthorID, BusinessID, Name, Bio, AvatarURL, AuthorLink, Slug, CreatedAt, UpdatedAt
        FROM blogauthors WHERE BusinessID = :bid ORDER BY Name
    """), {"bid": business_id}).fetchall()
    return [_author_row(r) for r in rows]


@router.get("/authors/{author_id}")
def get_author(author_id: int, db: Session = Depends(get_db)):
    """Get a public author profile with their published posts."""
    _ensure_schema(db)
    row = db.execute(text("""
        SELECT AuthorID, BusinessID, Name, Bio, AvatarURL, AuthorLink, Slug, CreatedAt, UpdatedAt
        FROM blogauthors WHERE AuthorID = :id
    """), {"id": author_id}).fetchone()
    if not row:
        raise HTTPException(404, "Author not found")
    posts = db.execute(text("""
        SELECT b.BlogID, b.BusinessID, b.Title, b.Slug, b.CoverImage, b.Content,
               b.Author, b.AuthorLink, b.AuthorID, b.BlogCatID, b.CustomCatID,
               b.IsPublished, b.IsFeatured, b.ShowOnDirectory, b.ShowOnWebsite,
               b.PublishedAt, b.CreatedAt, b.UpdatedAt,
               biz.BusinessName,
               bc.BlogCategoryName,
               cc.BlogCategoryName AS CustomCategoryName
        FROM blog b
        JOIN Business biz ON biz.BusinessID = b.BusinessID
        LEFT JOIN blogcategories bc ON bc.BlogCatID = b.BlogCatID
        LEFT JOIN blogcategories cc ON cc.BlogCatID = b.CustomCatID
        WHERE b.AuthorID = :id AND b.IsPublished = 1
        ORDER BY COALESCE(b.PublishedAt, b.CreatedAt) DESC
    """), {"id": author_id}).fetchall()
    return {
        **_author_row(row),
        "posts": [
            {**_post_row(p), "business_name": p.BusinessName,
             "category_name": p.BlogCategoryName, "custom_category_name": p.CustomCategoryName}
            for p in posts
        ],
    }


@router.post("/authors", dependencies=[Depends(require_business)])
def create_author(business_id: int, body: AuthorIn, db: Session = Depends(get_db)):
    """Create a new author profile."""
    _ensure_schema(db)
    slug = _slugify(body.name)
    result = db.execute(text("""
        INSERT INTO blogauthors (BusinessID, Name, Bio, AvatarURL, AuthorLink, Slug, CreatedAt, UpdatedAt)
        OUTPUT INSERTED.AuthorID
        VALUES (:bid, :name, :bio, :avatar, :link, :slug, GETDATE(), GETDATE())
    """), {"bid": business_id, "name": body.name.strip(), "bio": body.bio,
           "avatar": body.avatar_url, "link": body.author_link, "slug": slug})
    author_id = result.fetchone()[0]
    db.commit()
    return {"author_id": author_id, "slug": slug}


@router.put("/authors/{author_id}", dependencies=[Depends(require_business)])
def update_author(author_id: int, business_id: int, body: AuthorIn,
                  db: Session = Depends(get_db)):
    """Update an author profile."""
    _ensure_schema(db)
    slug = _slugify(body.name)
    result = db.execute(text("""
        UPDATE blogauthors
        SET Name=:name, Bio=:bio, AvatarURL=:avatar, AuthorLink=:link,
            Slug=:slug, UpdatedAt=GETDATE()
        WHERE AuthorID=:id AND BusinessID=:bid
    """), {"id": author_id, "bid": business_id, "name": body.name.strip(),
           "bio": body.bio, "avatar": body.avatar_url, "link": body.author_link, "slug": slug})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Author not found")
    return {"author_id": author_id, "slug": slug}


@router.delete("/authors/{author_id}", dependencies=[Depends(require_business)])
def delete_author(author_id: int, business_id: int, db: Session = Depends(get_db)):
    """Delete an author profile."""
    _ensure_schema(db)
    # Unlink posts that referenced this author
    db.execute(text("UPDATE blog SET AuthorID=NULL WHERE AuthorID=:id AND BusinessID=:bid"),
               {"id": author_id, "bid": business_id})
    result = db.execute(text(
        "DELETE FROM blogauthors WHERE AuthorID=:id AND BusinessID=:bid"
    ), {"id": author_id, "bid": business_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Author not found")
    return {"deleted": author_id}


GCS_BUCKET = "oatmeal-farm-network-images"

@router.post("/upload-image", dependencies=[Depends(get_current_user)])
async def upload_blog_image(file: UploadFile = File(...)):
    """Upload an image file to GCS and return its public URL."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")
    content = await file.read()
    ext = (file.filename or "img").rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "gif", "webp", "avif", "svg"}:
        ext = "jpg"
    filename = f"{uuid.uuid4().hex}.{ext}"
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(f"blog/{filename}")
        blob.upload_from_string(content, content_type=file.content_type)
        url = f"https://storage.googleapis.com/{GCS_BUCKET}/blog/{filename}"
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {e}")
    return {"url": url}
