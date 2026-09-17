# Real GEO records used as test fixtures

These files are real SOFT records, kept small so the tests run offline.

| File | Origin | Why it is here |
|---|---|---|
| `GSM2795971.txt` | GEO record GSM2795971, as stored in the test suite of [GEOparse](https://github.com/guma44/GEOparse) (BSD-3-Clause) | Windows line endings (CRLF), a non-ASCII character (β), no characteristics |
| `GSM11805_trimmed.txt` | GEO record GSM11805, as stored in the test suite of [GEOquery](https://github.com/seandavi/GEOquery) (Artistic-2.0) | An old-style record: metadata in the description and a data table. The table was cut to 5 rows and the submitter's contact lines were removed |
| `soft_ex_family.txt` | The example family file from the GEO SOFT documentation, as stored in the GEOparse test suite | Two-colour arrays: channel 2 describes a reference sample that must not be read as the sample itself |

GEO records are public data from NCBI.
