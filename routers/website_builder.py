from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from database import get_db, engine, Base
from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel
import models, json, re, uuid

router = APIRouter(prefix="/api/website", tags=["website-builder"])

# Import-time schema bootstrap. Guarded so a DB outage at startup can't crash the
# whole container (the import would otherwise raise and uvicorn would exit(1)
# before binding the port). DDL is idempotent, so it re-runs safely next boot.
try:
    Base.metadata.create_all(
        bind=engine,
        tables=[
            models.BusinessWebsite.__table__,
            models.BusinessWebPage.__table__,
            models.BusinessWebBlock.__table__,
        ],
        checkfirst=True,
    )

    # Auto-create supplemental tables
    with engine.connect() as _conn:
        _conn.execute(text("""
        IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'WebsiteHeaderImages')
        CREATE TABLE WebsiteHeaderImages (
            HeaderImageID INT IDENTITY(1,1) PRIMARY KEY,
            WebsiteID     INT NOT NULL,
            ImageURL      NVARCHAR(500) NOT NULL,
            StartDate     DATE,
            EndDate       DATE,
            SortOrder     INT DEFAULT 0,
            CreatedAt     DATETIME DEFAULT GETDATE()
        )
        """))
        _conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'WebsiteVersionHistory')
            CREATE TABLE WebsiteVersionHistory (
                VersionID    INT IDENTITY(1,1) PRIMARY KEY,
                WebsiteID    INT NOT NULL,
                VersionLabel NVARCHAR(255),
                SnapshotJSON NVARCHAR(MAX) NOT NULL,
                CreatedAt    DATETIME DEFAULT GETDATE()
            )
        """))
        # Add new design columns to BusinessWebsite if they don't exist yet
        for col_ddl in [
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='HeaderContentWidth') ALTER TABLE BusinessWebsite ADD HeaderContentWidth NVARCHAR(20) DEFAULT '100%'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='BodyContentWidth') ALTER TABLE BusinessWebsite ADD BodyContentWidth NVARCHAR(20) DEFAULT '100%'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='BodyBgWidth') ALTER TABLE BusinessWebsite ADD BodyBgWidth NVARCHAR(20) DEFAULT '100%'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FooterContentWidth') ALTER TABLE BusinessWebsite ADD FooterContentWidth NVARCHAR(20) DEFAULT '100%'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='TopBarEnabled') ALTER TABLE BusinessWebsite ADD TopBarEnabled BIT DEFAULT 0",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='TopBarHTML') ALTER TABLE BusinessWebsite ADD TopBarHTML NVARCHAR(MAX)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='TopBarBgColor') ALTER TABLE BusinessWebsite ADD TopBarBgColor NVARCHAR(20) DEFAULT '#f8f5ef'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='TopBarTextColor') ALTER TABLE BusinessWebsite ADD TopBarTextColor NVARCHAR(20) DEFAULT '#333333'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='TopBarAlign') ALTER TABLE BusinessWebsite ADD TopBarAlign NVARCHAR(10) DEFAULT 'right'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='HeaderBannerURL') ALTER TABLE BusinessWebsite ADD HeaderBannerURL NVARCHAR(1000)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='HeaderHeight') ALTER TABLE BusinessWebsite ADD HeaderHeight INT DEFAULT 120",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='ShowSiteName') ALTER TABLE BusinessWebsite ADD ShowSiteName BIT DEFAULT 1",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='NavBgImageURL') ALTER TABLE BusinessWebsite ADD NavBgImageURL NVARCHAR(1000)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FooterBgImageURL') ALTER TABLE BusinessWebsite ADD FooterBgImageURL NVARCHAR(1000)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FooterHTML') ALTER TABLE BusinessWebsite ADD FooterHTML NVARCHAR(MAX)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FooterHeight') ALTER TABLE BusinessWebsite ADD FooterHeight INT DEFAULT 200",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='BgImageURL') ALTER TABLE BusinessWebsite ADD BgImageURL NVARCHAR(1000)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='BgGradient') ALTER TABLE BusinessWebsite ADD BgGradient NVARCHAR(500)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='HeaderBgWidth') ALTER TABLE BusinessWebsite ADD HeaderBgWidth NVARCHAR(20) DEFAULT '100%'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FooterBgWidth') ALTER TABLE BusinessWebsite ADD FooterBgWidth NVARCHAR(20) DEFAULT '100%'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebPage' AND COLUMN_NAME='ParentPageID') ALTER TABLE BusinessWebPage ADD ParentPageID INT NULL",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebPage' AND COLUMN_NAME='IsNavHeading') ALTER TABLE BusinessWebPage ADD IsNavHeading BIT NOT NULL DEFAULT 0",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebPage' AND COLUMN_NAME='LinkURL') ALTER TABLE BusinessWebPage ADD LinkURL NVARCHAR(500) NULL",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='DropdownBgColor') ALTER TABLE BusinessWebsite ADD DropdownBgColor NVARCHAR(50)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='DropdownHoverColor') ALTER TABLE BusinessWebsite ADD DropdownHoverColor NVARCHAR(50)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='DropdownBgColor2') ALTER TABLE BusinessWebsite ADD DropdownBgColor2 NVARCHAR(50)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='DropdownGradientDir') ALTER TABLE BusinessWebsite ADD DropdownGradientDir NVARCHAR(20) DEFAULT '135deg'",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='HeaderBannerBgColor') ALTER TABLE BusinessWebsite ADD HeaderBannerBgColor NVARCHAR(20)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='CopyrightBarBgColor') ALTER TABLE BusinessWebsite ADD CopyrightBarBgColor NVARCHAR(20)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FaviconURL') ALTER TABLE BusinessWebsite ADD FaviconURL NVARCHAR(1000)",
            "IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='BusinessWebsite' AND COLUMN_NAME='FooterBottomRadius') ALTER TABLE BusinessWebsite ADD FooterBottomRadius INT DEFAULT 0",
        ]:
            _conn.execute(text(col_ddl))

        # ── WebsiteCustomDomain — indexed custom-domain lookup table ──────────────
        # One row per bare domain (e.g. "alpacasontheweb.com").  Replaces the slow
        # LIKE '%domain%' full-table scan on CanonicalURL that times out on cold starts.
        _conn.execute(text("""
            IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'WebsiteCustomDomain')
            BEGIN
                CREATE TABLE WebsiteCustomDomain (
                    DomainID  INT IDENTITY(1,1) PRIMARY KEY,
                    WebsiteID INT NOT NULL,
                    Domain    NVARCHAR(255) NOT NULL,
                    IsActive  BIT NOT NULL DEFAULT 1,
                    CreatedAt DATETIME DEFAULT GETDATE(),
                    CONSTRAINT UQ_WebsiteCustomDomain_Domain UNIQUE (Domain)
                );
                CREATE INDEX IX_WebsiteCustomDomain_WebsiteID ON WebsiteCustomDomain(WebsiteID);
            END
        """))

        # Back-fill from existing CanonicalURL values (idempotent — skips conflicts).
        _conn.execute(text("""
            INSERT INTO WebsiteCustomDomain (WebsiteID, Domain)
            SELECT WebsiteID,
                   LOWER(
                     LTRIM(RTRIM(
                       REPLACE(REPLACE(REPLACE(REPLACE(CanonicalURL,
                         'https://', ''), 'http://', ''), 'www.', ''), '/', '')
                     ))
                   )
            FROM BusinessWebsite
            WHERE CanonicalURL IS NOT NULL AND LEN(LTRIM(RTRIM(CanonicalURL))) > 0
            AND LOWER(LTRIM(RTRIM(REPLACE(REPLACE(REPLACE(REPLACE(CanonicalURL,
                  'https://', ''), 'http://', ''), 'www.', ''), '/', '')))) NOT IN (
                SELECT Domain FROM WebsiteCustomDomain
            )
        """))

        _conn.commit()
except Exception as e:
    print(f"[website_builder] schema bootstrap skipped (DB unavailable): {e}")


def _normalize_domain(raw: str) -> str:
    """Return a bare lowercase domain: strips protocol, www., and trailing slashes."""
    d = raw.lower().strip()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.rstrip("/")
    if d.startswith("www."):
        d = d[4:]
    return d


def _upsert_custom_domain(db: Session, website_id: int, canonical_url: str | None) -> None:
    """Register or update the custom domain for a site (called on create/update)."""
    if not canonical_url:
        return
    bare = _normalize_domain(canonical_url)
    if not bare:
        return
    try:
        db.execute(text("""
            MERGE WebsiteCustomDomain AS target
            USING (SELECT :wid AS WebsiteID, :dom AS Domain) AS src
            ON target.Domain = src.Domain
            WHEN MATCHED THEN UPDATE SET WebsiteID = src.WebsiteID, IsActive = 1
            WHEN NOT MATCHED THEN INSERT (WebsiteID, Domain, IsActive, CreatedAt)
                                  VALUES (src.WebsiteID, src.Domain, 1, GETDATE());
        """), {"wid": website_id, "dom": bare})
        db.commit()
    except Exception:
        db.rollback()


# ── Pydantic models ──────────────────────────────────────────────

class SiteCreate(BaseModel):
    business_id: int
    site_name: str
    slug: str
    tagline: Optional[str] = None
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    primary_color: Optional[str] = '#3D6B34'
    secondary_color: Optional[str] = '#819360'
    accent_color: Optional[str] = '#FFC567'
    bg_color: Optional[str] = '#FFFFFF'
    screen_background_color: Optional[str] = None
    page_background_color: Optional[str] = None
    text_color: Optional[str] = '#111827'
    font_family: Optional[str] = 'Inter, sans-serif'
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    facebook_url: Optional[str] = None
    instagram_url: Optional[str] = None
    twitter_url: Optional[str] = None
    nav_text_color: Optional[str] = '#FFFFFF'
    footer_bg_color: Optional[str] = None
    copyright_text: Optional[str] = None
    is_published: Optional[bool] = False
    meta_title: Optional[str] = None
    canonical_url: Optional[str] = None
    og_image_url: Optional[str] = None
    seo_extras_json: Optional[str] = None
    menu_style_json: Optional[str] = None
    footer_json: Optional[str] = None
    # Width controls
    header_bg_width: Optional[str] = '100%'
    header_content_width: Optional[str] = '100%'
    body_content_width: Optional[str] = '100%'
    body_bg_width: Optional[str] = '100%'
    footer_content_width: Optional[str] = '100%'
    footer_bg_width: Optional[str] = '100%'
    # Typography / type scale
    h1_size: Optional[str] = '40px'
    h1_weight: Optional[str] = '800'
    h1_color: Optional[str] = ''
    h1_align: Optional[str] = 'left'
    h1_underline: Optional[bool] = False
    h1_italic: Optional[bool] = False
    h1_rule: Optional[bool] = False
    h1_rule_color: Optional[str] = ''
    h2_size: Optional[str] = '29px'
    h2_weight: Optional[str] = '700'
    h2_color: Optional[str] = ''
    h2_align: Optional[str] = 'left'
    h2_underline: Optional[bool] = False
    h2_italic: Optional[bool] = False
    h2_rule: Optional[bool] = False
    h2_rule_color: Optional[str] = ''
    h3_size: Optional[str] = '21px'
    h3_weight: Optional[str] = '600'
    h3_color: Optional[str] = ''
    h3_align: Optional[str] = 'left'
    h3_underline: Optional[bool] = False
    h3_italic: Optional[bool] = False
    h3_rule: Optional[bool] = False
    h3_rule_color: Optional[str] = ''
    h4_size: Optional[str] = '17px'
    h4_weight: Optional[str] = '600'
    h4_color: Optional[str] = ''
    h4_align: Optional[str] = 'left'
    h4_underline: Optional[bool] = False
    h4_italic: Optional[bool] = False
    h4_rule: Optional[bool] = False
    h4_rule_color: Optional[str] = ''
    h1_margin_top: Optional[int] = 0
    h1_margin_bottom: Optional[int] = 8
    h1_font: Optional[str] = ''
    h2_margin_top: Optional[int] = 0
    h2_margin_bottom: Optional[int] = 8
    h2_font: Optional[str] = ''
    h3_margin_top: Optional[int] = 0
    h3_margin_bottom: Optional[int] = 6
    h3_font: Optional[str] = ''
    h4_margin_top: Optional[int] = 0
    h4_margin_bottom: Optional[int] = 4
    h4_font: Optional[str] = ''
    body_size: Optional[str] = '16px'
    body_line_height: Optional[str] = '1.75'
    body_color: Optional[str] = ''
    body_align: Optional[str] = 'left'
    body_underline: Optional[bool] = False
    body_italic: Optional[bool] = False
    # Site-wide image styling
    image_border_radius: Optional[int] = 0
    image_shadow_enabled: Optional[bool] = False
    image_shadow_color: Optional[str] = 'rgba(0,0,0,0.35)'
    image_shadow_distance: Optional[int] = 4
    image_shadow_blur: Optional[int] = 8
    image_shadow_angle: Optional[int] = 135
    body_margin_top: Optional[int] = 0
    body_margin_bottom: Optional[int] = 12
    body_font: Optional[str] = ''
    link_color: Optional[str] = ''
    link_underline: Optional[bool] = True
    dropdown_bg_color: Optional[str] = None
    dropdown_hover_color: Optional[str] = None
    dropdown_bg_color2: Optional[str] = None
    dropdown_gradient_dir: Optional[str] = '135deg'
    # Top bar
    top_bar_enabled: Optional[bool] = False
    top_bar_html: Optional[str] = None
    top_bar_bg_color: Optional[str] = '#f8f5ef'
    top_bar_text_color: Optional[str] = '#333333'
    top_bar_align: Optional[str] = 'right'
    # Header banner
    header_banner_url: Optional[str] = None
    header_height: Optional[int] = 120
    show_site_name: Optional[bool] = True
    header_layout: Optional[str] = 'banner_top'
    # Nav bar
    nav_bg_image_url: Optional[str] = None
    # Footer
    footer_bg_image_url: Optional[str] = None
    header_content_bg_color: Optional[str] = None
    header_content_bg_image_url: Optional[str] = None
    body_bg_color: Optional[str] = None
    body_bg_image_url: Optional[str] = None
    body_content_bg_color: Optional[str] = None
    body_content_bg_image_url: Optional[str] = None
    footer_content_bg_color: Optional[str] = None
    footer_content_bg_image_url: Optional[str] = None
    footer_html: Optional[str] = None
    footer_height: Optional[int] = 200
    footer_bottom_radius: Optional[int] = 0
    # Page background
    bg_image_url: Optional[str] = None
    bg_gradient: Optional[str] = None

class SiteUpdate(SiteCreate):
    business_id: Optional[int] = None
    site_name: Optional[str] = None
    slug: Optional[str] = None
    # All non-None defaults from SiteCreate must be None here so partial saves
    # (e.g. togglePublish sending only {is_published}) never overwrite stored values
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    bg_color: Optional[str] = None
    screen_background_color: Optional[str] = None
    page_background_color: Optional[str] = None
    text_color: Optional[str] = None
    font_family: Optional[str] = None
    nav_text_color: Optional[str] = None
    header_bg_width: Optional[str] = None
    header_content_width: Optional[str] = None
    body_content_width: Optional[str] = None
    body_bg_width: Optional[str] = None
    footer_content_width: Optional[str] = None
    footer_bg_width: Optional[str] = None
    h1_size: Optional[str] = None
    h1_weight: Optional[str] = None
    h1_color: Optional[str] = None
    h1_align: Optional[str] = None
    h1_underline: Optional[bool] = None
    h1_italic: Optional[bool] = None
    h1_rule: Optional[bool] = None
    h1_rule_color: Optional[str] = None
    h2_size: Optional[str] = None
    h2_weight: Optional[str] = None
    h2_color: Optional[str] = None
    h2_align: Optional[str] = None
    h2_underline: Optional[bool] = None
    h2_italic: Optional[bool] = None
    h2_rule: Optional[bool] = None
    h2_rule_color: Optional[str] = None
    h3_size: Optional[str] = None
    h3_weight: Optional[str] = None
    h3_color: Optional[str] = None
    h3_align: Optional[str] = None
    h3_underline: Optional[bool] = None
    h3_italic: Optional[bool] = None
    h3_rule: Optional[bool] = None
    h3_rule_color: Optional[str] = None
    h4_size: Optional[str] = None
    h4_weight: Optional[str] = None
    h4_color: Optional[str] = None
    h4_align: Optional[str] = None
    h4_underline: Optional[bool] = None
    h4_italic: Optional[bool] = None
    h4_rule: Optional[bool] = None
    h4_rule_color: Optional[str] = None
    h1_margin_top: Optional[int] = None
    h1_margin_bottom: Optional[int] = None
    h1_font: Optional[str] = None
    h2_margin_top: Optional[int] = None
    h2_margin_bottom: Optional[int] = None
    h2_font: Optional[str] = None
    h3_margin_top: Optional[int] = None
    h3_margin_bottom: Optional[int] = None
    h3_font: Optional[str] = None
    h4_margin_top: Optional[int] = None
    h4_margin_bottom: Optional[int] = None
    h4_font: Optional[str] = None
    body_size: Optional[str] = None
    body_line_height: Optional[str] = None
    body_color: Optional[str] = None
    body_align: Optional[str] = None
    body_underline: Optional[bool] = None
    body_italic: Optional[bool] = None
    image_border_radius: Optional[int] = None
    image_shadow_enabled: Optional[bool] = None
    image_shadow_color: Optional[str] = None
    image_shadow_distance: Optional[int] = None
    image_shadow_blur: Optional[int] = None
    image_shadow_angle: Optional[int] = None
    body_margin_top: Optional[int] = None
    body_margin_bottom: Optional[int] = None
    body_font: Optional[str] = None
    link_color: Optional[str] = None
    link_underline: Optional[bool] = None
    dropdown_bg_color: Optional[str] = None
    dropdown_hover_color: Optional[str] = None
    dropdown_bg_color2: Optional[str] = None
    dropdown_gradient_dir: Optional[str] = None
    top_bar_enabled: Optional[bool] = None
    top_bar_bg_color: Optional[str] = None
    top_bar_text_color: Optional[str] = None
    top_bar_align: Optional[str] = None
    header_height: Optional[int] = None
    header_banner_bg_color: Optional[str] = None
    footer_height: Optional[int] = None
    footer_bottom_radius: Optional[int] = None
    copyright_bar_bg_color: Optional[str] = None
    show_site_name: Optional[bool] = None
    header_layout: Optional[str] = None
    is_published: Optional[bool] = None

class PageCreate(BaseModel):
    website_id: int
    business_id: int
    page_name: str
    slug: str
    page_title: Optional[str] = None
    meta_description: Optional[str] = None
    sort_order: Optional[int] = 0
    is_published: Optional[bool] = True
    is_home_page: Optional[bool] = False
    parent_page_id: Optional[int] = None
    is_nav_heading: Optional[bool] = False
    link_url: Optional[str] = None

class PageUpdate(BaseModel):
    page_name: Optional[str] = None
    slug: Optional[str] = None
    page_title: Optional[str] = None
    meta_description: Optional[str] = None
    sort_order: Optional[int] = None
    is_published: Optional[bool] = None
    is_home_page: Optional[bool] = None
    parent_page_id: Optional[int] = None
    is_nav_heading: Optional[bool] = None
    link_url: Optional[str] = None

class BlockCreate(BaseModel):
    page_id: int
    block_type: str
    block_data: dict
    sort_order: Optional[int] = 0

class BlockUpdate(BaseModel):
    block_type: Optional[str] = None
    block_data: Optional[dict] = None
    sort_order: Optional[int] = None

class BlockReorder(BaseModel):
    block_ids: List[int]  # ordered list of IDs


# ── Serializers ──────────────────────────────────────────────────

def _ser_site(s: models.BusinessWebsite) -> dict:
    return {
        "website_id":     s.WebsiteID,
        "business_id":    s.BusinessID,
        "site_name":      s.SiteName,
        "slug":           s.Slug,
        "tagline":        s.Tagline,
        "logo_url":       s.LogoURL,
        "favicon_url":    s.FaviconURL or '',
        "primary_color":  s.PrimaryColor or '#3D6B34',
        "secondary_color":s.SecondaryColor or '#819360',
        "accent_color":   s.AccentColor or '#FFC567',
        "bg_color":       s.BgColor or '#FFFFFF',
        "screen_background_color": s.ScreenBackgroundColor or s.BgColor or '#FFFFFF',
        "page_background_color":   s.PageBackgroundColor or '',
        "text_color":     s.TextColor or '#111827',
        "font_family":    s.FontFamily or 'Inter, sans-serif',
        "phone":          s.Phone,
        "email":          s.Email,
        "address":        s.Address,
        "facebook_url":   s.FacebookURL,
        "instagram_url":  s.InstagramURL,
        "twitter_url":    s.TwitterURL,
        "nav_text_color":  s.NavTextColor or '#FFFFFF',
        "footer_bg_color": s.FooterBgColor or s.PrimaryColor or '#3D6B34',
        "copyright_text":  s.CopyrightText,
        "is_published":    bool(s.IsPublished),
        "meta_title":      s.MetaTitle,
        "canonical_url":   s.CanonicalURL,
        "og_image_url":    s.OgImageURL,
        "seo_extras_json": s.SeoExtrasJSON,
        "menu_style_json": s.MenuStyleJSON,
        "footer_json":     getattr(s, 'FooterJSON', None),
        # Width controls
        "header_bg_width":      s.HeaderBgWidth or '100%',
        "header_content_width": s.HeaderContentWidth or '100%',
        "body_content_width":   s.BodyContentWidth or '100%',
        "body_bg_width":        s.BodyBgWidth or '100%',
        "footer_content_width": s.FooterContentWidth or '100%',
        "footer_bg_width":      s.FooterBgWidth or '100%',
        # Typography / type scale
        "h1_size":          s.H1Size or '40px',
        "h1_weight":        s.H1Weight or '800',
        "h1_color":         s.H1Color or '',
        "h1_align":         s.H1Align or 'left',
        "h1_underline":     bool(s.H1Underline) if s.H1Underline is not None else False,
        "h1_italic":        bool(getattr(s, 'H1Italic', False)) if getattr(s, 'H1Italic', None) is not None else False,
        "h1_rule":          bool(s.H1Rule) if s.H1Rule is not None else False,
        "h1_rule_color":    s.H1RuleColor or '',
        "h2_size":          s.H2Size or '29px',
        "h2_weight":        s.H2Weight or '700',
        "h2_color":         s.H2Color or '',
        "h2_align":         s.H2Align or 'left',
        "h2_underline":     bool(s.H2Underline) if s.H2Underline is not None else False,
        "h2_italic":        bool(getattr(s, 'H2Italic', False)) if getattr(s, 'H2Italic', None) is not None else False,
        "h2_rule":          bool(s.H2Rule) if s.H2Rule is not None else False,
        "h2_rule_color":    s.H2RuleColor or '',
        "h3_size":          s.H3Size or '21px',
        "h3_weight":        s.H3Weight or '600',
        "h3_color":         s.H3Color or '',
        "h3_align":         s.H3Align or 'left',
        "h3_underline":     bool(s.H3Underline) if s.H3Underline is not None else False,
        "h3_italic":        bool(getattr(s, 'H3Italic', False)) if getattr(s, 'H3Italic', None) is not None else False,
        "h3_rule":          bool(s.H3Rule) if s.H3Rule is not None else False,
        "h3_rule_color":    s.H3RuleColor or '',
        "h4_size":          s.H4Size or '17px',
        "h4_weight":        s.H4Weight or '600',
        "h4_color":         s.H4Color or '',
        "h4_align":         s.H4Align or 'left',
        "h4_underline":     bool(s.H4Underline) if s.H4Underline is not None else False,
        "h4_italic":        bool(getattr(s, 'H4Italic', False)) if getattr(s, 'H4Italic', None) is not None else False,
        "h4_rule":          bool(s.H4Rule) if s.H4Rule is not None else False,
        "h4_rule_color":    s.H4RuleColor or '',
        "h1_margin_top":    s.H1MarginTop if s.H1MarginTop is not None else 0,
        "h1_margin_bottom": s.H1MarginBottom if s.H1MarginBottom is not None else 8,
        "h1_font":          s.H1Font or '',
        "h2_margin_top":    s.H2MarginTop if s.H2MarginTop is not None else 0,
        "h2_margin_bottom": s.H2MarginBottom if s.H2MarginBottom is not None else 8,
        "h2_font":          s.H2Font or '',
        "h3_margin_top":    s.H3MarginTop if s.H3MarginTop is not None else 0,
        "h3_margin_bottom": s.H3MarginBottom if s.H3MarginBottom is not None else 6,
        "h3_font":          s.H3Font or '',
        "h4_margin_top":    s.H4MarginTop if s.H4MarginTop is not None else 0,
        "h4_margin_bottom": s.H4MarginBottom if s.H4MarginBottom is not None else 4,
        "h4_font":          s.H4Font or '',
        "body_size":        s.BodySize or '16px',
        "body_line_height": s.BodyLineHeight or '1.75',
        "body_color":       s.BodyColor or '',
        "body_align":       s.BodyAlign or 'left',
        "body_underline":   bool(s.BodyUnderline) if s.BodyUnderline is not None else False,
        "body_italic":      bool(getattr(s, 'BodyItalic', False)) if getattr(s, 'BodyItalic', None) is not None else False,
        # Site-wide image styling
        "image_border_radius":   getattr(s, 'ImageBorderRadius', 0) or 0,
        "image_shadow_enabled":  bool(getattr(s, 'ImageShadowEnabled', False)) if getattr(s, 'ImageShadowEnabled', None) is not None else False,
        "image_shadow_color":    getattr(s, 'ImageShadowColor', '') or 'rgba(0,0,0,0.35)',
        "image_shadow_distance": getattr(s, 'ImageShadowDistance', 4) if getattr(s, 'ImageShadowDistance', None) is not None else 4,
        "image_shadow_blur":     getattr(s, 'ImageShadowBlur', 8) if getattr(s, 'ImageShadowBlur', None) is not None else 8,
        "image_shadow_angle":    getattr(s, 'ImageShadowAngle', 135) if getattr(s, 'ImageShadowAngle', None) is not None else 135,
        "body_margin_top":    s.BodyMarginTop if s.BodyMarginTop is not None else 0,
        "body_margin_bottom": s.BodyMarginBottom if s.BodyMarginBottom is not None else 12,
        "body_font":          s.BodyFont or '',
        "link_color":          s.LinkColor or '',
        "link_underline":      bool(s.LinkUnderline) if s.LinkUnderline is not None else True,
        "dropdown_bg_color":    s.DropdownBgColor or '',
        "dropdown_hover_color": s.DropdownHoverColor or '',
        "dropdown_bg_color2":   s.DropdownBgColor2 or '',
        "dropdown_gradient_dir":s.DropdownGradientDir or '135deg',
        # Top bar
        "top_bar_enabled":    bool(s.TopBarEnabled) if s.TopBarEnabled is not None else False,
        "top_bar_html":       s.TopBarHTML or '',
        "top_bar_bg_color":   s.TopBarBgColor or '#f8f5ef',
        "top_bar_text_color": s.TopBarTextColor or '#333333',
        "top_bar_align":      s.TopBarAlign or 'right',
        # Header banner
        "header_banner_url":     s.HeaderBannerURL or '',
        "header_banner_bg_color": s.HeaderBannerBgColor or '',
        "header_height":         s.HeaderHeight or 120,
        "show_site_name":        bool(s.ShowSiteName) if s.ShowSiteName is not None else True,
        "header_layout":         s.HeaderLayout or 'banner_top',
        # Nav bar
        "nav_bg_image_url":   s.NavBgImageURL or '',
        # Footer
        "footer_bg_image_url":    s.FooterBgImageURL or '',
        "header_content_bg_color": s.HeaderContentBgColor or '',
        "header_content_bg_image_url": s.HeaderContentBgImageURL or '',
        "body_bg_color": s.BodyBgColor or '',
        "body_bg_image_url": s.BodyBgImageURL or '',
        "body_content_bg_color": s.BodyContentBgColor or '',
        "body_content_bg_image_url": s.BodyContentBgImageURL or '',
        "footer_content_bg_color": s.FooterContentBgColor or '',
        "footer_content_bg_image_url": s.FooterContentBgImageURL or '',
        "footer_html":            s.FooterHTML or '',
        "footer_height":          s.FooterHeight or 200,
        "footer_bottom_radius":   s.FooterBottomRadius or 0,
        "copyright_bar_bg_color": s.CopyrightBarBgColor or '',
        # Page background
        "bg_image_url":        s.BgImageURL or '',
        "bg_gradient":         s.BgGradient or '',
        "created_at":      str(s.CreatedAt) if s.CreatedAt else None,
        "updated_at":      str(s.UpdatedAt) if s.UpdatedAt else None,
    }

def _ser_page(p: models.BusinessWebPage) -> dict:
    return {
        "page_id":          p.PageID,
        "website_id":       p.WebsiteID,
        "business_id":      p.BusinessID,
        "page_name":        p.PageName,
        "slug":             p.Slug,
        "page_title":       p.PageTitle,
        "meta_description": p.MetaDescription,
        "sort_order":       p.SortOrder or 0,
        "is_published":     bool(p.IsPublished),
        "is_home_page":     bool(p.IsHomePage),
        "parent_page_id":   p.ParentPageID,
        "is_nav_heading":   bool(p.IsNavHeading) if p.IsNavHeading is not None else False,
        "link_url":         p.LinkURL,
        "created_at":       str(p.CreatedAt) if p.CreatedAt else None,
    }

def _ser_block(b: models.BusinessWebBlock) -> dict:
    try:
        data = json.loads(b.BlockData) if b.BlockData else {}
    except Exception:
        data = {}
    return {
        "block_id":   b.BlockID,
        "page_id":    b.PageID,
        "block_type": b.BlockType,
        "block_data": data,
        "sort_order": b.SortOrder or 0,
        "created_at": str(b.CreatedAt) if b.CreatedAt else None,
    }


# ── Site endpoints ───────────────────────────────────────────────

@router.get("/site")
def get_site(business_id: int, db: Session = Depends(get_db)):
    site = db.query(models.BusinessWebsite).filter(
        models.BusinessWebsite.BusinessID == business_id
    ).first()
    if not site:
        return None
    return _ser_site(site)

@router.get("/site/slug/{slug}")
def get_site_by_slug(slug: str, db: Session = Depends(get_db)):
    site = db.query(models.BusinessWebsite).filter(
        models.BusinessWebsite.Slug == slug
    ).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return _ser_site(site)

@router.post("/site")
def create_site(body: SiteCreate, db: Session = Depends(get_db)):
    # Check slug uniqueness
    existing = db.query(models.BusinessWebsite).filter(
        models.BusinessWebsite.Slug == body.slug
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Slug already taken")
    site = models.BusinessWebsite(
        BusinessID=body.business_id, SiteName=body.site_name, Slug=body.slug,
        Tagline=body.tagline, LogoURL=body.logo_url,
        PrimaryColor=body.primary_color, SecondaryColor=body.secondary_color,
        AccentColor=body.accent_color, BgColor=body.bg_color,
        ScreenBackgroundColor=body.screen_background_color or body.bg_color,
        PageBackgroundColor=body.page_background_color,
        TextColor=body.text_color,
        FontFamily=body.font_family, Phone=body.phone, Email=body.email, Address=body.address,
        FacebookURL=body.facebook_url, InstagramURL=body.instagram_url, TwitterURL=body.twitter_url,
        NavTextColor=body.nav_text_color or '#FFFFFF',
        FooterBgColor=body.footer_bg_color,
        CopyrightText=body.copyright_text,
        IsPublished=body.is_published,
        MetaTitle=body.meta_title,
        CanonicalURL=body.canonical_url,
        OgImageURL=body.og_image_url,
        SeoExtrasJSON=body.seo_extras_json,
        MenuStyleJSON=body.menu_style_json,
        CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow()
    )
    db.add(site); db.commit(); db.refresh(site)
    _upsert_custom_domain(db, site.WebsiteID, body.canonical_url)
    return _ser_site(site)

@router.put("/site/{website_id}")
def update_site(website_id: int, body: SiteUpdate, db: Session = Depends(get_db)):
    site = db.query(models.BusinessWebsite).filter(
        models.BusinessWebsite.WebsiteID == website_id
    ).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    if body.site_name is not None: site.SiteName = body.site_name
    if body.slug is not None:
        conflict = db.query(models.BusinessWebsite).filter(
            models.BusinessWebsite.Slug == body.slug,
            models.BusinessWebsite.WebsiteID != website_id
        ).first()
        if conflict:
            raise HTTPException(status_code=400, detail="Slug already taken")
        site.Slug = body.slug
    if body.tagline is not None: site.Tagline = body.tagline
    if body.logo_url is not None: site.LogoURL = body.logo_url
    if body.favicon_url is not None: site.FaviconURL = body.favicon_url
    if body.primary_color is not None: site.PrimaryColor = body.primary_color
    if body.secondary_color is not None: site.SecondaryColor = body.secondary_color
    if body.accent_color is not None: site.AccentColor = body.accent_color
    if body.bg_color is not None: site.BgColor = body.bg_color
    if body.screen_background_color is not None: site.ScreenBackgroundColor = body.screen_background_color
    if body.page_background_color is not None: site.PageBackgroundColor = body.page_background_color
    if body.text_color is not None: site.TextColor = body.text_color
    if body.font_family is not None: site.FontFamily = body.font_family
    if body.phone is not None: site.Phone = body.phone
    if body.email is not None: site.Email = body.email
    if body.address is not None: site.Address = body.address
    if body.facebook_url is not None: site.FacebookURL = body.facebook_url
    if body.instagram_url is not None: site.InstagramURL = body.instagram_url
    if body.twitter_url is not None: site.TwitterURL = body.twitter_url
    if body.is_published is not None: site.IsPublished = body.is_published
    if body.nav_text_color is not None: site.NavTextColor = body.nav_text_color
    if body.footer_bg_color is not None: site.FooterBgColor = body.footer_bg_color
    if body.copyright_text is not None: site.CopyrightText = body.copyright_text
    if body.meta_title is not None: site.MetaTitle = body.meta_title
    if body.canonical_url is not None: site.CanonicalURL = body.canonical_url
    if body.og_image_url is not None: site.OgImageURL = body.og_image_url
    if body.seo_extras_json is not None: site.SeoExtrasJSON = body.seo_extras_json
    if body.menu_style_json is not None: site.MenuStyleJSON = body.menu_style_json
    if body.footer_json is not None: site.FooterJSON = body.footer_json
    # Width controls
    if body.header_bg_width is not None: site.HeaderBgWidth = body.header_bg_width
    if body.header_content_width is not None: site.HeaderContentWidth = body.header_content_width
    if body.body_content_width is not None: site.BodyContentWidth = body.body_content_width
    if body.body_bg_width is not None: site.BodyBgWidth = body.body_bg_width
    if body.footer_content_width is not None: site.FooterContentWidth = body.footer_content_width
    if body.footer_bg_width is not None: site.FooterBgWidth = body.footer_bg_width
    # Typography / type scale
    if body.h1_size is not None: site.H1Size = body.h1_size
    if body.h1_weight is not None: site.H1Weight = body.h1_weight
    if body.h1_color is not None: site.H1Color = body.h1_color
    if body.h1_align is not None: site.H1Align = body.h1_align
    if body.h1_underline is not None: site.H1Underline = body.h1_underline
    if body.h1_italic is not None: site.H1Italic = body.h1_italic
    if body.h1_rule is not None: site.H1Rule = body.h1_rule
    if body.h1_rule_color is not None: site.H1RuleColor = body.h1_rule_color
    if body.h2_size is not None: site.H2Size = body.h2_size
    if body.h2_weight is not None: site.H2Weight = body.h2_weight
    if body.h2_color is not None: site.H2Color = body.h2_color
    if body.h2_align is not None: site.H2Align = body.h2_align
    if body.h2_underline is not None: site.H2Underline = body.h2_underline
    if body.h2_italic is not None: site.H2Italic = body.h2_italic
    if body.h2_rule is not None: site.H2Rule = body.h2_rule
    if body.h2_rule_color is not None: site.H2RuleColor = body.h2_rule_color
    if body.h3_size is not None: site.H3Size = body.h3_size
    if body.h3_weight is not None: site.H3Weight = body.h3_weight
    if body.h3_color is not None: site.H3Color = body.h3_color
    if body.h3_align is not None: site.H3Align = body.h3_align
    if body.h3_underline is not None: site.H3Underline = body.h3_underline
    if body.h3_italic is not None: site.H3Italic = body.h3_italic
    if body.h3_rule is not None: site.H3Rule = body.h3_rule
    if body.h3_rule_color is not None: site.H3RuleColor = body.h3_rule_color
    if body.h4_size is not None: site.H4Size = body.h4_size
    if body.h4_weight is not None: site.H4Weight = body.h4_weight
    if body.h4_color is not None: site.H4Color = body.h4_color
    if body.h4_align is not None: site.H4Align = body.h4_align
    if body.h4_underline is not None: site.H4Underline = body.h4_underline
    if body.h4_italic is not None: site.H4Italic = body.h4_italic
    if body.h4_rule is not None: site.H4Rule = body.h4_rule
    if body.h4_rule_color is not None: site.H4RuleColor = body.h4_rule_color
    if body.h1_margin_top is not None: site.H1MarginTop = body.h1_margin_top
    if body.h1_margin_bottom is not None: site.H1MarginBottom = body.h1_margin_bottom
    if body.h1_font is not None: site.H1Font = body.h1_font
    if body.h2_margin_top is not None: site.H2MarginTop = body.h2_margin_top
    if body.h2_margin_bottom is not None: site.H2MarginBottom = body.h2_margin_bottom
    if body.h2_font is not None: site.H2Font = body.h2_font
    if body.h3_margin_top is not None: site.H3MarginTop = body.h3_margin_top
    if body.h3_margin_bottom is not None: site.H3MarginBottom = body.h3_margin_bottom
    if body.h3_font is not None: site.H3Font = body.h3_font
    if body.h4_margin_top is not None: site.H4MarginTop = body.h4_margin_top
    if body.h4_margin_bottom is not None: site.H4MarginBottom = body.h4_margin_bottom
    if body.h4_font is not None: site.H4Font = body.h4_font
    if body.body_size is not None: site.BodySize = body.body_size
    if body.body_line_height is not None: site.BodyLineHeight = body.body_line_height
    if body.body_color is not None: site.BodyColor = body.body_color
    if body.body_align is not None: site.BodyAlign = body.body_align
    if body.body_underline is not None: site.BodyUnderline = body.body_underline
    if body.body_italic is not None: site.BodyItalic = body.body_italic
    # Site-wide image styling
    if body.image_border_radius is not None: site.ImageBorderRadius = body.image_border_radius
    if body.image_shadow_enabled is not None: site.ImageShadowEnabled = body.image_shadow_enabled
    if body.image_shadow_color is not None: site.ImageShadowColor = body.image_shadow_color
    if body.image_shadow_distance is not None: site.ImageShadowDistance = body.image_shadow_distance
    if body.image_shadow_blur is not None: site.ImageShadowBlur = body.image_shadow_blur
    if body.image_shadow_angle is not None: site.ImageShadowAngle = body.image_shadow_angle
    if body.body_margin_top is not None: site.BodyMarginTop = body.body_margin_top
    if body.body_margin_bottom is not None: site.BodyMarginBottom = body.body_margin_bottom
    if body.body_font is not None: site.BodyFont = body.body_font
    if body.link_color is not None: site.LinkColor = body.link_color
    if body.link_underline is not None: site.LinkUnderline = body.link_underline
    if body.dropdown_bg_color is not None: site.DropdownBgColor = body.dropdown_bg_color
    if body.dropdown_hover_color is not None: site.DropdownHoverColor = body.dropdown_hover_color
    if body.dropdown_bg_color2 is not None: site.DropdownBgColor2 = body.dropdown_bg_color2
    if body.dropdown_gradient_dir is not None: site.DropdownGradientDir = body.dropdown_gradient_dir
    # Top bar
    if body.top_bar_enabled is not None: site.TopBarEnabled = body.top_bar_enabled
    if body.top_bar_html is not None: site.TopBarHTML = body.top_bar_html
    if body.top_bar_bg_color is not None: site.TopBarBgColor = body.top_bar_bg_color
    if body.top_bar_text_color is not None: site.TopBarTextColor = body.top_bar_text_color
    if body.top_bar_align is not None: site.TopBarAlign = body.top_bar_align
    # Header banner
    if body.header_banner_url is not None: site.HeaderBannerURL = body.header_banner_url
    if body.header_banner_bg_color is not None: site.HeaderBannerBgColor = body.header_banner_bg_color
    if body.header_height is not None: site.HeaderHeight = body.header_height
    if body.show_site_name is not None: site.ShowSiteName = body.show_site_name
    if body.header_layout is not None: site.HeaderLayout = body.header_layout
    # Nav bar
    if body.nav_bg_image_url is not None: site.NavBgImageURL = body.nav_bg_image_url
    # Footer
    if body.footer_bg_image_url is not None: site.FooterBgImageURL = body.footer_bg_image_url
    if body.header_content_bg_color is not None: site.HeaderContentBgColor = body.header_content_bg_color
    if body.header_content_bg_image_url is not None: site.HeaderContentBgImageURL = body.header_content_bg_image_url
    if body.body_bg_color is not None: site.BodyBgColor = body.body_bg_color
    if body.body_bg_image_url is not None: site.BodyBgImageURL = body.body_bg_image_url
    if body.body_content_bg_color is not None: site.BodyContentBgColor = body.body_content_bg_color
    if body.body_content_bg_image_url is not None: site.BodyContentBgImageURL = body.body_content_bg_image_url
    if body.footer_content_bg_color is not None: site.FooterContentBgColor = body.footer_content_bg_color
    if body.footer_content_bg_image_url is not None: site.FooterContentBgImageURL = body.footer_content_bg_image_url
    if body.footer_html is not None: site.FooterHTML = body.footer_html
    if body.footer_height is not None: site.FooterHeight = body.footer_height
    if body.footer_bottom_radius is not None: site.FooterBottomRadius = body.footer_bottom_radius
    if body.copyright_bar_bg_color is not None: site.CopyrightBarBgColor = body.copyright_bar_bg_color
    if body.bg_image_url is not None: site.BgImageURL = body.bg_image_url
    if body.bg_gradient is not None: site.BgGradient = body.bg_gradient
    site.UpdatedAt = datetime.utcnow()
    db.commit(); db.refresh(site)
    if body.canonical_url is not None:
        _upsert_custom_domain(db, site.WebsiteID, body.canonical_url)
    return _ser_site(site)


# ── Page endpoints ───────────────────────────────────────────────

@router.get("/pages")
def list_pages(website_id: int, db: Session = Depends(get_db)):
    pages = db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.WebsiteID == website_id
    ).order_by(models.BusinessWebPage.SortOrder).all()
    return [_ser_page(p) for p in pages]

@router.post("/pages")
def create_page(body: PageCreate, db: Session = Depends(get_db)):
    page = models.BusinessWebPage(
        WebsiteID=body.website_id, BusinessID=body.business_id,
        PageName=body.page_name, Slug=body.slug, PageTitle=body.page_title,
        MetaDescription=body.meta_description, SortOrder=body.sort_order,
        IsPublished=body.is_published, IsHomePage=body.is_home_page,
        ParentPageID=body.parent_page_id, IsNavHeading=body.is_nav_heading or False,
        LinkURL=body.link_url,
        CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow()
    )
    db.add(page); db.commit(); db.refresh(page)
    return _ser_page(page)

@router.put("/pages/{page_id}")
def update_page(page_id: int, body: PageUpdate, db: Session = Depends(get_db)):
    page = db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.PageID == page_id
    ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    if body.page_name is not None: page.PageName = body.page_name
    if body.slug is not None: page.Slug = body.slug
    if body.page_title is not None: page.PageTitle = body.page_title
    if body.meta_description is not None: page.MetaDescription = body.meta_description
    if body.sort_order is not None: page.SortOrder = body.sort_order
    if body.is_published is not None: page.IsPublished = body.is_published
    if body.is_home_page is not None:
        if body.is_home_page:
            # Clear home page flag from all sibling pages first
            db.query(models.BusinessWebPage).filter(
                models.BusinessWebPage.WebsiteID == page.WebsiteID,
                models.BusinessWebPage.PageID != page_id,
            ).update({models.BusinessWebPage.IsHomePage: False})
        page.IsHomePage = body.is_home_page
    # parent_page_id: use exclude_unset to distinguish "not sent" from "explicitly set to null"
    try:
        _unset = body.model_dump(exclude_unset=True)
    except AttributeError:
        _unset = body.dict(exclude_unset=True)
    if 'parent_page_id' in _unset:
        page.ParentPageID = body.parent_page_id
    if body.is_nav_heading is not None:
        page.IsNavHeading = body.is_nav_heading
    if 'link_url' in _unset:
        page.LinkURL = body.link_url or None
    page.UpdatedAt = datetime.utcnow()
    db.commit(); db.refresh(page)
    return _ser_page(page)

@router.delete("/pages/{page_id}")
def delete_page(page_id: int, db: Session = Depends(get_db)):
    page = db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.PageID == page_id
    ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    # Promote any child pages to top-level before deleting the parent
    db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.ParentPageID == page_id
    ).update({models.BusinessWebPage.ParentPageID: None})
    db.query(models.BusinessWebBlock).filter(
        models.BusinessWebBlock.PageID == page_id
    ).delete()
    db.delete(page); db.commit()
    return {"success": True}


# ── Page templates ───────────────────────────────────────────────

class PageFromTemplate(BaseModel):
    website_id: int
    business_id: int
    template_key: str
    page_name: Optional[str] = None
    slug: Optional[str] = None


def _unique_slug(db: Session, website_id: int, base: str) -> str:
    base = (base or "page").strip().lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "page"
    slug = base
    i = 2
    while db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.WebsiteID == website_id,
        models.BusinessWebPage.Slug == slug,
    ).first():
        slug = f"{base}-{i}"
        i += 1
    return slug


@router.get("/templates")
def list_page_templates(business_id: int, db: Session = Depends(get_db)):
    """Return page templates applicable to this business's BusinessTypeID."""
    import page_templates
    biz = db.query(models.Business).filter(models.Business.BusinessID == business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")
    return {
        "business_type_id": biz.BusinessTypeID,
        "templates": page_templates.list_templates(biz.BusinessTypeID),
    }


@router.post("/pages/from-template")
def create_page_from_template(body: PageFromTemplate, db: Session = Depends(get_db)):
    """Create a page + seed blocks from a named template."""
    import page_templates
    tpl = page_templates.get_template(body.template_key)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    # Gate: template must apply to this business's BusinessTypeID (unless universal)
    gate = tpl.get("business_type_ids")
    if gate is not None:
        biz = db.query(models.Business).filter(models.Business.BusinessID == body.business_id).first()
        if not biz or biz.BusinessTypeID not in gate:
            raise HTTPException(status_code=403, detail="Template not available for this business type")

    name = (body.page_name or tpl.get("name") or "New Page").strip()
    slug = _unique_slug(db, body.website_id, body.slug or tpl.get("slug") or name)

    # Sort order: append at end
    last = db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.WebsiteID == body.website_id
    ).order_by(models.BusinessWebPage.SortOrder.desc()).first()
    next_order = (last.SortOrder + 1) if last and last.SortOrder is not None else 0

    page = models.BusinessWebPage(
        WebsiteID=body.website_id, BusinessID=body.business_id,
        PageName=name, Slug=slug,
        PageTitle=tpl.get("page_title"),
        MetaDescription=tpl.get("meta_description"),
        SortOrder=next_order, IsPublished=True, IsHomePage=False,
        IsNavHeading=False,
        CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow()
    )
    db.add(page); db.commit(); db.refresh(page)

    blocks_out = []
    for i, b in enumerate(tpl.get("default_blocks", [])):
        block = models.BusinessWebBlock(
            PageID=page.PageID,
            BlockType=b.get("block_type", "content"),
            BlockData=json.dumps(b.get("block_data", {})),
            SortOrder=i,
            CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow(),
        )
        db.add(block); blocks_out.append(block)
    db.commit()
    for bl in blocks_out:
        db.refresh(bl)

    return {
        "page": _ser_page(page),
        "blocks": [_ser_block(b) for b in blocks_out],
        "template_key": tpl["key"],
    }


class PagesFromTemplatesBulk(BaseModel):
    website_id: int
    business_id: int
    template_keys: List[str]


@router.post("/pages/from-templates-bulk")
def create_pages_from_templates_bulk(body: PagesFromTemplatesBulk, db: Session = Depends(get_db)):
    """Apply a batch of templates to a site in one call. Best-effort: skips invalid
    keys and templates the business type isn't entitled to, returns what was created
    plus what was skipped and why."""
    import page_templates

    biz = db.query(models.Business).filter(models.Business.BusinessID == body.business_id).first()
    if not biz:
        raise HTTPException(status_code=404, detail="Business not found")

    last = db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.WebsiteID == body.website_id
    ).order_by(models.BusinessWebPage.SortOrder.desc()).first()
    next_order = (last.SortOrder + 1) if last and last.SortOrder is not None else 0

    created, skipped = [], []
    for key in body.template_keys:
        tpl = page_templates.get_template(key)
        if not tpl:
            skipped.append({"template_key": key, "reason": "not_found"}); continue
        gate = tpl.get("business_type_ids")
        if gate is not None and biz.BusinessTypeID not in gate:
            skipped.append({"template_key": key, "reason": "gated"}); continue

        name = (tpl.get("name") or "New Page").strip()
        slug = _unique_slug(db, body.website_id, tpl.get("slug") or name)
        page = models.BusinessWebPage(
            WebsiteID=body.website_id, BusinessID=body.business_id,
            PageName=name, Slug=slug,
            PageTitle=tpl.get("page_title"),
            MetaDescription=tpl.get("meta_description"),
            SortOrder=next_order, IsPublished=True, IsHomePage=False,
            IsNavHeading=False,
            CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow(),
        )
        db.add(page); db.commit(); db.refresh(page)
        next_order += 1

        for i, b in enumerate(tpl.get("default_blocks", [])):
            db.add(models.BusinessWebBlock(
                PageID=page.PageID,
                BlockType=b.get("block_type", "content"),
                BlockData=json.dumps(b.get("block_data", {})),
                SortOrder=i,
                CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow(),
            ))
        db.commit()
        created.append({"template_key": key, "page": _ser_page(page)})

    return {"created": created, "skipped": skipped}


# ── Block endpoints ──────────────────────────────────────────────

@router.get("/blocks/{page_id}")
def list_blocks(page_id: int, db: Session = Depends(get_db)):
    blocks = db.query(models.BusinessWebBlock).filter(
        models.BusinessWebBlock.PageID == page_id
    ).order_by(models.BusinessWebBlock.SortOrder).all()
    return [_ser_block(b) for b in blocks]

@router.post("/blocks")
def create_block(body: BlockCreate, db: Session = Depends(get_db)):
    block = models.BusinessWebBlock(
        PageID=body.page_id, BlockType=body.block_type,
        BlockData=json.dumps(body.block_data), SortOrder=body.sort_order,
        CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow()
    )
    db.add(block); db.commit(); db.refresh(block)
    return _ser_block(block)

@router.put("/blocks/{block_id}")
def update_block(block_id: int, body: BlockUpdate, db: Session = Depends(get_db)):
    block = db.query(models.BusinessWebBlock).filter(
        models.BusinessWebBlock.BlockID == block_id
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    if body.block_type is not None: block.BlockType = body.block_type
    if body.block_data is not None: block.BlockData = json.dumps(body.block_data)
    if body.sort_order is not None: block.SortOrder = body.sort_order
    block.UpdatedAt = datetime.utcnow()
    db.commit(); db.refresh(block)
    return _ser_block(block)

@router.delete("/blocks/{block_id}")
def delete_block(block_id: int, db: Session = Depends(get_db)):
    block = db.query(models.BusinessWebBlock).filter(
        models.BusinessWebBlock.BlockID == block_id
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    db.delete(block); db.commit()
    return {"success": True}

@router.post("/blocks/reorder")
def reorder_blocks(body: BlockReorder, db: Session = Depends(get_db)):
    for idx, bid in enumerate(body.block_ids):
        db.query(models.BusinessWebBlock).filter(
            models.BusinessWebBlock.BlockID == bid
        ).update({"SortOrder": idx, "UpdatedAt": datetime.utcnow()})
    db.commit()
    return {"success": True}


# ── Live content endpoints (for dynamic blocks) ──────────────────

@router.get("/content/members")
def get_association_members(business_id: int, q: Optional[str] = None, state: Optional[str] = None,
                             max_items: int = 24, db: Session = Depends(get_db)):
    """Return member businesses for an association.
    A business is treated as a 'member' of the association if any of its animals carry an
    AnimalRegistration whose RegType matches the association's acronym. Associations are
    matched to the requesting Business by name (best-effort)."""
    try:
        # Resolve the association's acronym. Match by AssociationName ≈ BusinessName (case-insensitive).
        acro_row = db.execute(text("""
            SELECT TOP 1 AssociationAcronym FROM Associations a
            JOIN Business b ON LOWER(LTRIM(RTRIM(a.AssociationName))) = LOWER(LTRIM(RTRIM(b.BusinessName)))
            WHERE b.BusinessID = :bid AND a.AssociationAcronym IS NOT NULL AND a.AssociationAcronym <> ''
        """), {"bid": business_id}).fetchone()
        if not acro_row:
            return []  # no acronym on record → can't resolve members
        acronym = acro_row[0]

        filters = ["reg.RegType = :acro", "b.BusinessID <> :selfid"]
        params  = {"acro": acronym, "selfid": business_id, "top": max_items}
        if q:
            filters.append("b.BusinessName LIKE :q")
            params["q"] = f"%{q}%"
        if state:
            filters.append("addr.AddressState = :state")
            params["state"] = state
        where = " AND ".join(filters)

        rows = db.execute(text(f"""
            SELECT TOP (:top)
                b.BusinessID, b.BusinessName, b.Logo, b.BusinessWebsite,
                addr.AddressCity, addr.AddressState
            FROM Business b
            JOIN Animals a              ON a.BusinessID = b.BusinessID
            JOIN AnimalRegistration reg ON reg.AnimalID = a.AnimalID
            LEFT JOIN Address addr      ON addr.AddressID = b.AddressID
            WHERE {where}
            GROUP BY b.BusinessID, b.BusinessName, b.Logo, b.BusinessWebsite,
                     addr.AddressCity, addr.AddressState
            ORDER BY b.BusinessName
        """), params).fetchall()
        return [{
            "business_id": r.BusinessID,
            "name":        r.BusinessName,
            "logo_url":    r.Logo,
            "website_url": r.BusinessWebsite,
            "city":        r.AddressCity,
            "state":       r.AddressState,
            "slug":        str(r.BusinessID),
        } for r in rows]
    except Exception as e:
        import traceback
        print(f"[content/members] ERROR for business_id={business_id}: {e}")
        traceback.print_exc()
        return []


@router.get("/content/registry")
def get_association_registry(business_id: int, name: Optional[str] = None,
                              reg_number: Optional[str] = None, owner: Optional[str] = None,
                              max_results: int = 20, db: Session = Depends(get_db)):
    """Search animal registrations scoped to this association's acronym."""
    if not any([name, reg_number, owner]):
        return []
    try:
        acro_row = db.execute(text("""
            SELECT TOP 1 AssociationAcronym FROM Associations a
            JOIN Business b ON LOWER(LTRIM(RTRIM(a.AssociationName))) = LOWER(LTRIM(RTRIM(b.BusinessName)))
            WHERE b.BusinessID = :bid AND a.AssociationAcronym IS NOT NULL AND a.AssociationAcronym <> ''
        """), {"bid": business_id}).fetchone()
        if not acro_row:
            return []
        acronym = acro_row[0]

        filters = ["reg.RegType = :acro"]
        params  = {"acro": acronym, "top": max_results}
        if name:
            filters.append("a.FullName LIKE :name")
            params["name"] = f"%{name}%"
        if reg_number:
            filters.append("reg.RegNumber LIKE :reg")
            params["reg"] = f"%{reg_number}%"
        if owner:
            filters.append("b.BusinessName LIKE :owner")
            params["owner"] = f"%{owner}%"
        where = " AND ".join(filters)

        rows = db.execute(text(f"""
            SELECT TOP (:top)
                a.AnimalID, a.FullName, reg.RegNumber, b.BusinessName
            FROM Animals a
            JOIN AnimalRegistration reg ON reg.AnimalID = a.AnimalID
            LEFT JOIN Business b        ON b.BusinessID = a.BusinessID
            WHERE {where}
            ORDER BY a.FullName
        """), params).fetchall()
        return [{
            "animal_id":  r.AnimalID,
            "name":       r.FullName,
            "reg_number": r.RegNumber,
            "owner":      r.BusinessName,
            "detail_url": f"/livestock/{r.AnimalID}",
        } for r in rows]
    except Exception as e:
        import traceback
        print(f"[content/registry] ERROR for business_id={business_id}: {e}")
        traceback.print_exc()
        return []


@router.get("/content/livestock")
def get_livestock(business_id: int, include_unpublished: int = 0, db: Session = Depends(get_db)):
    try:
        publish_clause = "" if include_unpublished else " AND (a.PublishForSale = 1 OR a.PublishStud = 1)"
        rows = db.execute(text(f"""
            SELECT TOP 200
                a.AnimalID, a.FullName, a.Description,
                a.SpeciesID, a.PublishForSale, a.PublishStud,
                b.Breed AS Breed,
                sc.SpeciesCategory       AS CategoryName,
                sc.SpeciesCategoryPlural AS CategoryPlural,
                sc.SpeciesCategoryOrder  AS CategoryOrder,
                sp.Species               AS SpeciesName,
                ph.Photo1,
                pr.Price,
                pr.SalePrice,
                pr.StudFee,
                pr.PriceComments
            FROM Animals a
            LEFT JOIN SpeciesBreedLookupTable b ON b.BreedLookupID = a.BreedID
            OUTER APPLY (
                SELECT TOP 1 SpeciesCategory, SpeciesCategoryPlural, SpeciesCategoryOrder
                FROM speciescategory
                WHERE SpeciesCategory = a.Category
                ORDER BY CASE WHEN SpeciesID = a.SpeciesID THEN 0 ELSE 1 END, SpeciesCategoryID
            ) sc
            LEFT JOIN SpeciesAvailable sp       ON sp.SpeciesID = a.SpeciesID
            LEFT JOIN Photos ph  ON ph.AnimalID = a.AnimalID
            LEFT JOIN Pricing pr ON pr.AnimalID = a.AnimalID
            WHERE a.BusinessID = :bid{publish_clause}
            ORDER BY sc.SpeciesCategoryOrder, sc.SpeciesCategory, a.FullName
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        import traceback
        print(f"[content/livestock] ERROR for business_id={business_id}: {e}")
        traceback.print_exc()
        return []

@router.get("/content/packages")
def get_packages(business_id: int, db: Session = Depends(get_db)):
    """Return all packages for a business with nested animal items including photos."""
    try:
        pkgs = db.execute(text("""
            SELECT p.PackageID, p.Title, p.Description, p.PackagePrice, p.CreatedAt
            FROM AnimalPackage p
            WHERE p.BusinessID = :bid
            ORDER BY p.CreatedAt DESC
        """), {"bid": business_id}).fetchall()
        result = []
        for pkg in pkgs:
            p = dict(pkg._mapping)
            items = db.execute(text("""
                SELECT pi.PackageItemID, pi.AnimalID, pi.IncludeType,
                       a.FullName, a.Description,
                       b.Breed,
                       ph.Photo1,
                       pr.Price, pr.SalePrice, pr.StudFee
                FROM AnimalPackageItem pi
                JOIN Animals a ON a.AnimalID = pi.AnimalID
                LEFT JOIN SpeciesBreedLookupTable b ON b.BreedLookupID = a.BreedID
                LEFT JOIN Photos ph ON ph.AnimalID = pi.AnimalID
                LEFT JOIN Pricing pr ON pr.AnimalID = pi.AnimalID
                WHERE pi.PackageID = :pid
            """), {"pid": p["PackageID"]}).fetchall()
            p["items"] = [dict(it._mapping) for it in items]
            # Calculate total value from individual animal prices
            total = 0
            for it in p["items"]:
                if it.get("IncludeType") == "stud":
                    total += float(it.get("StudFee") or 0)
                else:
                    sale = float(it.get("SalePrice") or 0)
                    total += sale if sale > 0 else float(it.get("Price") or 0)
            p["total_value"] = total
            result.append(p)
        return result
    except Exception as e:
        import traceback
        print(f"[content/packages] ERROR for business_id={business_id}: {e}")
        traceback.print_exc()
        return []

@router.get("/content/animal-packages")
def get_animal_packages(animal_id: int, db: Session = Depends(get_db)):
    """Return all packages that contain a specific animal."""
    try:
        rows = db.execute(text("""
            SELECT p.PackageID, p.Title, p.Description, p.PackagePrice
            FROM AnimalPackageItem pi
            JOIN AnimalPackage p ON p.PackageID = pi.PackageID
            WHERE pi.AnimalID = :aid
            ORDER BY p.CreatedAt DESC
        """), {"aid": animal_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        print(f"[content/animal-packages] ERROR for animal_id={animal_id}: {e}")
        return []

@router.get("/content/produce")
def get_produce(business_id: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT TOP 20 p.ProduceID, i.IngredientName, p.Quantity,
                   p.QuantityMeasurement, p.RetailPrice, p.WholesalePrice,
                   p.IsOrganic, p.IsLocal, p.HarvestDate, p.AvailableDate
            FROM Produce p
            JOIN Ingredients i ON i.IngredientID = p.IngredientID
            WHERE p.BusinessID = :bid AND p.ShowProduce = 1
            ORDER BY p.ProduceID DESC
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        return []

@router.get("/content/meat")
def get_meat(business_id: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT TOP 20 m.MeatInventoryID, i.IngredientName, m.Weight,
                   m.WeightUnit, m.Quantity, m.RetailPrice, m.WholesalePrice, m.AvailableDate
            FROM MeatInventory m
            JOIN Ingredients i ON i.IngredientID = m.IngredientID
            WHERE m.BusinessID = :bid AND m.ShowMeat = 1
            ORDER BY m.MeatInventoryID DESC
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        return []

@router.get("/content/processed-food")
def get_processed_food(business_id: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT TOP 20 ProcessedFoodID, Name, Description, Quantity,
                   RetailPrice, WholesalePrice, ImageURL, AvailableDate
            FROM ProcessedFood
            WHERE BusinessID = :bid AND ShowProcessedFood = 1
            ORDER BY ProcessedFoodID DESC
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        return []

@router.get("/content/services")
def get_services(business_id: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT TOP 20 ServicesID, ServiceTitle, ServicesDescription,
                   ServicePrice, Price2, Photo1
            FROM services
            WHERE BusinessID = :bid AND ServiceAvailable = 1
            ORDER BY ServicesID DESC
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        return []

@router.get("/content/marketplace")
def get_marketplace(business_id: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT TOP 20 ListingID, Title, Description, CategoryName,
                   UnitPrice, UnitLabel, QuantityAvailable, ImageURL,
                   IsOrganic, IsLocal, IsFeatured, ProductType
            FROM MarketplaceListings
            WHERE BusinessID = :bid AND IsActive = 1
            ORDER BY IsFeatured DESC, ListingID DESC
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        return []

@router.get("/content/blog")
def get_blog(business_id: int, limit: int = 10, category: Optional[str] = None,
             db: Session = Depends(get_db)):
    try:
        where = ["b.BusinessID = :bid", "b.IsPublished = 1"]
        params = {"bid": business_id, "limit": limit}
        if category:
            where.append("bc.BlogCategoryName = :cat")
            params["cat"] = category
        where_sql = " AND ".join(where)
        rows = db.execute(text(f"""
            SELECT TOP (:limit)
                   b.BlogID, b.Title, b.Author, b.AuthorLink,
                   b.CoverImage, b.Content, b.IsFeatured,
                   b.CreatedAt, bc.BlogCategoryName
            FROM blog b
            LEFT JOIN blogcategories bc ON bc.BlogCatID = b.BlogCatID
            WHERE {where_sql}
            ORDER BY b.IsFeatured DESC, b.CreatedAt DESC
        """), params).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception:
        return []

@router.get("/content/gallery")
def get_gallery(business_id: int, db: Session = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT TOP 50 g.GalleryID, g.GalleryImage, g.GalleryCaption,
                   gc.GalleryCategoryName
            FROM gallery g
            LEFT JOIN gallerycategories gc ON gc.GalleryCatID = g.GalleryCatID
            JOIN BusinessAccess ba ON ba.PeopleID = g.PeopleID
            WHERE ba.BusinessID = :bid
            ORDER BY g.ImageOrder, g.GalleryID
        """), {"bid": business_id}).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        return []


# ── Content availability check ───────────────────────────────────

@router.get("/content/check")
def check_content(business_id: int, db: Session = Depends(get_db)):
    """Returns which content types the business has live data for."""
    def has(query, params):
        try:
            row = db.execute(text(query), params).fetchone()
            return row is not None and row[0] > 0
        except:
            return False

    bid = {"bid": business_id}
    return {
        "livestock_for_sale": has("SELECT COUNT(1) FROM animals WHERE BusinessID=:bid AND PublishForSale=1", bid),
        "studs":              has("SELECT COUNT(1) FROM animals WHERE BusinessID=:bid AND PublishStud=1", bid),
        "produce":            has("SELECT COUNT(1) FROM Produce WHERE BusinessID=:bid AND ShowProduce=1", bid),
        "meat":               has("SELECT COUNT(1) FROM MeatInventory WHERE BusinessID=:bid AND ShowMeat=1", bid),
        "processed_food":     has("SELECT COUNT(1) FROM ProcessedFood WHERE BusinessID=:bid AND ShowProcessedFood=1", bid),
        "services":           has("SELECT COUNT(1) FROM services WHERE BusinessID=:bid AND ServiceAvailable=1", bid),
        "products":           has("SELECT COUNT(1) FROM products WHERE BusinessID=:bid AND IsActive=1", bid),
        "marketplace":        has("SELECT COUNT(1) FROM MarketplaceListings WHERE BusinessID=:bid AND IsActive=1", bid),
    }


# ── Full site bundle (for public renderer) ───────────────────────

def _build_bundle(site: models.BusinessWebsite, db) -> dict:
    """Shared helper: serialise site + published pages + blocks."""
    import traceback
    try:
        pages = db.query(models.BusinessWebPage).filter(
            models.BusinessWebPage.WebsiteID == site.WebsiteID,
            models.BusinessWebPage.IsPublished == True
        ).order_by(models.BusinessWebPage.SortOrder).all()

        result_pages = []
        for page in pages:
            blocks = db.query(models.BusinessWebBlock).filter(
                models.BusinessWebBlock.PageID == page.PageID
            ).order_by(models.BusinessWebBlock.SortOrder).all()
            p = _ser_page(page)
            p["blocks"] = [_ser_block(b) for b in blocks]
            result_pages.append(p)

        site_data = _ser_site(site)
        site_data["pages"] = result_pages

        # Include header images so the public renderer can pick the date-matched one.
        header_rows = db.execute(
            text("SELECT * FROM WebsiteHeaderImages WHERE WebsiteID=:wid ORDER BY StartDate, SortOrder"),
            {"wid": site.WebsiteID}
        ).fetchall()
        site_data["header_images"] = [_ser_header_image(r) for r in header_rows]

        return site_data
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"bundle build failed: {exc}")


@router.get("/bundle/{slug}")
def get_site_bundle(slug: str, db: Session = Depends(get_db)):
    """Returns site + all pages + all blocks in a single request for the public renderer."""
    site = db.query(models.BusinessWebsite).filter(
        models.BusinessWebsite.Slug == slug
    ).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return _build_bundle(site, db)


@router.get("/bundle-by-domain")
def get_site_bundle_by_domain(domain: str, db: Session = Depends(get_db)):
    """Fast domain → site bundle lookup.

    Resolution order:
      1. WebsiteCustomDomain table (indexed — O(log n), handles all variants)
      2. Fallback LIKE scan on CanonicalURL (old sites not yet in the index)
         — auto-registers the match so subsequent calls use the fast path.

    Retries once on transient DB connection errors (Cloud Run cold-start races).
    """
    import traceback

    bare = _normalize_domain(domain)
    if not bare:
        raise HTTPException(status_code=400, detail="domain parameter is required")

    # Build all candidates to search: bare and www. variant
    candidates = [bare, f"www.{bare}"]

    for attempt in range(2):
        try:
            # ── 1. Fast indexed lookup ────────────────────────────────────────
            row = db.execute(
                text("SELECT TOP 1 WebsiteID FROM WebsiteCustomDomain WHERE Domain IN :doms AND IsActive = 1"),
                {"doms": tuple(candidates)},
            ).first()

            if row:
                site = db.query(models.BusinessWebsite).filter(
                    models.BusinessWebsite.WebsiteID == row.WebsiteID
                ).first()
            else:
                # ── 2. Fallback: LIKE scan (back-compat for old entries) ──────
                site = None
                for candidate in candidates:
                    site = db.query(models.BusinessWebsite).filter(
                        models.BusinessWebsite.CanonicalURL.ilike(f"%{candidate}%")
                    ).first()
                    if site:
                        # Auto-register so next request hits the fast path
                        _upsert_custom_domain(db, site.WebsiteID, site.CanonicalURL)
                        break

            if not site:
                raise HTTPException(status_code=404, detail="No site found for this domain")

            return _build_bundle(site, db)

        except HTTPException:
            raise
        except OperationalError as exc:
            # Transient connection error (stale pool, cold-start race) — retry once
            if attempt == 0:
                try:
                    db.rollback()
                except Exception:
                    pass
                continue
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"bundle-by-domain failed: {exc}")
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"bundle-by-domain failed: {exc}")


# ── Contact form submission ───────────────────────────────────────

class ContactFormPayload(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str = ""
    organization: str = ""
    message: str
    to_email: str
    site_name: str = ""

@router.post("/contact-form")
def submit_contact_form(payload: ContactFormPayload):
    import os
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        api_key = os.getenv("SENDGRID_API_KEY", "")
        from_email = os.getenv("FROM_EMAIL", "john@oatmeal-ai.com")
        from_name  = os.getenv("FROM_NAME",  "Oatmeal Farm Network")
        if not api_key:
            raise HTTPException(status_code=500, detail="Email service not configured")
        subject = f"New contact form message from {payload.first_name} {payload.last_name}"
        if payload.site_name:
            subject += f" via {payload.site_name}"
        rows = [
            ("First Name", payload.first_name),
            ("Last Name",  payload.last_name),
            ("Email",      payload.email),
            ("Phone",      payload.phone or "—"),
            ("Organization", payload.organization or "—"),
            ("Message",    payload.message.replace("\n", "<br>")),
        ]
        html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
          <h2 style="color:#374151">Contact Form Submission</h2>
          <table style="width:100%;border-collapse:collapse">
            {"".join(f'<tr><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-weight:600;width:140px;color:#6b7280;vertical-align:top">{k}</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{v}</td></tr>' for k,v in rows)}
          </table>
          <p style="color:#9ca3af;font-size:12px;margin-top:24px">Sent via {payload.site_name or "your website"} contact form.</p>
        </div>"""
        msg = Mail(
            from_email=Email(from_email, from_name),
            to_emails=To(payload.to_email),
            subject=subject,
            html_content=Content("text/html", html),
        )
        msg.reply_to = payload.email
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        sg.send(msg)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[contact-form] send failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to send message")
    return {"ok": True}


# ── Image upload ─────────────────────────────────────────────────

GCS_BUCKET  = "oatmeal-farm-network-images"
GCS_PREFIX  = "website-images"

@router.delete("/site/{website_id}")
def delete_site(website_id: int, db: Session = Depends(get_db)):
    site = db.query(models.BusinessWebsite).filter(
        models.BusinessWebsite.WebsiteID == website_id
    ).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    # Free the slug in its own commit BEFORE the cascade delete. Slug has a
    # unique index, so if a child-table FK ever blocks the row delete, the
    # slug is already released and the user can recreate without a collision.
    site.Slug = None
    db.commit()
    # Delete all child data first
    page_ids = [p.PageID for p in db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.WebsiteID == website_id
    ).all()]
    if page_ids:
        db.query(models.BusinessWebBlock).filter(
            models.BusinessWebBlock.PageID.in_(page_ids)
        ).delete(synchronize_session=False)
    db.query(models.BusinessWebPage).filter(
        models.BusinessWebPage.WebsiteID == website_id
    ).delete(synchronize_session=False)
    db.execute(text("DELETE FROM WebsiteHeaderImages WHERE WebsiteID=:wid"), {"wid": website_id})
    db.execute(text("DELETE FROM WebsiteVersionHistory WHERE WebsiteID=:wid"), {"wid": website_id})
    db.delete(site)
    db.commit()
    return {"ok": True}


@router.post("/upload-image")
async def upload_website_image(file: UploadFile = File(...)):
    """Upload an image to GCS and return its public URL."""
    try:
        from google.cloud import storage as gcs
        contents = await file.read()
        ext = (file.filename or "image.jpg").rsplit(".", 1)[-1].lower()
        filename = f"{GCS_PREFIX}/{uuid.uuid4().hex}.{ext}"
        client = gcs.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(filename)
        blob.upload_from_string(contents, content_type=file.content_type or "image/jpeg")
        url = f"https://storage.googleapis.com/{GCS_BUCKET}/{filename}"
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/upload-file")
async def upload_website_file(file: UploadFile = File(...)):
    """Upload a PDF (or any document) to GCS and return its public URL + original filename."""
    try:
        from google.cloud import storage as gcs
        contents = await file.read()
        original = file.filename or "document.pdf"
        ext = original.rsplit(".", 1)[-1].lower() if "." in original else "pdf"
        filename = f"{GCS_PREFIX}/files/{uuid.uuid4().hex}.{ext}"
        client = gcs.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(filename)
        blob.upload_from_string(contents, content_type=file.content_type or "application/pdf")
        url = f"https://storage.googleapis.com/{GCS_BUCKET}/{filename}"
        return {"url": url, "filename": original}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# ── Header images ─────────────────────────────────────────────────

class HeaderImageCreate(BaseModel):
    website_id: int
    image_url: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    sort_order: Optional[int] = 0

class HeaderImageUpdate(BaseModel):
    image_url: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    sort_order: Optional[int] = None

def _ser_header_image(row) -> dict:
    return {
        "header_image_id": row.HeaderImageID,
        "website_id":      row.WebsiteID,
        "image_url":       row.ImageURL,
        "start_date":      str(row.StartDate) if row.StartDate else None,
        "end_date":        str(row.EndDate) if row.EndDate else None,
        "sort_order":      row.SortOrder or 0,
    }

@router.get("/header-images/{website_id}")
def list_header_images(website_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT * FROM WebsiteHeaderImages WHERE WebsiteID=:wid ORDER BY StartDate, SortOrder"),
        {"wid": website_id}
    ).fetchall()
    return [_ser_header_image(r) for r in rows]

@router.post("/header-images")
def create_header_image(body: HeaderImageCreate, db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO WebsiteHeaderImages (WebsiteID, ImageURL, StartDate, EndDate, SortOrder)
        VALUES (:wid, :url, :sd, :ed, :so)
    """), {"wid": body.website_id, "url": body.image_url,
           "sd": body.start_date, "ed": body.end_date, "so": body.sort_order or 0})
    db.commit()
    row = db.execute(
        text("SELECT TOP 1 * FROM WebsiteHeaderImages WHERE WebsiteID=:wid ORDER BY HeaderImageID DESC"),
        {"wid": body.website_id}
    ).fetchone()
    return _ser_header_image(row)

@router.put("/header-images/{header_image_id}")
def update_header_image(header_image_id: int, body: HeaderImageUpdate, db: Session = Depends(get_db)):
    sets, params = [], {"hid": header_image_id}
    if body.image_url is not None:  sets.append("ImageURL=:url");   params["url"] = body.image_url
    if body.start_date is not None: sets.append("StartDate=:sd");   params["sd"]  = body.start_date
    if body.end_date is not None:   sets.append("EndDate=:ed");     params["ed"]  = body.end_date
    if body.sort_order is not None: sets.append("SortOrder=:so");   params["so"]  = body.sort_order
    if sets:
        db.execute(text(f"UPDATE WebsiteHeaderImages SET {', '.join(sets)} WHERE HeaderImageID=:hid"), params)
        db.commit()
    row = db.execute(text("SELECT * FROM WebsiteHeaderImages WHERE HeaderImageID=:hid"), {"hid": header_image_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Header image not found")
    return _ser_header_image(row)

@router.delete("/header-images/{header_image_id}")
def delete_header_image(header_image_id: int, db: Session = Depends(get_db)):
    db.execute(text("DELETE FROM WebsiteHeaderImages WHERE HeaderImageID=:hid"), {"hid": header_image_id})
    db.commit()
    return {"ok": True}


# ── Version History ───────────────────────────────────────────────

class VersionCreate(BaseModel):
    website_id: int
    version_label: Optional[str] = None

def _build_snapshot(website_id: int, db: Session) -> str:
    """Capture full site state: site + pages + blocks."""
    site = db.query(models.BusinessWebsite).filter(models.BusinessWebsite.WebsiteID == website_id).first()
    if not site:
        return "{}"
    pages = db.query(models.BusinessWebPage).filter(models.BusinessWebPage.WebsiteID == website_id).all()
    result = {"site": _ser_site(site), "pages": []}
    for page in pages:
        blocks = db.query(models.BusinessWebBlock).filter(models.BusinessWebBlock.PageID == page.PageID).all()
        p = _ser_page(page)
        p["blocks"] = [_ser_block(b) for b in blocks]
        result["pages"].append(p)
    return json.dumps(result)

@router.get("/versions/{website_id}")
def list_versions(website_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT TOP 20 VersionID, WebsiteID, VersionLabel, CreatedAt FROM WebsiteVersionHistory WHERE WebsiteID=:wid ORDER BY CreatedAt DESC"),
        {"wid": website_id}
    ).fetchall()
    return [{"version_id": r.VersionID, "website_id": r.WebsiteID, "version_label": r.VersionLabel, "created_at": str(r.CreatedAt)} for r in rows]

@router.post("/versions")
def save_version(body: VersionCreate, db: Session = Depends(get_db)):
    snapshot = _build_snapshot(body.website_id, db)
    label = body.version_label or f"Saved {datetime.utcnow().strftime('%b %d %Y %H:%M')}"
    db.execute(text("""
        INSERT INTO WebsiteVersionHistory (WebsiteID, VersionLabel, SnapshotJSON)
        VALUES (:wid, :label, :snap)
    """), {"wid": body.website_id, "label": label, "snap": snapshot})
    db.commit()
    row = db.execute(
        text("SELECT TOP 1 * FROM WebsiteVersionHistory WHERE WebsiteID=:wid ORDER BY VersionID DESC"),
        {"wid": body.website_id}
    ).fetchone()
    return {"version_id": row.VersionID, "version_label": row.VersionLabel, "created_at": str(row.CreatedAt)}

@router.post("/versions/{version_id}/restore")
def restore_version(version_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT * FROM WebsiteVersionHistory WHERE VersionID=:vid"), {"vid": version_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
    snapshot = json.loads(row.SnapshotJSON)
    website_id = row.WebsiteID

    # Restore site fields
    site_data = snapshot.get("site", {})
    site = db.query(models.BusinessWebsite).filter(models.BusinessWebsite.WebsiteID == website_id).first()
    if site and site_data:
        for field, col in [
            ("site_name","SiteName"),("tagline","Tagline"),("logo_url","LogoURL"),("favicon_url","FaviconURL"),
            ("primary_color","PrimaryColor"),("secondary_color","SecondaryColor"),
            ("accent_color","AccentColor"),("bg_color","BgColor"),
            ("screen_background_color","ScreenBackgroundColor"),
            ("page_background_color","PageBackgroundColor"),
            ("text_color","TextColor"),
            ("font_family","FontFamily"),("nav_text_color","NavTextColor"),
            ("footer_bg_color","FooterBgColor"),("copyright_text","CopyrightText"),
        ]:
            if field in site_data:
                setattr(site, col, site_data[field])
        site.UpdatedAt = datetime.utcnow()

    # Restore pages and blocks
    existing_pages = db.query(models.BusinessWebPage).filter(models.BusinessWebPage.WebsiteID == website_id).all()
    existing_page_ids = [p.PageID for p in existing_pages]
    if existing_page_ids:
        db.query(models.BusinessWebBlock).filter(models.BusinessWebBlock.PageID.in_(existing_page_ids)).delete(synchronize_session=False)
    db.query(models.BusinessWebPage).filter(models.BusinessWebPage.WebsiteID == website_id).delete(synchronize_session=False)

    for pg in snapshot.get("pages", []):
        new_page = models.BusinessWebPage(
            WebsiteID=website_id, BusinessID=site_data.get("business_id", 0),
            PageName=pg["page_name"], Slug=pg["slug"],
            PageTitle=pg.get("page_title"), MetaDescription=pg.get("meta_description"),
            SortOrder=pg.get("sort_order", 0), IsPublished=pg.get("is_published", True),
            IsHomePage=pg.get("is_home_page", False),
            CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow()
        )
        db.add(new_page); db.flush()
        for blk in pg.get("blocks", []):
            db.add(models.BusinessWebBlock(
                PageID=new_page.PageID, BlockType=blk["block_type"],
                BlockData=json.dumps(blk["block_data"]), SortOrder=blk.get("sort_order", 0),
                CreatedAt=datetime.utcnow(), UpdatedAt=datetime.utcnow()
            ))

    db.commit()
    return {"ok": True, "website_id": website_id}
