from dataset_scout.config import AgeThresholds
from dataset_scout.harmonize.samples import SAMPLE_COLUMNS, harmonize_sample
from dataset_scout.harmonize.vocabulary import Vocabulary
from dataset_scout.soft import parse_soft_samples, split_characteristic

MOUSE = {"Mus musculus": AgeThresholds(1.5, 6, 18)}


def test_real_record_with_windows_line_endings(real_text):
    samples = parse_soft_samples(real_text("GSM2795971.txt"))
    assert [s.accession for s in samples] == ["GSM2795971"]
    assert samples[0].get("title") == "SMAD2/3, Hs578T, TGF\u03b2 16h_ChIP"
    assert samples[0].characteristics == []


def test_data_table_is_skipped(real_text):
    sample = parse_soft_samples(real_text("GSM11805_trimmed.txt"))[0]
    assert sample.get("series_id") == "GSE781"
    assert "AFFX-BioB-5_at" not in str(sample.attributes)
    assert len(sample.get_all("description")) == 7


def test_two_colour_reference_channel_is_ignored(real_text):
    samples = parse_soft_samples(real_text("soft_ex_family.txt"))
    assert len(samples) == 3
    row, chars = harmonize_sample(samples[0], Vocabulary.load(), MOUSE)
    # Channel 2 is the reference: pooled E17.5 embryos, C57BL/6. It must not leak in.
    assert row["age_raw"] is None
    assert row["strain"] == "129"
    assert {c["channel"] for c in chars} == {1, 2}


def test_split_characteristic():
    assert split_characteristic("age: 3 months") == ("age", "3 months")
    assert split_characteristic("http://example.org/x") == (None, "http://example.org/x")
    assert split_characteristic("no key here") == (None, "no key here")


SOFT = """^SAMPLE = GSM1
!Sample_title = MG_old_rep1
!Sample_source_name_ch1 = FACS-sorted CD11b+ microglia
!Sample_organism_ch1 = Mus musculus
!Sample_characteristics_ch1 = strain: C57BL/6J
!Sample_characteristics_ch1 = age: 22 mo
!Sample_characteristics_ch1 = Sex: F
!Sample_characteristics_ch1 = genotype: WT
!Sample_molecule_ch1 = total RNA
!Sample_library_strategy = RNA-Seq
!Sample_library_source = transcriptomic
^SAMPLE = GSM2
!Sample_title = aged_female_2
!Sample_source_name_ch1 = hippocampus
!Sample_organism_ch1 = Mus musculus
!Sample_characteristics_ch1 = age: N/A
!Sample_molecule_ch1 = total RNA
^SAMPLE = GSM3
!Sample_title = young_3
!Sample_source_name_ch1 = hippocampus
!Sample_organism_ch1 = Mus musculus
!Sample_characteristics_ch1 = age: about a year
!Sample_molecule_ch1 = genomic DNA
!Sample_library_strategy = ChIP-Seq
"""


def rows():
    vocab = Vocabulary.load()
    return [harmonize_sample(s, vocab, MOUSE)[0] for s in parse_soft_samples(SOFT)]


def test_row_matches_the_schema_contract():
    assert tuple(rows()[0]) == SAMPLE_COLUMNS


def test_explicit_fields_are_harmonized_with_their_rules():
    row = rows()[0]
    assert (row["age_months"], row["age_group"], row["age_source"]) == (22.0, "old", "field")
    assert (row["sex"], row["sex_rule"]) == ("female", "explicit_token")
    assert (row["cell_type"], row["sample_type"], row["strain"]) == ("microglia", "isolated cells", "C57BL/6J")
    assert row["genotype_is_control"] is True and row["is_expression"] is True


def test_placeholder_age_allows_an_inferred_value():
    row = rows()[1]
    assert row["age_group"] == "old" and row["age_source"] == "fallback"
    assert row["sex"] == "female" and row["sex_source"] == "fallback"
    assert row["tissue"] == "hippocampus" and row["sample_type"] == "tissue"


def test_written_but_unreadable_age_is_not_replaced_by_a_guess():
    row = rows()[2]
    assert row["age_rule"] == "unparsed" and row["age_group"] is None
    assert row["is_expression"] is False


def harmonize_one(soft_text):
    sample = parse_soft_samples(soft_text)[0]
    return harmonize_sample(sample, Vocabulary.load(), MOUSE)[0]


def test_unambiguous_age_in_another_field_is_used():
    row = harmonize_one(
        "^SAMPLE = GSM10\n!Sample_title = cortex_1\n!Sample_organism_ch1 = Mus musculus\n"
        "!Sample_characteristics_ch1 = time: 6 months old mice\n"
    )
    assert (row["age_months"], row["age_group"], row["age_source"]) == (6.0, "young", "fallback")
    assert "inferred" in row["age_flags"]
    # Only "N units old" counts there: a time after injury or a bare word is not an age.
    row = harmonize_one(
        "^SAMPLE = GSM11\n!Sample_title = cortex_2\n!Sample_organism_ch1 = Mus musculus\n"
        "!Sample_characteristics_ch1 = time: 7dpi\n!Sample_characteristics_ch1 = timepoint: young\n"
    )
    assert row["age_rule"] is None


def test_age_unit_written_in_its_own_field():
    row = harmonize_one(
        "^SAMPLE = GSM12\n!Sample_title = microglia_1\n!Sample_organism_ch1 = Mus musculus\n"
        "!Sample_characteristics_ch1 = age: 16\n!Sample_characteristics_ch1 = age_unit: months\n"
    )
    assert (row["age_months"], row["age_rule"]) == (16.0, "unit_from_field_name")


def test_isolation_written_in_another_field_sets_the_sample_type():
    sorted_cells = harmonize_one(
        "^SAMPLE = GSM13\n!Sample_title = mic_1\n!Sample_source_name_ch1 = brain\n"
        "!Sample_organism_ch1 = Mus musculus\n!Sample_characteristics_ch1 = selection marker: CD11b+ CD45int\n"
    )
    assert sorted_cells["sample_type"] == "isolated cells"
    cultured = harmonize_one(
        "^SAMPLE = GSM14\n!Sample_title = MG_1\n!Sample_organism_ch1 = Mus musculus\n"
        "!Sample_characteristics_ch1 = cell type: microglial cells\n"
        "!Sample_characteristics_ch1 = time: 5 days in vitro\n"
    )
    assert cultured["sample_type"] == "primary culture"
