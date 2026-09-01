-- Stripe Product IDs for SubscriptionPackage, so the backend can create the
-- Stripe Product and Price for a package on demand instead of an admin having
-- to build them by hand in the Stripe dashboard.
--
-- Split live/test because a Stripe test account and a live account are separate
-- object namespaces -- a product created in test mode does not exist in live.
--
-- The Price IDs added by add_package_stripe_prices.sql keep their meaning: they
-- are still the source of truth for what gets billed. The difference is that the
-- backend now fills them in itself the first time a package is sold, and an
-- admin can still paste an existing Stripe price in to override that.
--
-- Idempotent: safe to re-run.

IF COL_LENGTH('SubscriptionPackage', 'StripeProductID') IS NULL
    ALTER TABLE SubscriptionPackage ADD StripeProductID NVARCHAR(255) NULL;
GO

IF COL_LENGTH('SubscriptionPackage', 'StripeProductIDTest') IS NULL
    ALTER TABLE SubscriptionPackage ADD StripeProductIDTest NVARCHAR(255) NULL;
GO
