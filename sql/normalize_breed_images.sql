-- Normalise SpeciesBreedLookupTable.BreedImage to the canonical GCS location.
--
-- Before: legacy web-server paths, in two shapes
--     /uploads/206462surialpaca.webp                                  (1783 rows)
--     https://www.Oatmealfarmnetwork.com/uploads/206690Perlfee.webp     (18 rows)
--
-- After:  the object's real home, matching how Photos.Photo1..8 already store
--         animal images
--     https://storage.googleapis.com/oatmeal-farm-network-images/Animals/<file>
--
-- Both backends' _fix_image_url() pass a storage.googleapis.com URL through
-- untouched, so no rewriting happens at request time once this has run.
--
-- Idempotent: rows already pointing at GCS are skipped by the WHERE clause.
-- Reversible from scratchpad/breedimage_backup.csv.

DECLARE @GCS varchar(200) =
    'https://storage.googleapis.com/oatmeal-farm-network-images/Animals/';

UPDATE SpeciesBreedLookupTable
SET BreedImage =
        @GCS +
        -- everything after the final '/'
        REVERSE(LEFT(REVERSE(LTRIM(RTRIM(BreedImage))),
                     CHARINDEX('/', REVERSE(LTRIM(RTRIM(BreedImage)))) - 1))
WHERE BreedImage IS NOT NULL
  AND LTRIM(RTRIM(BreedImage)) NOT IN ('', '0')
  AND CHARINDEX('/', BreedImage) > 0                    -- has a path to strip
  AND BreedImage NOT LIKE 'https://storage.googleapis.com/%';  -- already done
