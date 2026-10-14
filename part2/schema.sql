-- schema.sql  (SQLite dialect; PostgreSQL notes inline)
--
-- Design: ONE table. The CSV is one row per lead, every column describes that
-- lead, and there is no repeating group, so splitting it into agents / sources /
-- cities tables would add joins without removing any real redundancy.
-- (If MGC later tracks agents or projects as entities with their own attributes,
--  agent and area would be the first columns I would split out.)
--
-- The table stores the dump AS IT CAME OUT of the CRM, including the known
-- duplicate rows, so nothing is silently lost on import. Duplicates are resolved
-- in a view (leads_canonical, bottom of file) with a deterministic rule.
-- Spelling mess in `city` is also kept raw here and normalised at query time.

PRAGMA foreign_keys = ON;

DROP VIEW  IF EXISTS leads_canonical;
DROP TABLE IF EXISTS leads;

CREATE TABLE leads (
    lead_id                      TEXT    PRIMARY KEY,           -- e.g. 'MGC-104974'; the '-B' suffix marks a re-entered copy
    created_at                   TEXT    NOT NULL,              -- ISO 'YYYY-MM-DD HH:MM:SS' (PG: TIMESTAMP NOT NULL)
    source                       TEXT    NOT NULL,              -- Facebook Ads, Referral, ... (9 clean values in this dump)
    city                         TEXT    NOT NULL,              -- RAW, messy: 'ISB', 'Rwp', 'khi', 'KARACHI'
    area                         TEXT,                          -- NULL for ~5% of rows
    property_type                TEXT    NOT NULL,
    budget_pkr_lac               REAL    CHECK (budget_pkr_lac IS NULL OR budget_pkr_lac > 0),
    bedrooms                     INTEGER CHECK (bedrooms IS NULL OR bedrooms BETWEEN 0 AND 20),
                                                                -- NULL is legitimate for plots / shops, not "missing"
    first_response_minutes       REAL    CHECK (first_response_minutes IS NULL OR first_response_minutes >= 0),
    calls_made                   INTEGER NOT NULL DEFAULT 0 CHECK (calls_made >= 0),
    total_call_seconds           REAL    NOT NULL DEFAULT 0 CHECK (total_call_seconds >= 0),
    whatsapp_replies             INTEGER NOT NULL DEFAULT 0 CHECK (whatsapp_replies >= 0),
    site_visits                  INTEGER NOT NULL DEFAULT 0 CHECK (site_visits >= 0),
    agent_experience_years       REAL    CHECK (agent_experience_years IS NULL OR agent_experience_years >= 0),
    is_overseas                  INTEGER NOT NULL DEFAULT 0 CHECK (is_overseas IN (0, 1)),            -- PG: BOOLEAN
    referred_by_existing_client  INTEGER NOT NULL DEFAULT 0 CHECK (referred_by_existing_client IN (0, 1)),
    has_financing_approved       INTEGER NOT NULL DEFAULT 0 CHECK (has_financing_approved IN (0, 1)),
    token_amount_received_pkr    REAL    NOT NULL DEFAULT 0 CHECK (token_amount_received_pkr >= 0),   -- PG: NUMERIC(14,2)
    crm_record_hash              INTEGER NOT NULL,              -- content fingerprint; equal hash => same lead entered twice
    converted                    INTEGER NOT NULL CHECK (converted IN (0, 1))                         -- the outcome
);

-- Reporting filters / groups on these:
CREATE INDEX idx_leads_source      ON leads (source);
CREATE INDEX idx_leads_created_at  ON leads (created_at);
-- Non-unique on purpose: this table holds the raw dump, which contains duplicates.
-- Dedup lookups need it fast. See queries.sql (Query 2) for turning it into a UNIQUE index.
CREATE INDEX idx_leads_crm_hash    ON leads (crm_record_hash);

-- Canonical rows: exactly one row per crm_record_hash.
-- Winner rule (deterministic, no ties possible because lead_id is the primary key):
--   1) earliest created_at wins  (the first entry is the original)
--   2) then lowest lead_id wins  ('MGC-104974' sorts before 'MGC-104974-B')
CREATE VIEW leads_canonical AS
SELECT *
FROM (
    SELECT l.*,
           ROW_NUMBER() OVER (
               PARTITION BY crm_record_hash
               ORDER BY created_at ASC, lead_id ASC
           ) AS rn
    FROM leads l
)
WHERE rn = 1;
