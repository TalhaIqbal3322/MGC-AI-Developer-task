-- queries.sql  (SQLite dialect; runs unchanged on PostgreSQL except where noted)
-- Run:  sqlite3 leads.db < queries.sql      (after `python load_leads.py`)
--
-- Queries 1 and 3 read from leads_canonical (the de-duplicated view from schema.sql),
-- so a lead that was entered twice is not counted twice. Query 2 shows how that
-- view's rule works and how to lock it in.


-- =====================================================================
-- QUERY 1: Conversion rate by lead source, best first, sources with 200+ leads only
-- =====================================================================
SELECT
    source,
    COUNT(*)                                   AS leads,
    SUM(converted)                             AS conversions,
    ROUND(100.0 * SUM(converted) / COUNT(*), 2) AS conversion_rate_pct
FROM leads_canonical
GROUP BY source
HAVING COUNT(*) >= 200                          -- HAVING, not WHERE: the filter is on the group size
ORDER BY conversion_rate_pct DESC, leads DESC;  -- leads DESC = stable tie-break


-- =====================================================================
-- QUERY 2: Duplicate leads, one canonical row per duplicate pair
-- =====================================================================
-- What a duplicate is here: two rows with the same crm_record_hash (the CRM's content
-- fingerprint) but different lead_id. In this dump there are 160 such pairs; the two
-- rows are identical in every field except lead_id (the re-entered copy ends in '-B').
--
-- Deterministic winner rule:  earliest created_at, then lowest lead_id.
--   * created_at first: the first entry is the original record.
--   * lead_id second: breaks ties (here the timestamps are equal), and is always
--     decisive because lead_id is the primary key. Same data -> same winner, every run.

-- 2a. Every row that belongs to a duplicate pair, labelled KEEP or DROP (320 rows)
WITH ranked AS (
    SELECT
        l.*,
        ROW_NUMBER() OVER (PARTITION BY crm_record_hash
                           ORDER BY created_at ASC, lead_id ASC) AS rn,
        COUNT(*)      OVER (PARTITION BY crm_record_hash)       AS copies
    FROM leads l
)
SELECT lead_id, crm_record_hash, created_at, source, city,
       CASE WHEN rn = 1 THEN 'KEEP' ELSE 'DROP' END AS decision
FROM ranked
WHERE copies > 1
ORDER BY crm_record_hash, rn;

-- 2b. Exactly one canonical row per duplicate pair (160 rows)
WITH ranked AS (
    SELECT
        l.*,
        ROW_NUMBER() OVER (PARTITION BY crm_record_hash
                           ORDER BY created_at ASC, lead_id ASC) AS rn,
        COUNT(*)      OVER (PARTITION BY crm_record_hash)       AS copies
    FROM leads l
)
SELECT *
FROM ranked
WHERE copies > 1 AND rn = 1;

-- 2c. Cleanup: delete the losing copies (run once; the view leads_canonical does the
--     same thing without deleting anything)
--
--   DELETE FROM leads
--   WHERE lead_id IN (
--       SELECT lead_id FROM (
--           SELECT lead_id,
--                  ROW_NUMBER() OVER (PARTITION BY crm_record_hash
--                                     ORDER BY created_at ASC, lead_id ASC) AS rn
--           FROM leads
--       ) WHERE rn > 1
--   );

-- PREVENTING DUPLICATES AT THE SCHEMA LEVEL
-- After the cleanup above, make the database refuse duplicates:
--
--   DROP INDEX idx_leads_crm_hash;
--   CREATE UNIQUE INDEX ux_leads_crm_hash ON leads (crm_record_hash);
--
-- From then on a second insert of the same lead fails with a UNIQUE violation and the
-- application can say "this lead already exists" instead of creating a copy. Writers
-- that want "insert if new" semantics use  INSERT ... ON CONFLICT (crm_record_hash) DO NOTHING.
-- Caveat: this only works if the hash is computed from the lead's identity (e.g. normalised
-- phone number + project), NOT from fields an agent can vary. If two agents can type the
-- same person differently, normalise the phone number (E.164) in a column and put the
-- UNIQUE constraint on that column instead.


-- =====================================================================
-- QUERY 3: Conversion rate by city, after normalising the messy spellings inside the query
-- =====================================================================
-- Step 1: lower() + trim() removes case and whitespace noise ('KARACHI', 'karachi ').
-- Step 2: a CASE maps the abbreviations (isb, rwp, khi) to the full city name.
-- Step 3: everything else is re-capitalised ('PESHAWAR' -> 'Peshawar') so new
--         all-caps cities still group correctly without editing the query.
WITH normalised AS (
    SELECT
        CASE LOWER(TRIM(city))
            WHEN 'isb' THEN 'Islamabad'
            WHEN 'rwp' THEN 'Rawalpindi'
            WHEN 'khi' THEN 'Karachi'
            WHEN 'lhr' THEN 'Lahore'          -- not in this dump, but the obvious next abbreviation
            ELSE UPPER(SUBSTR(TRIM(city), 1, 1)) || LOWER(SUBSTR(TRIM(city), 2))
        END AS city_clean,
        converted
    FROM leads_canonical
)
SELECT
    city_clean                                   AS city,
    COUNT(*)                                     AS leads,
    SUM(converted)                               AS conversions,
    ROUND(100.0 * SUM(converted) / COUNT(*), 2)  AS conversion_rate_pct
FROM normalised
GROUP BY city_clean
ORDER BY conversion_rate_pct DESC, leads DESC;
