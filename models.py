from sqlalchemy import Column, Integer, String, SmallInteger, DateTime, Date, Text, Boolean, Float
from sqlalchemy import Numeric as Decimal
from database import Base

# ── PEOPLE / ACCOUNTS ──────────────────────────────────────────
class People(Base):
    __tablename__ = "People"
    PeopleID          = Column(Integer, primary_key=True, index=True)
    PeopleFirstName   = Column(String(100))
    PeopleLastName    = Column(String(100))
    PeopleEmail       = Column(String(255))
    PeoplePhone       = Column(String(50))
    PeopleActive      = Column(SmallInteger)
    accesslevel       = Column(Integer)
    LKMAccessLevel    = Column(Integer)
    Subscriptionlevel = Column(Integer)
    AddressID         = Column(Integer)
    BusinessId        = Column(Integer)
    PeopleCreationDate= Column(DateTime)
    PeoplePassword    = Column(String(255))

# ── BUSINESS ────────────────────────────────────────────────────
class Business(Base):
    __tablename__ = "Business"
    BusinessID              = Column(Integer, primary_key=True, index=True)
    BusinessTypeID          = Column(Integer, index=True)
    BusinessName            = Column(String(1000))
    BusinessEmail           = Column(String(100))
    BusinessPhone           = Column(String(50))
    AddressID               = Column(Integer)
    SubscriptionLevel       = Column(Integer)
    SubscriptionEndDate     = Column(DateTime)
    SubscriptionStartDate   = Column(DateTime)
    AccessLevel             = Column(Integer)
    Logo = Column(String(255))
    BusinessFacebook        = Column(String(255))
    BusinessInstagram       = Column(String(255))
    BusinessLinkedIn        = Column(String(255))
    BusinessX               = Column(String(255))
    BusinessPinterest       = Column(String(255))
    BusinessYouTube         = Column(String(255))
    BusinessTruthSocial     = Column(String(255))
    BusinessBlog            = Column(String(255))
    BusinessOtherSocial1    = Column(String(255))
    BusinessOtherSocial2    = Column(String(255))
    WebsitesID              = Column(Integer)
    BusinessDescription     = Column(Text)

# ── ADDRESS ─────────────────────────────────────────────────────
class Address(Base):
    __tablename__ = "Address"
    AddressID      = Column(Integer, primary_key=True, index=True)
    AddressStreet  = Column(String(50))
    AddressCity    = Column(String(50))
    AddressState   = Column(String(365))
    AddressZip     = Column(String(48))
    AddressCountry = Column(String(50))
    country_id     = Column(Integer)

# ── ANIMALS ─────────────────────────────────────────────────────
class Animal(Base):
    __tablename__ = "Animals"
    AnimalID          = Column(Integer, primary_key=True, index=True)
    BusinessID        = Column(Integer)
    PeopleID          = Column(Integer)
    SpeciesID         = Column(Integer)
    FullName          = Column(String(255))
    ShortName         = Column(String(255))
    NumberOfAnimals   = Column(Integer)
    BreedID           = Column(Integer)
    BreedID2          = Column(Integer)
    BreedID3          = Column(Integer)
    BreedID4          = Column(Integer)
    Category          = Column(Integer)
    DOBday            = Column(Integer)
    DOBMonth          = Column(Integer)
    DOBYear           = Column(Integer)
    Temperment       = Column(Integer)
    Height            = Column(Decimal(10, 2))
    Weight            = Column(Decimal(10, 2))
    Gaited            = Column(SmallInteger)
    Warmblooded       = Column(SmallInteger)
    Horns             = Column(String(20))
    Temperament       = Column(Integer)
    Description       = Column(Text)
    PublishForSale    = Column(SmallInteger)
    PublishStud       = Column(SmallInteger)
    Lastupdated       = Column(DateTime)

# ── ANIMAL REGISTRATION ──────────────────────────────────────────
class AnimalRegistration(Base):
    __tablename__ = "AnimalRegistration"
    AnimalRegistrationID = Column(Integer, primary_key=True, index=True)
    AnimalID             = Column(Integer)
    RegType              = Column(String(255))
    RegNumber            = Column(String(255))

# ── COLORS ───────────────────────────────────────────────────────
class AnimalColor(Base):
    __tablename__ = "Colors"
    ColorID   = Column(Integer, primary_key=True, index=True)
    AnimalID  = Column(Integer)
    Color1    = Column(String(100))
    Color2    = Column(String(100))
    Color3    = Column(String(100))
    Color4    = Column(String(100))

# ── ANCESTORS ────────────────────────────────────────────────────
class Ancestor(Base):
    __tablename__ = "Ancestors"
    AncestorID          = Column(Integer, primary_key=True, index=True)
    AnimalID            = Column(Integer)
    SireName            = Column(String(255))
    SireColor           = Column(String(100))
    SireARI             = Column(String(100))
    SireCLAA            = Column(String(100))
    DamName             = Column(String(255))
    DamColor            = Column(String(100))
    DamARI              = Column(String(100))
    DamCLAA             = Column(String(100))
    SireSireName        = Column(String(255))
    SireSireColor       = Column(String(100))
    SireDamName         = Column(String(255))
    SireDamColor        = Column(String(100))
    DamSireName         = Column(String(255))
    DamSireColor        = Column(String(100))
    DamDamName          = Column(String(255))
    DamDamColor         = Column(String(100))
    SireSireSireName    = Column(String(255))
    SireSireSireColor   = Column(String(100))
    SireSireDamName     = Column(String(255))
    SireSireDamColor    = Column(String(100))
    SireDamSireName     = Column(String(255))
    SireDamSireColor    = Column(String(100))
    SireDamDamName      = Column(String(255))
    SireDamDamColor     = Column(String(100))
    DamSireSireName     = Column(String(255))
    DamSireSireColor    = Column(String(100))
    DamSireDamName      = Column(String(255))
    DamSireDamColor     = Column(String(100))
    DamDamSireName      = Column(String(255))
    DamDamSireColor     = Column(String(100))
    DamDamDamName       = Column(String(255))
    DamDamDamColor      = Column(String(100))
    AncestryDescription = Column(Text)

# ── ANCESTRY PERCENTS (Alpacas) ──────────────────────────────────
class AncestryPercent(Base):
    __tablename__ = "AncestryPercents"
    AncestryPercentID    = Column(Integer, primary_key=True, index=True)
    AnimalID             = Column(Integer)
    PercentPeruvian      = Column(String(50))
    PercentChilean       = Column(String(50))
    PercentBolivian      = Column(String(50))
    PercentUnknownOther  = Column(String(50))
    PercentAccoyo        = Column(String(50))

# ── FIBER (Alpacas) ──────────────────────────────────────────────
class Fiber(Base):
    __tablename__ = "Fiber"
    FiberID        = Column(Integer, primary_key=True, index=True)
    AnimalID       = Column(Integer)
    SampleDateDay  = Column(Integer)
    SampleDateMonth= Column(Integer)
    SampleDateYear = Column(Integer)
    AFD            = Column(Decimal(10, 2))
    SD             = Column(Decimal(10, 2))
    COV            = Column(Decimal(10, 2))
    CF             = Column(Decimal(10, 2))
    GreaterThan30  = Column(Decimal(10, 2))
    Curve          = Column(Decimal(10, 2))
    CrimpPerInch   = Column(Decimal(10, 2))
    Length         = Column(Decimal(10, 2))
    ShearWeight    = Column(Decimal(10, 2))
    BlanketWeight  = Column(Decimal(10, 2))

# ── AWARDS ───────────────────────────────────────────────────────
class Award(Base):
    __tablename__ = "Awards"
    AwardID       = Column(Integer, primary_key=True, index=True)
    AnimalID      = Column(Integer)
    AwardYear     = Column(Integer)
    ShowName      = Column(String(255))
    Placing       = Column(String(255))
    Type          = Column(String(255))
    Awardcomments = Column(Text)

# ── PRICING ──────────────────────────────────────────────────────
class Pricing(Base):
    __tablename__ = "Pricing"
    AnimalID          = Column(Integer, primary_key=True, index=True)
    Price             = Column(Decimal(10, 2))
    Price2            = Column(Decimal(10, 2))
    Price3            = Column(Decimal(10, 2))
    Price4            = Column(Decimal(10, 2))
    MinOrder1         = Column(Integer)
    MinOrder2         = Column(Integer)
    MinOrder3         = Column(Integer)
    MinOrder4         = Column(Integer)
    MaxOrder1         = Column(Integer)
    MaxOrder2         = Column(Integer)
    MaxOrder3         = Column(Integer)
    MaxOrder4         = Column(Integer)
    StudFee           = Column(Decimal(10, 2))
    ForSale           = Column(SmallInteger)
    Free              = Column(SmallInteger)
    OBO               = Column(SmallInteger)
    Foundation        = Column(SmallInteger)
    Discount          = Column(Integer)
    PriceComments     = Column(Text)
    Donor             = Column(SmallInteger)
    EmbryoPrice       = Column(Decimal(10, 2))
    SemenPrice        = Column(Decimal(10, 2))
    PayWhatYouCanStud = Column(SmallInteger)
    Sold              = Column(SmallInteger)
    SalePrice         = Column(Decimal(10, 2))
    CoOwnerBusiness1  = Column(String(255))
    CoOwnerName1      = Column(String(255))
    CoOwnerLink1      = Column(String(255))
    CoOwnerBusiness2  = Column(String(255))
    CoOwnerName2      = Column(String(255))
    CoOwnerLink2      = Column(String(255))
    CoOwnerBusiness3  = Column(String(255))
    CoOwnerName3      = Column(String(255))
    CoOwnerLink3      = Column(String(255))

# ── PHOTOS ───────────────────────────────────────────────────────
class Photo(Base):
    __tablename__ = "Photos"
    AnimalID      = Column(Integer, primary_key=True, index=True)
    Photo1        = Column(String(500))
    Photo2        = Column(String(500))
    Photo3        = Column(String(500))
    Photo4        = Column(String(500))
    Photo5        = Column(String(500))
    Photo6        = Column(String(500))
    Photo7        = Column(String(500))
    Photo8        = Column(String(500))
    Photo9        = Column(String(500))
    Photo10       = Column(String(500))
    Photo11       = Column(String(500))
    Photo12       = Column(String(500))
    Photo13       = Column(String(500))
    Photo14       = Column(String(500))
    Photo15       = Column(String(500))
    Photo16       = Column(String(500))
    PhotoCaption1 = Column(String(500))
    PhotoCaption2 = Column(String(500))
    PhotoCaption3 = Column(String(500))
    PhotoCaption4 = Column(String(500))
    PhotoCaption5 = Column(String(500))
    PhotoCaption6 = Column(String(500))
    PhotoCaption7 = Column(String(500))
    PhotoCaption8 = Column(String(500))
    PhotoCaption9 = Column(String(500))
    PhotoCaption10= Column(String(500))
    PhotoCaption11= Column(String(500))
    PhotoCaption12= Column(String(500))
    PhotoCaption13= Column(String(500))
    PhotoCaption14= Column(String(500))
    PhotoCaption15= Column(String(500))
    PhotoCaption16= Column(String(500))
    FiberAnalysis = Column(String(500))
    Histogram     = Column(String(500))
    ARI           = Column(String(500))
    AnimalVideo   = Column(String(1000))

# ── SPECIES LOOKUP TABLES ────────────────────────────────────────
class SpeciesAvailable(Base):
    __tablename__ = "speciesavailable"
    SpeciesID                = Column(Integer, primary_key=True, index=True)
    Species                  = Column(String(255))
    SpeciesPriority          = Column(Integer)
    SpeciesAvailableonSite   = Column(SmallInteger)

class SpeciesBreedLookup(Base):
    __tablename__ = "SpeciesBreedLookupTable"
    BreedLookupID = Column(Integer, primary_key=True, index=True)
    SpeciesID     = Column(Integer)
    Breed         = Column(String(255))

class SpeciesColorLookup(Base):
    __tablename__ = "SpeciesColorlookupTable"
    SpeciesColorID = Column(Integer, primary_key=True, index=True)
    SpeciesID      = Column(Integer)
    SpeciesColor   = Column(String(255))

class SpeciesRegistrationTypeLookup(Base):
    __tablename__ = "SpeciesRegistrationTypeLookupTable"
    SpeciesRegTypeID     = Column(Integer, primary_key=True, index=True)
    SpeciesID            = Column(Integer)
    SpeciesRegistrationType = Column(String(255))
    country_id           = Column(Integer)

class SpeciesCategory(Base):
    __tablename__ = "speciescategory"
    SpeciesCategoryID     = Column(Integer, primary_key=True, index=True)
    SpeciesID             = Column(Integer)
    SpeciesCategory       = Column(String(255))
    SpeciesCategoryPlural = Column(String(255))
    SpeciesCategoryOrder  = Column(Integer)

# ── EVENTS ──────────────────────────────────────────────────────
class Event(Base):
    __tablename__ = "Event"
    EventID          = Column(Integer, primary_key=True, index=True)
    PeopleID         = Column(Integer)
    EventName        = Column(String(255))
    EventTypeID      = Column(Integer)
    AddressID        = Column(Integer)
    EventStartMonth  = Column(Integer)
    EventStartDay    = Column(Integer)
    EventStartYear   = Column(Integer)
    EventEndMonth    = Column(Integer)
    EventEndDay      = Column(Integer)
    EventEndYear     = Column(Integer)
    EventDescription = Column(String)
    EventStatus      = Column(String(50))

# ── ASSOCIATIONS ─────────────────────────────────────────────────
class Association(Base):
    __tablename__ = "Associations"
    AssociationID           = Column(Integer, primary_key=True, index=True)
    AssociationName         = Column(String(255))
    AssociationAcronym      = Column(String(50))
    AssociationEmailaddress = Column(String(255))
    SpeciesID               = Column(Integer)
    AddressID               = Column(Integer)

# ── PRODUCE ──────────────────────────────────────────────────────
class Produce(Base):
    __tablename__ = "Produce"
    ProduceID      = Column(Integer, primary_key=True, index=True)
    BusinessID     = Column(Integer)
    IngredientID   = Column(Integer)
    Quantity       = Column(Decimal(10, 2))
    RetailPrice    = Column(Decimal(10, 2))
    WholesalePrice = Column(Decimal(10, 2))
    HarvestDate    = Column(Date)
    ExpirationDate = Column(Date)
    IsOrganic      = Column(Boolean)
    ShowProduce    = Column(SmallInteger)

# ── FIELDS ───────────────────────────────────────────────────────
class Field(Base):
    __tablename__ = "Field"
    FieldID                  = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID               = Column(Integer)
    Name                     = Column(String(255))
    Address                  = Column(String(500))
    Latitude                 = Column(Decimal(9, 6))
    Longitude                = Column(Decimal(9, 6))
    FieldSizeHectares        = Column(Decimal(10, 2))
    CropType                 = Column(String(255))
    PlantingDate             = Column(Date)
    MonitoringEnabled        = Column(Boolean)
    MonitoringIntervalDays   = Column(Integer)
    AlertThresholdHealth     = Column(Integer)
    CreatedAt                = Column(DateTime)
    CreatedByPeopleID        = Column(Integer)
    UpdatedAt                = Column(DateTime)
    DeletedAt                = Column(DateTime)
    BoundaryGeoJSON          = Column(Text)
    FieldDescription         = Column(Text)
    AddressID                = Column(Integer)
    SoilID                   = Column(Integer)

# ── FIELD NOTES ──────────────────────────────────────────────────
class FieldNote(Base):
    __tablename__ = "FieldNote"
    NoteID     = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID    = Column(Integer, index=True)
    BusinessID = Column(Integer, index=True)
    PeopleID   = Column(Integer)
    NoteDate   = Column(Date)
    Category   = Column(String(100))
    Title      = Column(String(500))
    Content    = Column(Text)
    Severity   = Column(String(20))           # Low/Medium/High/Critical (scouting-style notes)
    Latitude   = Column(Decimal(10, 7))       # optional GPS pin
    Longitude  = Column(Decimal(10, 7))
    ImageUrl   = Column(String(1000))         # optional photo URL
    CreatedAt  = Column(DateTime)
    UpdatedAt  = Column(DateTime)

# ── BIOMASS ANALYSIS ─────────────────────────────────────────────
class FieldBiomassAnalysis(Base):
    __tablename__ = "FieldBiomassAnalysis"
    AnalysisID        = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID           = Column(Integer, index=True)
    BusinessID        = Column(Integer, index=True)
    Source            = Column(String(20))          # 'satellite' | 'upload'
    BiomassKgHa       = Column(Decimal(10, 2))
    Confidence        = Column(Decimal(5, 3))
    ImageUrl          = Column(String(1000))        # GCS or source imagery URL
    CapturedAt        = Column(DateTime)            # imagery capture date (satellite) or upload time
    ModelVersion      = Column(String(50))
    FeaturesJSON      = Column(Text)                # raw feature payload from estimator
    CreatedByPeopleID = Column(Integer)
    CreatedAt         = Column(DateTime)

# ── MATURITY SAMPLES ─────────────────────────────────────────────
# Ground-truth ripeness/quality readings from refractometer, NIR, lab, etc.
# Used by the Maturity Engine to fit a per-field curve and predict the
# peak-antioxidant harvest date.
class FieldMaturitySample(Base):
    __tablename__ = "FieldMaturitySample"
    SampleID             = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID              = Column(Integer, index=True)
    BusinessID           = Column(Integer, index=True)
    PeopleID             = Column(Integer)
    SampleDate           = Column(Date)
    Cultivar             = Column(String(100))
    SampleSize           = Column(Integer)
    LabName              = Column(String(200))
    BrixDegrees          = Column(Decimal(5, 2))
    FirmnessKgF          = Column(Decimal(5, 2))
    AnthocyaninMgG       = Column(Decimal(7, 3))
    PH                   = Column(Decimal(4, 2))
    TitratableAcidityPct = Column(Decimal(5, 2))
    ColorScoreL          = Column(Decimal(6, 2))
    ColorScoreA          = Column(Decimal(6, 2))
    ColorScoreB          = Column(Decimal(6, 2))
    DryMatterPct         = Column(Decimal(5, 2))
    Notes                = Column(Text)
    ImageUrl             = Column(String(1000))
    CreatedAt            = Column(DateTime)
    UpdatedAt            = Column(DateTime)


class FieldHarvestTarget(Base):
    __tablename__ = "FieldHarvestTarget"
    TargetID         = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID          = Column(Integer, index=True)
    BusinessID       = Column(Integer, index=True)
    DestinationLabel = Column(String(200))
    DestinationMiles = Column(Decimal(7, 1))    # one-way road miles farm → DC
    ReceivingLagDays = Column(Integer)
    ShelfTargetDate  = Column(Date)
    Notes            = Column(Text)
    CreatedAt        = Column(DateTime)
    UpdatedAt        = Column(DateTime)


# ── FIELD ASSESSMENT REPORTS ─────────────────────────────────────
# Persisted Saige-authored consultant reports. ReportJSON is the parsed
# structured payload the UI renders; RawText is the raw LLM output kept for
# audit; ContextJSON is the input data snapshot so we can replay or RAG it.
class FieldAssessmentReport(Base):
    __tablename__ = "FieldAssessmentReport"
    ReportID      = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID       = Column(Integer, index=True)
    BusinessID    = Column(Integer, index=True)
    PeopleID      = Column(Integer)
    GeneratedAt   = Column(DateTime)
    Headline      = Column(String(500))
    OverallHealth = Column(String(100))
    Confidence    = Column(String(20))
    ReportJSON    = Column(Text)
    RawText       = Column(Text)
    ContextJSON   = Column(Text)
    DeletedAt     = Column(DateTime)


# ── CROP ROTATION ────────────────────────────────────────────────
class CropRotationEntry(Base):
    __tablename__ = "CropRotationEntry"
    RotationID   = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID      = Column(Integer, index=True)
    BusinessID   = Column(Integer, index=True)
    SeasonYear   = Column(Integer)
    CropName     = Column(String(255))
    Variety      = Column(String(255))
    PlantingDate = Column(Date)
    HarvestDate  = Column(Date)
    YieldAmount  = Column(Decimal(10, 2))
    YieldUnit    = Column(String(50))
    IsCoverCrop  = Column(Boolean, default=False)
    Notes        = Column(Text)
    CreatedAt    = Column(DateTime)
    UpdatedAt    = Column(DateTime)

# ── FIELD SCOUTING ───────────────────────────────────────────────
class FieldScout(Base):
    __tablename__ = "FieldScout"
    ScoutID    = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID    = Column(Integer, index=True)
    BusinessID = Column(Integer, index=True)
    PeopleID   = Column(Integer)
    ObservedAt = Column(DateTime)
    Category   = Column(String(50))
    Severity   = Column(String(20))
    Notes      = Column(Text)
    Latitude   = Column(Decimal(10, 7))
    Longitude  = Column(Decimal(10, 7))
    ImageUrl   = Column(String(1000))
    CreatedAt  = Column(DateTime)

# ── SOIL SAMPLES ─────────────────────────────────────────────────
class FieldSoilSample(Base):
    __tablename__ = "FieldSoilSample"
    SampleID      = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID       = Column(Integer, index=True)
    BusinessID    = Column(Integer, index=True)
    SampleDate    = Column(Date)
    SampleLabel   = Column(String(100))
    Latitude      = Column(Decimal(10, 7))
    Longitude     = Column(Decimal(10, 7))
    Depth_cm      = Column(Integer)
    pH            = Column(Decimal(4, 2))
    OrganicMatter = Column(Decimal(5, 2))
    Nitrogen      = Column(Decimal(8, 2))
    Phosphorus    = Column(Decimal(8, 2))
    Potassium     = Column(Decimal(8, 2))
    Sulfur        = Column(Decimal(8, 2))
    Calcium       = Column(Decimal(8, 2))
    Magnesium     = Column(Decimal(8, 2))
    CEC           = Column(Decimal(6, 2))
    Notes         = Column(Text)
    CreatedAt     = Column(DateTime)

# ── PRESCRIPTIONS ─────────────────────────────────────────────────
class FieldPrescription(Base):
    __tablename__ = "FieldPrescription"
    PrescriptionID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID        = Column(Integer, index=True)
    BusinessID     = Column(Integer, index=True)
    Name           = Column(String(255))
    Product        = Column(String(255))
    Unit           = Column(String(50))
    IndexKey       = Column(String(20))
    ZoneMethod     = Column(String(50))
    NumZones       = Column(Integer)
    ZoneRatesJSON  = Column(Text)
    AnalysisDate   = Column(Date)
    Notes          = Column(Text)
    CreatedAt      = Column(DateTime)

# ── FIELD ACTIVITY LOG ───────────────────────────────────────────
class FieldActivityLog(Base):
    __tablename__ = "FieldActivityLog"
    ActivityID   = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FieldID      = Column(Integer, index=True)
    BusinessID   = Column(Integer, index=True)
    PeopleID     = Column(Integer)
    ActivityDate = Column(Date)
    ActivityType = Column(String(50))
    Product      = Column(String(255))
    Rate         = Column(Decimal(10, 2))
    RateUnit     = Column(String(50))
    OperatorName = Column(String(255))
    Notes        = Column(Text)
    CreatedAt    = Column(DateTime)

# ── BUSINESS ACCESS ──────────────────────────────────────────────
class BusinessAccess(Base):
    __tablename__ = "BusinessAccess"
    BusinessAccessID = Column(Integer, primary_key=True, index=True)
    BusinessID       = Column(Integer)
    PeopleID         = Column(Integer)
    AccessLevelID    = Column(Integer)
    Active           = Column(SmallInteger)
    CreatedAt        = Column(DateTime)
    RevokedAt        = Column(DateTime)
    Role             = Column(String(100))

# ── BUSINESS TYPE LOOKUP ─────────────────────────────────────────
class BusinessTypeLookup(Base):
    __tablename__ = "businesstypelookup"
    BusinessTypeID      = Column(Integer, primary_key=True, index=True)
    BusinessType        = Column(String(255))
    BusinessTypeIcon    = Column(String(255))
    BusinessTypeIDOrder = Column(Integer)

    # ── COUNTRY ──────────────────────────────────────────────────────
class Country(Base):
    __tablename__ = "country"
    country_id = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100))
    iso_code   = Column(String(10))


    # ── STATE / PROVINCE ─────────────────────────────────────────────
class StateProvince(Base):
    __tablename__ = "state_province"
    StateIndex   = Column(Integer, primary_key=True, index=True)
    name         = Column(String(100))
    abbreviation = Column(String(10))
    country_id   = Column(Integer)

class Websites(Base):
    __tablename__ = "Websites"
    WebsitesID  = Column(Integer, primary_key=True, index=True)
    Website     = Column(String(500))
    websitepath = Column(String(500))
    watermark   = Column(DateTime)

# ── WEBSITE BUILDER ──────────────────────────────────────────────
class BusinessWebsite(Base):
    __tablename__ = "BusinessWebsite"
    WebsiteID       = Column(Integer, primary_key=True, autoincrement=True)
    BusinessID      = Column(Integer, nullable=False, index=True)
    SiteName        = Column(String(255))
    Slug            = Column(String(100), unique=True)
    Tagline         = Column(String(500))
    LogoURL         = Column(String(1000))
    PrimaryColor    = Column(String(20), default='#3D6B34')
    SecondaryColor  = Column(String(20), default='#819360')
    AccentColor     = Column(String(20), default='#FFC567')
    BgColor         = Column(String(20), default='#FFFFFF')
    ScreenBackgroundColor = Column(String(20))
    PageBackgroundColor   = Column(String(20))
    BgImageURL      = Column(String(1000))
    BgGradient      = Column(String(500))
    BodyContentWidth= Column(String(20), default='100%')
    BodyBgWidth     = Column(String(20), default='100%')
    HeaderBgWidth   = Column(String(20), default='100%')
    FooterBgWidth   = Column(String(20), default='100%')
    TextColor       = Column(String(20), default='#111827')
    FontFamily      = Column(String(100), default='Inter, sans-serif')
    # Typography / type scale
    H1Size          = Column(String(20), default='40px')
    H1Weight        = Column(String(10), default='800')
    H1Color         = Column(String(20), default='')
    H1Align         = Column(String(10), default='left')
    H1Underline     = Column(Boolean, default=False)
    H1Italic        = Column(Boolean, default=False)
    H1Rule          = Column(Boolean, default=False)
    H1RuleColor     = Column(String(20), default='')
    H2Size          = Column(String(20), default='29px')
    H2Weight        = Column(String(10), default='700')
    H2Color         = Column(String(20), default='')
    H2Align         = Column(String(10), default='left')
    H2Underline     = Column(Boolean, default=False)
    H2Italic        = Column(Boolean, default=False)
    H2Rule          = Column(Boolean, default=False)
    H2RuleColor     = Column(String(20), default='')
    H3Size          = Column(String(20), default='21px')
    H3Weight        = Column(String(10), default='600')
    H3Color         = Column(String(20), default='')
    H3Align         = Column(String(10), default='left')
    H3Underline     = Column(Boolean, default=False)
    H3Italic        = Column(Boolean, default=False)
    H3Rule          = Column(Boolean, default=False)
    H3RuleColor     = Column(String(20), default='')
    H4Size          = Column(String(20), default='17px')
    H4Weight        = Column(String(10), default='600')
    H4Color         = Column(String(20), default='')
    H4Align         = Column(String(10), default='left')
    H4Underline     = Column(Boolean, default=False)
    H4Italic        = Column(Boolean, default=False)
    H4Rule          = Column(Boolean, default=False)
    H4RuleColor     = Column(String(20), default='')
    H1MarginTop     = Column(Integer, default=0)
    H1MarginBottom  = Column(Integer, default=8)
    H1Font          = Column(String(200), default='')
    H2MarginTop     = Column(Integer, default=0)
    H2MarginBottom  = Column(Integer, default=8)
    H2Font          = Column(String(200), default='')
    H3MarginTop     = Column(Integer, default=0)
    H3MarginBottom  = Column(Integer, default=6)
    H3Font          = Column(String(200), default='')
    H4MarginTop     = Column(Integer, default=0)
    H4MarginBottom  = Column(Integer, default=4)
    H4Font          = Column(String(200), default='')
    BodySize        = Column(String(20), default='16px')
    BodyLineHeight  = Column(String(10), default='1.75')
    BodyColor       = Column(String(20), default='')
    BodyAlign       = Column(String(10), default='left')
    BodyUnderline   = Column(Boolean, default=False)
    BodyItalic      = Column(Boolean, default=False)
    # Site-wide image styling
    ImageBorderRadius   = Column(Integer, default=0)       # percent 0-50
    ImageShadowEnabled  = Column(Boolean, default=False)
    ImageShadowColor    = Column(String(40), default='rgba(0,0,0,0.35)')
    ImageShadowDistance = Column(Integer, default=4)       # px
    ImageShadowBlur     = Column(Integer, default=8)       # px
    ImageShadowAngle    = Column(Integer, default=135)     # degrees 0-359
    BodyMarginTop   = Column(Integer, default=0)
    BodyMarginBottom= Column(Integer, default=12)
    BodyFont        = Column(String(200), default='')
    LinkColor           = Column(String(20), default='')
    LinkUnderline       = Column(Boolean, default=True)
    DropdownBgColor     = Column(String(50))
    DropdownHoverColor  = Column(String(50))
    DropdownBgColor2    = Column(String(50))
    DropdownGradientDir = Column(String(20), default='135deg')
    Phone           = Column(String(50))
    Email           = Column(String(255))
    Address         = Column(String(500))
    FacebookURL     = Column(String(500))
    InstagramURL    = Column(String(500))
    TwitterURL      = Column(String(500))
    NavTextColor    = Column(String(20), default='#FFFFFF')
    FooterBgColor   = Column(String(20))
    FooterBgImageURL= Column(String(1000))
    # Per-band backgrounds: each width band can carry its own colour and image.
    HeaderContentBgColor    = Column(String(20))
    HeaderContentBgImageURL = Column(String(1000))
    BodyBgColor             = Column(String(20))
    BodyBgImageURL          = Column(String(1000))
    BodyContentBgColor      = Column(String(20))
    BodyContentBgImageURL   = Column(String(1000))
    FooterContentBgColor    = Column(String(20))
    FooterContentBgImageURL = Column(String(1000))
    FooterHTML      = Column(Text)
    FooterHeight    = Column(Integer, default=200)
    FooterBottomRadius = Column(Integer, default=0)
    CopyrightBarBgColor = Column(String(20))
    CopyrightText   = Column(String(500))
    IsPublished     = Column(Boolean, default=False)
    MetaTitle       = Column(String(255))
    CanonicalURL    = Column(String(500))
    OgImageURL      = Column(String(1000))
    SeoExtrasJSON   = Column(Text)
    MenuStyleJSON   = Column(Text)
    FooterJSON      = Column(Text)
    # Width controls
    HeaderContentWidth = Column(String(20), default='100%')
    FooterContentWidth = Column(String(20), default='100%')
    # Top bar
    TopBarEnabled   = Column(Boolean, default=False)
    TopBarHTML      = Column(Text)
    TopBarBgColor   = Column(String(20), default='#f8f5ef')
    TopBarTextColor = Column(String(20), default='#333333')
    TopBarAlign     = Column(String(10), default='right')
    # Header banner
    HeaderBannerURL    = Column(String(1000))
    HeaderBannerBgColor = Column(String(20))
    HeaderHeight    = Column(Integer, default=120)
    ShowSiteName    = Column(Boolean, default=True)
    # Layout: 'banner_top' (default — logo banner above nav) or 'nav_top'
    # (slim nav above a larger centered-logo band, reference: oregonqha.com)
    HeaderLayout   = Column(String(20), default='banner_top')
    # Nav bar
    NavBgImageURL   = Column(String(1000))
    # Favicon
    FaviconURL      = Column(String(1000))
    CreatedAt       = Column(DateTime)
    UpdatedAt       = Column(DateTime)

class BusinessWebPage(Base):
    __tablename__ = "BusinessWebPage"
    PageID          = Column(Integer, primary_key=True, autoincrement=True)
    WebsiteID       = Column(Integer, nullable=False, index=True)
    BusinessID      = Column(Integer, nullable=False)
    PageName        = Column(String(255))
    Slug            = Column(String(100))
    PageTitle       = Column(String(255))
    MetaDescription = Column(String(500))
    SortOrder       = Column(Integer, default=0)
    IsPublished     = Column(Boolean, default=True)
    IsHomePage      = Column(Boolean, default=False)
    ParentPageID    = Column(Integer, nullable=True)
    IsNavHeading    = Column(Boolean, default=False)
    LinkURL         = Column(String(500), nullable=True)
    CreatedAt       = Column(DateTime)
    UpdatedAt       = Column(DateTime)

class BusinessWebBlock(Base):
    __tablename__ = "BusinessWebBlock"
    BlockID     = Column(Integer, primary_key=True, autoincrement=True)
    PageID      = Column(Integer, nullable=False, index=True)
    BlockType   = Column(String(50))
    BlockData   = Column(Text)   # JSON string
    SortOrder   = Column(Integer, default=0)
    CreatedAt   = Column(DateTime)
    UpdatedAt   = Column(DateTime)


class WebsiteCustomDomain(Base):
    """Indexed lookup table: one row per custom domain pointing to a WebsiteID.
    Populated automatically when CanonicalURL is saved and used for fast
    O(log n) domain resolution instead of a full-table LIKE scan."""
    __tablename__ = "WebsiteCustomDomain"
    DomainID  = Column(Integer, primary_key=True, autoincrement=True)
    WebsiteID = Column(Integer, nullable=False, index=True)
    Domain    = Column(String(255), nullable=False, unique=True)
    IsActive  = Column(Boolean, default=True)
    CreatedAt = Column(DateTime)


# ── SITE SETTINGS (single-row control table) ─────────────────────
class SiteSettings(Base):
    __tablename__ = "SiteSettings"
    id              = Column(Integer, primary_key=True, default=1)
    team_only_login = Column(Boolean, nullable=False, default=True)   # True = team members only
    signup_open     = Column(Boolean, nullable=False, default=False)  # True = join page visible




# ── BUSINESS BLOG POSTS ──────────────────────────────────────────
class BusinessBlogPost(Base):
    __tablename__ = "BusinessBlogPosts"
    PostID       = Column(Integer, primary_key=True, autoincrement=True)
    BusinessID   = Column(Integer, nullable=False, index=True)
    Title        = Column(String(500), nullable=False)
    Slug         = Column(String(500))
    Excerpt      = Column(String(1000))
    Content      = Column(Text)
    CoverImage   = Column(String(500))
    Category     = Column(String(100))
    IsPublished  = Column(Boolean, default=False)
    CreatedAt    = Column(DateTime)
    UpdatedAt    = Column(DateTime)


# ── ACCOUNTING ───────────────────────────────────────────────────

class AccountType(Base):
    __tablename__ = "AccountTypes"
    AccountTypeID       = Column(Integer, primary_key=True, index=True)
    Name                = Column(String(100))
    NormalBalance       = Column(String(10))   # Debit or Credit
    FinancialStatement  = Column(String(50))   # Balance Sheet, Income Statement

class Account(Base):
    __tablename__ = "Accounts"
    AccountID       = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID      = Column(Integer, index=True)
    AccountTypeID   = Column(Integer)
    AccountNumber   = Column(String(20))
    AccountName     = Column(String(255))
    Description     = Column(Text, nullable=True)
    ParentAccountID = Column(Integer, nullable=True)
    IsActive        = Column(Integer, default=1)
    IsSystem        = Column(Integer, default=0)
    CreatedAt       = Column(DateTime)
    UpdatedAt       = Column(DateTime)

class JournalEntry(Base):
    __tablename__ = "JournalEntries"
    JournalEntryID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID     = Column(Integer, index=True)
    EntryNumber    = Column(String(20))
    EntryDate      = Column(Date)
    Description    = Column(Text)
    Reference      = Column(String(100), nullable=True)
    SourceType     = Column(String(50), nullable=True)
    SourceID       = Column(Integer, nullable=True)
    IsPosted       = Column(Integer, default=1)
    CreatedBy      = Column(Integer)
    CreatedAt      = Column(DateTime)

class JournalEntryLine(Base):
    __tablename__ = "JournalEntryLines"
    JournalEntryLineID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    JournalEntryID     = Column(Integer)
    BusinessID         = Column(Integer, index=True)
    AccountID          = Column(Integer)
    DebitAmount        = Column(Decimal(19, 2), default=0)
    CreditAmount       = Column(Decimal(19, 2), default=0)
    Description        = Column(Text, nullable=True)
    LineOrder          = Column(Integer)

class AccountingCustomer(Base):
    __tablename__ = "AccountingCustomers"
    CustomerID       = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID       = Column(Integer, index=True)
    DisplayName      = Column(String(255))
    CompanyName      = Column(String(255), nullable=True)
    FirstName        = Column(String(100), nullable=True)
    LastName         = Column(String(100), nullable=True)
    Email            = Column(String(255), nullable=True)
    Phone            = Column(String(50), nullable=True)
    BillingAddress1  = Column(String(255), nullable=True)
    BillingCity      = Column(String(100), nullable=True)
    BillingState     = Column(String(50), nullable=True)
    BillingZip       = Column(String(20), nullable=True)
    BillingCountry   = Column(String(50), default='US')
    PaymentTerms     = Column(String(50), default='Net30')
    StripeCustomerID = Column(String(255), nullable=True)
    Notes            = Column(Text, nullable=True)
    IsActive         = Column(Integer, default=1)
    CreatedAt        = Column(DateTime)
    UpdatedAt        = Column(DateTime)

class AccountingVendor(Base):
    __tablename__ = "AccountingVendors"
    VendorID     = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID   = Column(Integer, index=True)
    DisplayName  = Column(String(255))
    CompanyName  = Column(String(255), nullable=True)
    FirstName    = Column(String(100), nullable=True)
    LastName     = Column(String(100), nullable=True)
    Email        = Column(String(255), nullable=True)
    Phone        = Column(String(50), nullable=True)
    Address1     = Column(String(255), nullable=True)
    City         = Column(String(100), nullable=True)
    State        = Column(String(50), nullable=True)
    Zip          = Column(String(20), nullable=True)
    Country      = Column(String(50), default='US')
    PaymentTerms = Column(String(50), default='Net30')
    Is1099       = Column(Integer, default=0)
    Notes        = Column(Text, nullable=True)
    IsActive     = Column(Integer, default=1)
    CreatedAt    = Column(DateTime)
    UpdatedAt    = Column(DateTime)

class Item(Base):
    __tablename__ = "Items"
    ItemID            = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID        = Column(Integer, index=True)
    ItemType          = Column(String(50), default='Service')
    SKU               = Column(String(50), nullable=True)
    Name              = Column(String(255))
    Description       = Column(Text, nullable=True)
    SalePrice         = Column(Decimal(19, 2), default=0)
    PurchasePrice     = Column(Decimal(19, 2), default=0)
    SaleAccountID     = Column(Integer, nullable=True)
    PurchaseAccountID = Column(Integer, nullable=True)
    Taxable           = Column(Integer, default=1)
    IsActive          = Column(Integer, default=1)
    CreatedAt         = Column(DateTime)

class Invoice(Base):
    __tablename__ = "Invoices"
    InvoiceID          = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID         = Column(Integer, index=True)
    CustomerID         = Column(Integer)
    InvoiceNumber      = Column(String(20))
    InvoiceDate        = Column(Date)
    DueDate            = Column(Date)
    Status             = Column(String(20), default='Draft')
    SubTotal           = Column(Decimal(19, 2), default=0)
    TaxAmount          = Column(Decimal(19, 2), default=0)
    TotalAmount        = Column(Decimal(19, 2), default=0)
    AmountPaid         = Column(Decimal(19, 2), default=0)
    BalanceDue         = Column(Decimal(19, 2), default=0)
    JournalEntryID     = Column(Integer, nullable=True)
    TermsAndConditions = Column(Text, nullable=True)
    Notes              = Column(Text, nullable=True)
    PaymentTerms       = Column(String(50), nullable=True)
    CreatedBy          = Column(Integer)
    CreatedAt          = Column(DateTime)
    PaidAt             = Column(DateTime, nullable=True)
    UpdatedAt          = Column(DateTime)

class InvoiceLine(Base):
    __tablename__ = "InvoiceLines"
    InvoiceLineID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    InvoiceID     = Column(Integer)
    BusinessID    = Column(Integer, index=True)
    ItemID        = Column(Integer, nullable=True)
    AccountID     = Column(Integer, nullable=True)
    Description   = Column(Text)
    Quantity      = Column(Decimal(19, 2))
    UnitPrice     = Column(Decimal(19, 2))
    TaxRateID     = Column(Integer, nullable=True)
    TaxAmount     = Column(Decimal(19, 2), default=0)
    LineTotal     = Column(Decimal(19, 2))
    LineOrder     = Column(Integer)

class Payment(Base):
    __tablename__ = "Payments"
    PaymentID             = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID            = Column(Integer, index=True)
    CustomerID            = Column(Integer)
    PaymentNumber         = Column(String(20))
    PaymentDate           = Column(Date)
    PaymentMethod         = Column(String(50))
    Amount                = Column(Decimal(19, 2))
    UnusedAmount          = Column(Decimal(19, 2), default=0)
    Reference             = Column(String(255), nullable=True)
    StripePaymentIntentID = Column(String(255), nullable=True)
    StripeChargeID        = Column(String(255), nullable=True)
    StripeFee             = Column(Decimal(19, 2), default=0)
    NetAmount             = Column(Decimal(19, 2))
    DepositAccountID      = Column(Integer, nullable=True)
    CreatedBy             = Column(Integer)
    CreatedAt             = Column(DateTime)

class PaymentApplication(Base):
    __tablename__ = "PaymentApplications"
    PaymentApplicationID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    PaymentID            = Column(Integer)
    InvoiceID            = Column(Integer)
    BusinessID           = Column(Integer, index=True)
    AmountApplied        = Column(Decimal(19, 2))

class Bill(Base):
    __tablename__ = "Bills"
    BillID      = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID  = Column(Integer, index=True)
    VendorID    = Column(Integer)
    BillNumber  = Column(String(20), nullable=True)
    BillDate    = Column(Date)
    DueDate     = Column(Date)
    Status      = Column(String(20), default='Open')
    SubTotal    = Column(Decimal(19, 2), default=0)
    TaxAmount   = Column(Decimal(19, 2), default=0)
    TotalAmount = Column(Decimal(19, 2), default=0)
    AmountPaid  = Column(Decimal(19, 2), default=0)
    BalanceDue  = Column(Decimal(19, 2), default=0)
    Notes       = Column(Text, nullable=True)
    CreatedBy   = Column(Integer)
    CreatedAt   = Column(DateTime)

class BillLine(Base):
    __tablename__ = "BillLines"
    BillLineID  = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BillID      = Column(Integer)
    BusinessID  = Column(Integer, index=True)
    ItemID      = Column(Integer, nullable=True)
    AccountID   = Column(Integer, nullable=True)
    Description = Column(Text)
    Quantity    = Column(Decimal(19, 2))
    UnitPrice   = Column(Decimal(19, 2))
    TaxRateID   = Column(Integer, nullable=True)
    TaxAmount   = Column(Decimal(19, 2), default=0)
    LineTotal   = Column(Decimal(19, 2))
    LineOrder   = Column(Integer)

class Expense(Base):
    __tablename__ = "Expenses"
    ExpenseID        = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID       = Column(Integer, index=True)
    VendorID         = Column(Integer, nullable=True)
    PaymentAccountID = Column(Integer, nullable=True)
    ExpenseDate      = Column(Date)
    PaymentMethod    = Column(String(50))
    TotalAmount      = Column(Decimal(19, 2))
    Reference        = Column(String(255), nullable=True)
    Notes            = Column(Text, nullable=True)
    CreatedBy        = Column(Integer)
    CreatedAt        = Column(DateTime)

class ExpenseLine(Base):
    __tablename__ = "ExpenseLines"
    ExpenseLineID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ExpenseID     = Column(Integer)
    BusinessID    = Column(Integer, index=True)
    AccountID     = Column(Integer)
    Description   = Column(Text)
    Amount        = Column(Decimal(19, 2))
    IsBillable    = Column(Integer, default=0)
    CustomerID    = Column(Integer, nullable=True)
    LineOrder     = Column(Integer)

class FiscalYear(Base):
    __tablename__ = "FiscalYears"
    FiscalYearID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    BusinessID   = Column(Integer, index=True)
    YearName     = Column(String(50))
    StartDate    = Column(Date)
    EndDate      = Column(Date)

class FiscalPeriod(Base):
    __tablename__ = "FiscalPeriods"
    FiscalPeriodID = Column(Integer, primary_key=True, index=True, autoincrement=True)
    FiscalYearID   = Column(Integer)
    BusinessID     = Column(Integer, index=True)
    PeriodNumber   = Column(Integer)
    PeriodName     = Column(String(50))
    StartDate      = Column(Date)
    EndDate        = Column(Date)
