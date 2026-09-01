-- Adds per-package listing allowances to SubscriptionPackage and seeds the free
-- "Basic Rancher Package" plan for Livestock of America.
--
-- Until now a package was a pure on/off bundle: SubscriptionPackageFeature said
-- whether a business could reach the Livestock Marketplace at all, but not how
-- many head it could list. A free tier needs that distinction, so the allowance
-- lives on the package itself rather than in the feature join table (the join
-- table's primary key is (PackageID, FeatureID), leaving nowhere for a count).
--
-- NULL means unlimited, which keeps every existing package behaving exactly as
-- it does today without a backfill. 0 means the plan does not include that
-- listing type at all.
--
-- Idempotent: safe to re-run.

-- ── 1. Columns ───────────────────────────────────────────────────────────────
IF COL_LENGTH('SubscriptionPackage', 'MaxForSaleListings') IS NULL
    ALTER TABLE SubscriptionPackage ADD MaxForSaleListings INT NULL;
GO

IF COL_LENGTH('SubscriptionPackage', 'MaxStudListings') IS NULL
    ALTER TABLE SubscriptionPackage ADD MaxStudListings INT NULL;
GO

IF COL_LENGTH('SubscriptionPackage', 'MaxDirectoryListings') IS NULL
    ALTER TABLE SubscriptionPackage ADD MaxDirectoryListings INT NULL;
GO

-- ── 2. Seed the free Basic Rancher Package ───────────────────────────────────
-- BusinessTypeID 8 is Farm / Ranch, shown as "Ranch" on Livestock of America.
-- SortOrder 0 places it ahead of Rancher Pro (SortOrder 1) so the free plan
-- reads first in the signup picker.
IF NOT EXISTS (SELECT 1 FROM SubscriptionPackage WHERE PackageName = 'Basic Rancher Package')
BEGIN
    INSERT INTO SubscriptionPackage
        (PackageName, Description, BusinessTypeID, MonthlyPrice, YearlyPrice,
         IsActive, SortOrder, MaxForSaleListings, MaxStudListings, MaxDirectoryListings)
    VALUES (
        'Basic Rancher Package',
        N'<p>A free way to get your ranch on Livestock of America. List a handful ' +
        N'of animals for sale, offer a few studs for breeding, and claim your spot ' +
        N'in the directory so buyers can find you.</p>' +
        N'<ul>' +
        N'<li>Up to <strong>5 livestock listings</strong> for sale</li>' +
        N'<li>Up to <strong>5 stud listings</strong> for breeding</li>' +
        N'<li><strong>One directory listing</strong> for your ranch</li>' +
        N'</ul>' +
        N'<p>Ready to list your whole herd? Rancher Pro removes the limits.</p>',
        8, 0.00, 0.00, 1, 0,
        5,   -- MaxForSaleListings
        5,   -- MaxStudListings
        1    -- MaxDirectoryListings
    );
END
GO

-- ── 3. Features for Basic Rancher Package ────────────────────────────────────────────
-- CategoryID 5 is Livestock Marketplace (covers both for-sale and stud listings,
-- which the columns above then cap) and 33 is Directory Listing.
INSERT INTO SubscriptionPackageFeature (PackageID, FeatureID)
SELECT p.PackageID, v.FeatureID
FROM SubscriptionPackage p
CROSS JOIN (VALUES (5), (33)) AS v(FeatureID)
WHERE p.PackageName = 'Basic Rancher Package'
  AND NOT EXISTS (
      SELECT 1 FROM SubscriptionPackageFeature f
      WHERE f.PackageID = p.PackageID AND f.FeatureID = v.FeatureID
  );
GO

-- ── 4. Rancher Pro keeps unlimited listings ──────────────────────────────────
-- Stated explicitly so the intent survives a future NOT NULL default.
UPDATE SubscriptionPackage
SET MaxForSaleListings = NULL, MaxStudListings = NULL, MaxDirectoryListings = NULL
WHERE PackageName = 'Rancher Pro';
GO
