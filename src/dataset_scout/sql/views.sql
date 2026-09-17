-- Views: saved queries that answer the questions a scientist actually asks.

-- Samples that belong to at least one in-scope study.
CREATE OR REPLACE VIEW v_in_scope_samples AS
SELECT DISTINCT sm.*
FROM samples AS sm
JOIN study_samples AS ss USING (gsm)
JOIN studies AS st USING (gse)
WHERE st.in_scope;

-- One line per study: what it is, how it scored and what it contains.
CREATE OR REPLACE VIEW v_study_overview AS
SELECT
    st.gse,
    st.title,
    st.data_type,
    st.organism,
    st.published,
    st.in_scope,
    st.exclusion_reason,
    st.fetch_error,
    q.n_samples,
    q.n_young,
    q.n_old,
    q.has_age_contrast,
    q.age_min_months,
    q.age_max_months,
    q.sexes,
    q.metadata_completeness,
    q.score,
    q.tier,
    q.flags,
    agg.sample_types,
    agg.tissues,
    agg.cell_types,
    st.pubmed_ids,
    st.geo_url
FROM studies AS st
LEFT JOIN study_quality AS q USING (gse)
LEFT JOIN (
    SELECT
        ss.gse,
        array_to_string(list_sort(list_distinct(list(sm.sample_type))), ', ') AS sample_types,
        array_to_string(list_sort(list_distinct(list(sm.tissue))), ', ') AS tissues,
        array_to_string(list_sort(list_distinct(list(sm.cell_type))), ', ') AS cell_types
    FROM study_samples AS ss
    JOIN samples AS sm USING (gsm)
    GROUP BY ss.gse
) AS agg USING (gse);

-- The ranked shortlist: in-scope studies at or above the usable threshold.
CREATE OR REPLACE VIEW v_usable_studies AS
SELECT *
FROM v_study_overview
WHERE in_scope
  AND score >= (SELECT CAST(value AS DOUBLE) FROM run_settings WHERE key = 'usable_threshold')
ORDER BY score DESC, n_samples DESC;

-- Studies that support a young-versus-old comparison.
CREATE OR REPLACE VIEW v_age_contrast_studies AS
SELECT gse, title, data_type, n_young, n_old, age_min_months, age_max_months, sexes, score, tier
FROM v_study_overview
WHERE in_scope AND has_age_contrast
ORDER BY score DESC;

-- Share of in-scope samples with each field filled in.
CREATE OR REPLACE VIEW v_field_completeness AS
SELECT 'age' AS field, AVG(CASE WHEN age_months IS NOT NULL OR age_group IS NOT NULL THEN 1 ELSE 0 END) AS share_filled FROM v_in_scope_samples
UNION ALL
SELECT 'sex', AVG(CASE WHEN sex IS NOT NULL THEN 1 ELSE 0 END) FROM v_in_scope_samples
UNION ALL
SELECT 'tissue or cell type', AVG(CASE WHEN tissue IS NOT NULL OR cell_type IS NOT NULL OR cell_line IS NOT NULL THEN 1 ELSE 0 END) FROM v_in_scope_samples
UNION ALL
SELECT 'strain or genotype', AVG(CASE WHEN strain IS NOT NULL OR genotype_raw IS NOT NULL THEN 1 ELSE 0 END) FROM v_in_scope_samples;

-- The "inbox" for curators: values the harmonizer could not interpret.
CREATE OR REPLACE VIEW v_needs_review AS
SELECT gsm, 'age' AS field, age_raw AS raw_value, age_rule AS reason
FROM v_in_scope_samples
WHERE age_rule IN ('unparsed', 'number_without_unit')
UNION ALL
SELECT gsm, 'tissue', tissue_raw, 'not in vocabulary'
FROM v_in_scope_samples
WHERE tissue_raw IS NOT NULL AND tissue IS NULL
UNION ALL
SELECT gsm, 'cell type', cell_type_raw, 'not in vocabulary'
FROM v_in_scope_samples
WHERE cell_type_raw IS NOT NULL AND cell_type IS NULL;
