-- Greenland had no rows in state_province, so /api/businesses/states?country=Greenland
-- returned an empty list and the required State field on the account signup form
-- could never be satisfied.
--
-- Greenland is divided into five municipalities and two unincorporated areas.
-- Abbreviations use the ISO 3166-2:GL codes without the GL- prefix, matching the
-- two-letter style already used for US states and Canadian provinces. The two
-- unincorporated areas have no ISO code, so NP and PI are used.
--
-- StateIndex is an IDENTITY column — deliberately not supplied.
-- country_id is varchar in this table; Greenland is 1086.
-- Idempotent: re-running inserts nothing.

DECLARE @GreenlandID varchar(255) = (
    SELECT CAST(country_id AS varchar(255)) FROM country WHERE name = 'Greenland'
);

INSERT INTO state_province (name, abbreviation, country_id)
SELECT v.name, v.abbreviation, @GreenlandID
FROM (VALUES
    -- Municipalities
    ('Avannaata',                        'AV'),
    ('Kujalleq',                         'KU'),
    ('Qeqertalik',                       'QT'),
    ('Qeqqata',                          'QE'),
    ('Sermersooq',                       'SM'),
    -- Unincorporated areas
    ('Northeast Greenland National Park', 'NP'),
    ('Pituffik',                          'PI')
) AS v(name, abbreviation)
WHERE @GreenlandID IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM state_province sp
      WHERE sp.country_id = @GreenlandID AND sp.name = v.name
  );
