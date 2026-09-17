import pytest

from dataset_scout.harmonize.fields import classify_key
from dataset_scout.harmonize.sex import find_sex_in_text, parse_sex
from dataset_scout.harmonize.vocabulary import Vocabulary


@pytest.mark.parametrize(
    ("raw", "sex"),
    [("M", "male"), ("Female", "female"), ("F", "female"), ("males", "male"), ("mixed", "mixed"),
     ("pooled males and females", "mixed"), ("unknown", None), ("hermaphrodite", None)],
)
def test_parse_sex(raw, sex):
    assert parse_sex(raw).sex == sex


def test_pooled_sex_is_flagged():
    assert "pooled" in parse_sex("pooled females").flags


def test_female_is_not_read_as_male_in_titles():
    assert find_sex_in_text("female_rep1", "title").sex == "female"
    assert find_sex_in_text("sample 3", "title") is None


@pytest.mark.parametrize(
    ("key", "field"),
    [("Age (months)", "age"), ("age group", "age"), ("gender", "sex"), ("Sex", "sex"),
     ("cell line", "cell_line"), ("tissue/cell type", "cell_type"), ("brain region", "tissue"),
     ("strain background", "strain"), ("genotype/variation", "genotype"), ("treated with", "treatment"),
     ("passage", None), ("batch", None), (None, None)],
)
def test_classify_key(key, field):
    assert classify_key(key) == field


@pytest.fixture(scope="module")
def vocab():
    return Vocabulary.load()


def test_longest_synonym_wins(vocab):
    assert vocab.tissues.find("medial prefrontal cortex").canonical == "prefrontal cortex"
    assert vocab.tissues.find("kidney cortex").canonical == "kidney"


def test_whole_word_matching(vocab):
    assert vocab.tissues.find("Hippocampus").ontology == "UBERON:0002421"
    assert vocab.cell_types.find("microgliosis score") is None


def test_ambiguous_abbreviation_is_not_mapped(vocab):
    assert vocab.tissues.find("HPC") is None


def test_cell_lines_carry_their_lineage(vocab):
    term = vocab.cell_lines.find("BV2 cells")
    assert term.canonical == "BV-2" and term.extra["lineage"] == "microglia"


def test_control_detection(vocab):
    assert vocab.is_control_genotype("Wild-type littermate") is True
    assert vocab.is_control_genotype("APP/PS1") is False
    assert vocab.is_control_treatment("vehicle") is True
    assert vocab.is_control_treatment("LPS 100 ng/ml") is False
    assert vocab.is_control_treatment("") is None


def test_stage_alone_is_not_an_age_field():
    # In GEO, a key named "stage" held treatment stages such as "BLZ945_5day"; "5day" is not an age.
    assert classify_key("stage") is None
    assert classify_key("developmental stage") == "age"


def test_knockout_status_is_a_genotype_field():
    assert classify_key("knockout status") == "genotype"


def test_strain_name_or_wild_type_allele_counts_as_control(vocab):
    assert vocab.is_control_genotype("C57BL/6J") is True
    assert vocab.is_control_genotype("C57Bl/6") is True
    assert vocab.is_control_genotype("Trem2+/+") is True
    assert vocab.is_control_genotype("C57BL/6-ApoeKO") is False
    assert vocab.is_control_genotype("Trem2+/+; 5xFAD") is False


@pytest.mark.parametrize(
    ("text", "canonical"),
    [("White Matter", "white matter"), ("gray matter", "grey matter"), ("p12  Thalamus", "thalamus"),
     ("Brain midbrain", "midbrain"), ("choroid plexus", "choroid plexus"), ("Olfactory bulb", "olfactory bulb"),
     ("Optic Nerve", "optic nerve"), ("Substantia nigra", "substantia nigra"), ("SVZ", "subventricular zone"),
     ("Hippocamus", "hippocampus"), ("spinal cord white matter", "spinal cord"),
     ("cerebellum and olfactory bulb-removed brains", "brain"), ("hypothalamus", "hypothalamus")],
)
def test_brain_regions_seen_in_geo(vocab, text, canonical):
    assert vocab.tissues.find(text).canonical == canonical


def test_neutrophils_are_a_cell_type(vocab):
    assert vocab.cell_types.find("Neutrophils").canonical == "neutrophil"


def test_more_ways_of_writing_no_treatment(vocab):
    for value in ("None/naïve", "Naïve", "CTR", "CON", "Not-treated"):
        assert vocab.is_control_treatment(value) is True, value
    assert vocab.is_control_treatment("Vehicle/LPS") is False
