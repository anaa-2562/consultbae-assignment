-- ConsultBae unified database (SQLite).
-- Design notes:
--   * `person` is the golden record: exactly one row per real human.
--   * Alternate emails/phones live in child tables, so the "same person, two
--     mailboxes" case (Nikhil Chopra) is preserved rather than overwritten.
--   * `source_record` keeps the raw row verbatim (JSON) for every ingested line,
--     so any merge decision can be audited back to the exact source cell.
--   * `data_issue` is the machine-generated backing store for the Task 4 report.
--   * `match_review` is the human queue: matches the pipeline refused to make
--     automatically because they were ambiguous.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS person (
    person_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name            TEXT    NOT NULL,
    primary_email        TEXT,
    primary_phone        TEXT,             -- E.164, +91XXXXXXXXXX
    city                 TEXT,

    -- recruitment profile (source1)
    experience_years     REAL,
    current_ctc_inr      INTEGER,          -- normalised to annual INR
    ctc_source_unit      TEXT,             -- 'lpa' | 'inr_annual'
    applied_date         TEXT,             -- ISO-8601

    -- gig profile (source2)
    gig_rate_inr_hour    REAL,
    gig_rate_inr_month   INTEGER,
    gig_rate_unit        TEXT,             -- 'per_hour' | 'per_month' (as given)
    gig_status           TEXT,             -- 'active' | 'inactive' | 'paused'

    -- CBNexus profile (source3)
    cbnexus_verified     INTEGER,          -- 1 / 0 / NULL(unknown)
    projects_completed   INTEGER,

    -- enrichment (written back by the n8n LLM flow, Task 2)
    skill_category       TEXT,
    skill_category_conf  REAL,
    skill_category_by    TEXT,
    skill_category_at    TEXT,

    -- provenance
    source_count         INTEGER NOT NULL DEFAULT 1,
    in_source1           INTEGER NOT NULL DEFAULT 0,
    in_source2           INTEGER NOT NULL DEFAULT 0,
    in_source3           INTEGER NOT NULL DEFAULT 0,
    match_methods        TEXT,             -- e.g. 'email,phone'
    match_confidence     REAL,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_person_email ON person(primary_email)
    WHERE primary_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_person_phone ON person(primary_phone);
CREATE INDEX IF NOT EXISTS ix_person_city  ON person(city);

CREATE TABLE IF NOT EXISTS person_email (
    person_id INTEGER NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    email     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (person_id, email)
);

CREATE TABLE IF NOT EXISTS person_phone (
    person_id INTEGER NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    phone     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (person_id, phone)
);

CREATE TABLE IF NOT EXISTS person_name_alias (
    person_id INTEGER NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    PRIMARY KEY (person_id, name)
);

CREATE TABLE IF NOT EXISTS skill (
    skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS person_skill (
    person_id INTEGER NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    skill_id  INTEGER NOT NULL REFERENCES skill(skill_id)  ON DELETE CASCADE,
    source    TEXT    NOT NULL,
    PRIMARY KEY (person_id, skill_id, source)
);

CREATE TABLE IF NOT EXISTS source_record (
    source_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file      TEXT    NOT NULL,
    source_row       INTEGER NOT NULL,     -- 1-based line number in the raw CSV
    row_hash         TEXT    NOT NULL,     -- sha1 of the raw row -> idempotent re-runs
    raw_json         TEXT    NOT NULL,
    person_id        INTEGER REFERENCES person(person_id) ON DELETE SET NULL,
    status           TEXT    NOT NULL,     -- 'ingested' | 'rejected' | 'duplicate_row'
    note             TEXT,
    ingested_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_file, source_row)
);

CREATE TABLE IF NOT EXISTS data_issue (
    issue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file  TEXT NOT NULL,
    source_row   INTEGER,
    issue_code   TEXT NOT NULL,
    severity     TEXT NOT NULL,
    field        TEXT,
    raw_value    TEXT,
    action       TEXT NOT NULL,
    resolved_value TEXT,
    detected_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_issue_code ON data_issue(issue_code);

CREATE TABLE IF NOT EXISTS match_review (
    review_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    reason      TEXT NOT NULL,
    candidates  TEXT NOT NULL,   -- JSON
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Task 3: audio submissions land here and point at the same person table.
CREATE TABLE IF NOT EXISTS audio_submission (
    submission_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER REFERENCES person(person_id) ON DELETE SET NULL,
    submitted_name  TEXT NOT NULL,
    submitted_phone TEXT NOT NULL,          -- E.164
    matched_by      TEXT,                   -- 'phone' | 'new_person'
    file_name       TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    mime_type       TEXT,
    file_size_bytes INTEGER,
    sha256          TEXT,

    -- required extracted properties
    duration_sec    REAL,
    sample_rate_khz REAL,
    bitrate_kbps    REAL,
    loudness_db     REAL,                   -- integrated/mean RMS in dBFS

    -- bonus quality block
    peak_db         REAL,
    est_snr_db      REAL,
    clipping_pct    REAL,
    silence_pct     REAL,
    zcr_mean        REAL,
    spectral_flatness REAL,
    quality_label   TEXT,                   -- good | fair | poor
    quality_notes   TEXT,
    channels        INTEGER,
    codec           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_audio_person ON audio_submission(person_id);

-- Convenience view for the n8n flow and the app's list page.
CREATE VIEW IF NOT EXISTS v_person_full AS
SELECT p.person_id,
       p.full_name,
       p.primary_email,
       p.primary_phone,
       p.city,
       p.experience_years,
       p.current_ctc_inr,
       p.gig_status,
       p.gig_rate_inr_hour,
       p.cbnexus_verified,
       p.projects_completed,
       p.skill_category,
       p.source_count,
       p.in_source1, p.in_source2, p.in_source3,
       (SELECT group_concat(name, ', ') FROM (
            SELECT DISTINCT s.name
              FROM person_skill ps JOIN skill s ON s.skill_id = ps.skill_id
             WHERE ps.person_id = p.person_id
             ORDER BY s.name)) AS skills
FROM person p;
