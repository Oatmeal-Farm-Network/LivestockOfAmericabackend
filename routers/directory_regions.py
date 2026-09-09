"""Which countries the public directories cover.

Livestock Of America lists organizations in the United States, Canada and
Greenland. Anything else is excluded from the directory listings, the directory
search, the country filter and the services directory.

Note this is a narrower set than the blog filter in routers/blog.py, which also
includes Mexico (1140). The two are deliberately separate: change one and the
other stays as it is.

No single column decides the country, because the legacy address data is not
consistent. Three signals are checked, in the same shape the blog filter uses:

    Address.country_id      authoritative whenever it is set. Some rows
                            contradict themselves -- country_id Ethiopia with
                            AddressCountry 'USA' -- so the weaker signals below
                            are consulted only when this one is absent.
    Address.AddressCountry  free text -- 'USA', 'CANADA', 'CAN', 'United States
                            (USA)', and some rows holding a bare id like '1039'.
                            Matched against an explicit list rather than a LIKE,
                            so 'United Kingdom' cannot slip through on 'UK' and
                            'CANADA' cannot match on a substring.
    Address.StateIndex      resolved through state_province, which is what
                            rescues businesses carrying a state but no country
                            at all.

EXISTS rather than a join: state_province can match more than one row for the
legacy AddressState values, and a join would duplicate the business.

A business with no address row at all does not match, so it is excluded. That is
deliberate -- it cannot be shown to be in the service area, and it has no city,
state or country to display in a directory entry either.

Expects the Business table aliased as `b`.
"""

# country.country_id
DIRECTORY_COUNTRY_IDS = (1228, 1039, 1086)  # USA, Canada, Greenland

DIRECTORY_COUNTRY_NAMES = (
    'USA', 'US', 'U.S.', 'U.S.A.', 'UNITED STATES', 'UNITED STATES OF AMERICA',
    'UNITED STATES (USA)', 'AMERICA',
    'CANADA', 'CAN', 'CA',
    'GREENLAND', 'GRL', 'GL', 'KALAALLIT NUNAAT',
)

_IDS = ", ".join(str(i) for i in DIRECTORY_COUNTRY_IDS)
_NAMES = ", ".join("'%s'" % n for n in DIRECTORY_COUNTRY_NAMES)

IN_DIRECTORY_REGION_SQL = f"""
    EXISTS (
        SELECT 1 FROM Address dir_a
        WHERE dir_a.AddressID = b.AddressID
          AND (
                TRY_CAST(dir_a.country_id AS INT) IN ({_IDS})
             OR (
                    -- The weaker signals only get a say when country_id is
                    -- absent. Some rows carry country_id = Ethiopia alongside
                    -- AddressCountry = 'USA'; the structured id is the one to
                    -- believe, and ORing them let the free text win.
                    TRY_CAST(dir_a.country_id AS INT) IS NULL
                AND (
                        TRY_CAST(dir_a.AddressCountry AS INT) IN ({_IDS})
                     OR UPPER(LTRIM(RTRIM(dir_a.AddressCountry))) IN ({_NAMES})
                     OR EXISTS (
                            SELECT 1 FROM state_province dir_sp
                            WHERE dir_sp.StateIndex = dir_a.StateIndex
                              AND TRY_CAST(dir_sp.country_id AS INT) IN ({_IDS})
                        )
                    )
                )
          )
    )
"""
