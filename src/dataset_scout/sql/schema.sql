-- Dataset Scout database schema (DuckDB).
--
-- Data model in one sentence: a study (GEO series, GSE) has many samples
-- (GSM), and a sample can belong to more than one study (SuperSeries and
-- SubSeries share samples), so studies and samples are linked through a
-- separate table, study_samples.
--
--   studies 1 ──< study_samples >── 1 samples 1 ──< sample_characteristics
--      │
--      └── 1:1 study_quality
--
-- runs, run_settings, data_checks and validation_results describe the build
-- itself (lineage and quality control), not the biology.

CREATE TABLE runs (
    run_id            VARCHAR PRIMARY KEY,
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    mode              VARCHAR,
    config_name       VARCHAR,
    config_sha256     VARCHAR,
    search_term       VARCHAR,
    snapshot_dir      VARCHAR,
    tool_version      VARCHAR,
    n_found           INTEGER,
    n_retrieved       INTEGER,
    is_demo           BOOLEAN
);

CREATE TABLE run_settings (
    key               VARCHAR PRIMARY KEY,
    value             VARCHAR
);

CREATE TABLE studies (
    gse                   VARCHAR PRIMARY KEY,
    uid                   VARCHAR,
    title                 VARCHAR,
    summary               VARCHAR,
    organism              VARCHAR,
    gds_type              VARCHAR,
    data_type             VARCHAR,
    data_type_flags       VARCHAR,
    platform              VARCHAR,
    n_samples_reported    INTEGER,
    published             DATE,
    pubmed_ids            VARCHAR,
    bioproject            VARCHAR,
    supplementary         VARCHAR,
    is_superseries        BOOLEAN,
    samples_fetched       BOOLEAN,
    in_scope              BOOLEAN NOT NULL,
    exclusion_reason      VARCHAR,
    fetch_error           VARCHAR,
    geo_url               VARCHAR
);

CREATE TABLE samples (
    gsm                   VARCHAR PRIMARY KEY,
    title                 VARCHAR,
    source_name           VARCHAR,
    organism              VARCHAR,
    molecule              VARCHAR,
    library_strategy      VARCHAR,
    library_source        VARCHAR,
    is_expression         BOOLEAN,
    age_raw               VARCHAR,
    age_value             DOUBLE,
    age_unit              VARCHAR,
    age_months            DOUBLE,
    age_stage             VARCHAR,
    age_group             VARCHAR,
    age_rule              VARCHAR,
    age_source            VARCHAR,
    age_flags             VARCHAR,
    sex_raw               VARCHAR,
    sex                   VARCHAR,
    sex_rule              VARCHAR,
    sex_source            VARCHAR,
    tissue_raw            VARCHAR,
    tissue                VARCHAR,
    tissue_ontology       VARCHAR,
    cell_type_raw         VARCHAR,
    cell_type             VARCHAR,
    cell_type_ontology    VARCHAR,
    cell_line             VARCHAR,
    sample_type           VARCHAR,
    strain_raw            VARCHAR,
    strain                VARCHAR,
    genotype_raw          VARCHAR,
    genotype_is_control   BOOLEAN,
    treatment_raw         VARCHAR,
    treatment_is_control  BOOLEAN,
    treatment_source      VARCHAR,
    is_pooled             BOOLEAN,
    is_cell_level         BOOLEAN,
    series_ids            VARCHAR
);

CREATE TABLE study_samples (
    gse   VARCHAR NOT NULL REFERENCES studies (gse),
    gsm   VARCHAR NOT NULL REFERENCES samples (gsm),
    PRIMARY KEY (gse, gsm)
);

CREATE TABLE sample_characteristics (
    gsm            VARCHAR NOT NULL REFERENCES samples (gsm),
    channel        INTEGER NOT NULL,
    position       INTEGER NOT NULL,
    key_raw        VARCHAR,
    value_raw      VARCHAR,
    mapped_field   VARCHAR,
    PRIMARY KEY (gsm, channel, position)
);

CREATE TABLE study_quality (
    gse                      VARCHAR PRIMARY KEY REFERENCES studies (gse),
    n_samples                INTEGER,
    pct_age                  DOUBLE,
    pct_sex                  DOUBLE,
    pct_tissue_or_cell       DOUBLE,
    pct_strain_or_genotype   DOUBLE,
    metadata_completeness    DOUBLE,
    n_young                  INTEGER,
    n_old                    INTEGER,
    has_age_contrast         BOOLEAN,
    age_min_months           DOUBLE,
    age_max_months           DOUBLE,
    sexes                    VARCHAR,
    design_fit               DOUBLE,
    target_fit               DOUBLE,
    data_type_fit            DOUBLE,
    traceability             DOUBLE,
    score                    DOUBLE,
    tier                     VARCHAR,
    flags                    VARCHAR
);

CREATE TABLE fetch_errors (
    gse      VARCHAR,
    step     VARCHAR,
    message  VARCHAR
);

-- Automated checks run on every build (the "validate" step of the pipeline).
CREATE TABLE data_checks (
    check_name  VARCHAR PRIMARY KEY,
    status      VARCHAR NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    detail      VARCHAR
);

-- Agreement between the harmonizer and a blind manual review, per field.
CREATE TABLE validation_results (
    field        VARCHAR PRIMARY KEY,
    n_reviewed   INTEGER,
    n_correct    INTEGER,
    n_wrong      INTEGER,
    n_missed     INTEGER,
    n_invented   INTEGER,
    accuracy     DOUBLE,
    ci_low       DOUBLE,
    ci_high      DOUBLE
);
