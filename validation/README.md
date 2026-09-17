# Blind manual review

The automated checks confirm that a build is consistent. This folder holds the evidence that the harmonized values are **correct**.

## Protocol

1. `scout review-sheet config/microglia_aging.yaml` draws 100 in-scope expression samples at random (fixed seed), spread across as many studies as possible, and writes `validation/<run name>/review_sheet.xlsx`.
2. The sheet shows only the raw metadata. The program's answers are hidden, so the reviewer is not anchored by them.
3. The reviewer fills the yellow columns: age group, age in months, sex, tissue, cell type, sample type. `unknown` means the metadata does not say; an empty cell means not reviewed.
4. `scout validate config/microglia_aging.yaml` compares both and writes:
   - `results.json`: agreement per field with a 95% Wilson interval, plus the SHA-256 of the sheet that produced it;
   - `disagreements.csv`: every mismatch with the raw evidence, for error analysis.
5. Every later `scout run` recomputes the agreement with the current code, so a fix is measured against the same reviewed samples.

## Outcomes

| Outcome | Meaning | Usual fix |
|---|---|---|
| correct | Same value, or both agree the metadata does not say | |
| wrong | Both gave a value and they differ | Parser rule |
| missed | The reviewer found a value the program did not | Synonym or new rule |
| invented | The program gave a value the reviewer could not find | Make a rule stricter |

The sheet is never overwritten once it contains reviewed values.
