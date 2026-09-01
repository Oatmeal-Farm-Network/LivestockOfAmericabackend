-- Stripe price IDs for SubscriptionPackage, so a paid package can be sold
-- through Stripe Checkout during account setup.
--
-- SubscriptionLevels already carries StripeAPIID (live) and StripeAPIIDTest
-- (test) and platform_subscriptions._price_id_for picks between them using
-- OFNPlatformSettings.StripeTestMode. Packages need the same live/test split,
-- doubled because a package can be billed monthly or yearly.
--
-- Prices are NOT stored here -- MonthlyPrice/YearlyPrice remain the display
-- figures and Stripe remains the authority on what is actually charged. These
-- columns only say which Stripe Price object to bill against.
--
-- NULL means "not sellable through Stripe on that cycle in that mode", which is
-- how every existing package starts. The checkout endpoint refuses with a clear
-- message rather than silently charging the wrong thing.
--
-- Idempotent: safe to re-run.

IF COL_LENGTH('SubscriptionPackage', 'StripePriceIDMonthly') IS NULL
    ALTER TABLE SubscriptionPackage ADD StripePriceIDMonthly NVARCHAR(255) NULL;
GO

IF COL_LENGTH('SubscriptionPackage', 'StripePriceIDMonthlyTest') IS NULL
    ALTER TABLE SubscriptionPackage ADD StripePriceIDMonthlyTest NVARCHAR(255) NULL;
GO

IF COL_LENGTH('SubscriptionPackage', 'StripePriceIDYearly') IS NULL
    ALTER TABLE SubscriptionPackage ADD StripePriceIDYearly NVARCHAR(255) NULL;
GO

IF COL_LENGTH('SubscriptionPackage', 'StripePriceIDYearlyTest') IS NULL
    ALTER TABLE SubscriptionPackage ADD StripePriceIDYearlyTest NVARCHAR(255) NULL;
GO
