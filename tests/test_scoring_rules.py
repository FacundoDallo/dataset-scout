"""The rules that decide whether a study really answers the question.

Each test here pins one judgement call: what counts as a wild-type mouse, what
counts as a baseline sample, what counts as a replicate, and what counts as the
material the question asks about. They are written from real GEO values.
"""

import pandas as pd

from dataset_scout.config import AgeThresholds
from dataset_scout.harmonize.assay import classify_data_type
from dataset_scout.harmonize.samples import harmonize_sample
from dataset_scout.harmonize.vocabulary import Vocabulary
from dataset_scout.quality import score_study
from dataset_scout.soft import parse_soft_samples

MOUSE = {"Mus musculus": AgeThresholds(1.5, 6, 18)}
SEQ = "Expression profiling by high throughput sequencing"


def harmonize_one(characteristics: str, title: str = "microglia_1") -> dict:
    soft = (
        f"^SAMPLE = GSM1\n!Sample_title = {title}\n!Sample_source_name_ch1 = FACS-sorted microglia\n"
        f"!Sample_organism_ch1 = Mus musculus\n!Sample_molecule_ch1 = total RNA\n"
        f"!Sample_library_strategy = RNA-Seq\n{characteristics}"
    )
    return harmonize_sample(parse_soft_samples(soft)[0], Vocabulary.load(), MOUSE)[0]


# --- what counts as a wild-type mouse ------------------------------------------------------


def test_engineered_alleles_are_not_wild_type():
    """Real genotypes from the demo run that used to be counted as wild type."""
    vocab = Vocabulary.load()
    engineered = [
        "Cx3cr1CreERT,Daxx wt/wt",
        "Cx3cr1-cre/ERT(+/-); NuTRAP(flox/wt)",
        "GFAP-Cre+/- Rpl22HA/wt",
        "p38-fl/fl, Aldh1l1-wt/wt",
        "Rosa26TurboID/wt/Aldh1l1CreERT2/wt",
        "Ctcf wild type; Camk2a-Cre positive; Ribotag positive",
        "Trem2 KO",
    ]
    for value in engineered:
        assert vocab.is_control_genotype(value) is False, value
    unmodified = ["wild type", "WT", "C57BL/6J", "Trem2+/+", "NTg", "non-transgenic wild type", "ATG7_WT"]
    for value in unmodified:
        assert vocab.is_control_genotype(value) is True, value


# --- what counts as a baseline sample ------------------------------------------------------


def test_intervention_written_in_another_field_is_not_baseline():
    """GSE270687 wrote its stroke and infection groups outside any treatment field."""
    infected = harmonize_one("!Sample_characteristics_ch1 = infection: day 4 post-MHV infection\n")
    assert infected["treatment_is_control"] is False
    assert infected["treatment_source"] == "other field"
    assert "infection" in infected["treatment_raw"]

    stroke = harmonize_one("!Sample_characteristics_ch1 = disease state: ischemic stroke\n")
    assert stroke["treatment_is_control"] is False


def test_the_control_of_an_intervention_stays_baseline():
    for value in ("mock infection", "uninfected", "sham surgery"):
        row = harmonize_one(f"!Sample_characteristics_ch1 = infection status: {value}\n")
        assert row["treatment_is_control"] is not False, value


# --- what counts as a replicate ------------------------------------------------------------


def test_per_cell_quality_fields_mark_one_cell_not_one_animal():
    cell = harmonize_one("!Sample_characteristics_ch1 = nGene: 2453\n!Sample_characteristics_ch1 = nUMI: 8100\n")
    assert cell["is_cell_level"] is True
    animal = harmonize_one("!Sample_characteristics_ch1 = age: 22 months\n")
    assert animal["is_cell_level"] is False


# --- how a data type is claimed ------------------------------------------------------------


def test_single_cell_claimed_only_by_the_summary_is_flagged():
    terms = frozenset({"single cell", "single-cell"})
    from_text = classify_data_type(SEQ, ["We compared our bulk data with single-cell atlases"], terms)
    assert from_text.data_type == "single-cell RNA-seq"
    assert "single_cell_from_text" in from_text.flags

    from_library = classify_data_type(SEQ, [""], terms, library_sources={"single cell transcriptomic"})
    assert from_library.data_type == "single-cell RNA-seq"
    assert "single_cell_from_text" not in from_library.flags


# --- scoring -------------------------------------------------------------------------------

SAMPLE_DEFAULTS: dict = {
    "is_expression": True,
    "organism": "Mus musculus",
    "age_group": None,
    "age_months": None,
    "age_flags": None,
    "age_rule": None,
    "age_source": "field",
    "sex": "male",
    "tissue": None,
    "cell_type": None,
    "cell_line": None,
    "sample_type": "isolated cells",
    "strain": "C57BL/6J",
    "genotype_raw": "WT",
    "genotype_is_control": True,
    "treatment_is_control": True,
    "is_pooled": False,
    "is_cell_level": False,
}
STUDY_DEFAULTS: dict = {
    "gse": "GSE1",
    "data_type": "bulk RNA-seq",
    "data_type_flags": "",
    "pubmed_ids": ["30000001"],
    "bioproject": "PRJNA1",
    "supplementary": "TXT",
    "organism": "Mus musculus",
}


def samples(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame([{**SAMPLE_DEFAULTS, **row} for row in rows])


def study(**changes) -> pd.Series:
    return pd.Series({**STUDY_DEFAULTS, **changes})


def age_design(n: int = 3, **extra) -> pd.DataFrame:
    return samples(*[{"age_group": group, **extra} for group in ["young"] * n + ["old"] * n])


def test_samples_of_an_organism_out_of_scope_are_not_counted(config):
    """GSE99074 counted its human donors towards a mouse question."""
    group = samples(
        *[{"age_group": "young", "cell_type": "microglia"} for _ in range(3)],
        *[{"age_group": "old", "cell_type": "microglia"} for _ in range(3)],
        *[{"age_group": "old", "cell_type": "microglia", "organism": "Homo sapiens"} for _ in range(4)],
    )
    row = score_study(study(), group, config)
    assert (row["n_samples"], row["n_young"], row["n_old"]) == (6, 3, 3)
    assert "other_organism_samples" in row["flags"]


def test_single_cells_are_not_counted_as_animals(config):
    cells = age_design(5, cell_type="microglia", is_cell_level=True)
    row = score_study(study(data_type_flags="single_cell"), cells, config)
    assert (row["n_young"], row["n_old"]) == (0, 0)
    assert row["has_age_contrast"] is False
    assert row["design_fit"] == 0.5 and "groups_are_cells" in row["flags"]


def test_isolated_target_cells_rank_above_whole_tissue(config):
    isolated = score_study(study(), age_design(cell_type="microglia"), config)
    cultured = score_study(study(), age_design(cell_type="microglia", sample_type="primary culture"), config)
    tissue = score_study(study(), age_design(tissue="hippocampus", sample_type="tissue"), config)
    other_cells = score_study(study(), age_design(cell_type="astrocyte", tissue="hippocampus"), config)

    assert (isolated["target_fit"], cultured["target_fit"]) == (1.0, 0.5)
    assert (tissue["target_fit"], other_cells["target_fit"]) == (0.25, 0.0)
    assert isolated["score"] > cultured["score"] > tissue["score"] > other_cells["score"]
    assert "off_target_material" in tissue["flags"] and "off_target_material" in other_cells["flags"]
    assert "off_target_material" not in isolated["flags"]


def test_single_cell_data_of_the_target_tissue_counts_more_than_bulk_tissue(config):
    bulk = score_study(study(), age_design(tissue="cerebral cortex", sample_type="tissue"), config)
    single = score_study(
        study(data_type="single-cell RNA-seq", data_type_flags="single_cell"),
        age_design(tissue="cerebral cortex", sample_type="tissue"),
        config,
    )
    assert bulk["target_fit"] == 0.25 and single["target_fit"] == 0.5
