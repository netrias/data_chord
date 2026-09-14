# Workflow test data

Use [classic-workflow.csv](tests/e2e/fixtures/classic-workflow.csv) for a small,
repeatable workflow check. It has 20 synthetic rows and 11 columns. It contains
no source records from a private dataset. Keep `case_id` in the file so that
you can identify each test row in the output.

## Run the no-cost check

From the repository root, run:

```bash
npm run test:e2e -- tests/e2e/real-workflow.e2e.spec.mjs --grep 'classic dataset' --workers=1
uv run pytest tests/test_classic_input.py
```

The browser test starts the real local application. It uses the existing fixed
local reference catalog and harmonization cache. It does not make paid model
calls or need AWS credentials. This is not the packaged demo profile.

Select **Synthetic E2E Data Model**, version **11.0.4** (model key `gc`). Map `diagnosis` to
`primary_diagnosis`. Leave every other column, including `case_id`, unmapped.
The test saves and checks every column choice before it runs harmonization.

The fixed local catalog approves `Diabetes` and changes `breast ca` to
`Breast Cancer`. All other present diagnosis values have no match. A live
catalog or model can give different recommendations. For a live check, confirm
each mapping and check that the selected values are in the selected catalog.
Do not expect the fixed local model output from a live model.

## Cases and expected results

| Case | Input | Fixed local result |
| --- | --- | --- |
| C01 | Approved diagnosis | `Diabetes`, unchanged |
| C02–C03 | Repeated `breast ca` | Both rows become `Breast Cancer`; one unique term |
| C04 | `adamantinoma` | No match; original remains; warning returns after edit and restore |
| C05–C09 | Empty, spaces, tab, U+0085, U+00A0 | Missing; no review card; original cell remains in export |
| C10 | U+FEFF | Present, not missing; no match |
| C11 | Meaningful text with surrounding spaces | No match; spaces remain |
| C12 | Long text with spaces | No match; full active value wraps and opens in value editing |
| C13 | Long text without spaces | No match; full active value wraps and opens in value editing |
| C14 | Unicode text | No match; all characters remain |
| C15 | Quoted comma | One cell; punctuation remains |
| C16 | Quoted quotation marks | One cell; quotation marks remain |
| C17 | Quoted newline | One cell; newline remains |
| C18–C19 | Literal `NaN` and `NULL` text | Present, not missing; no match |
| C20 | Empty diagnosis and unmapped multiline site | No diagnosis card; site passes through exactly |

The input has 14 present diagnosis rows and 13 unique present diagnosis values.
Stage 3 checks 13 unique values and retains all 20 rows. One unique value is
already approved, one unique value is harmonized, and 11 unique values have no
match. Stage 4 has 12 column cards by default and 13 with unchanged values
shown. Row view shows 14 cards when unchanged values are shown. Missing cells
have no cards in either view.

The browser test checks long and multiline active values in both review views
at 1044 × 921 pixels. Clicking the card body opens **value editing**. It also
saves an approved edit, reloads it, restores the original, reloads again, and
checks the original warning. The complete downloaded CSV must match the input
in row order, column order, and every cell, except for C02 and C03.

## Structurally abnormal files

These separate files record the current reader behavior. They are not inputs
for the normal workflow and do not define a desired acceptance policy.

| File | Current read and analysis result |
| --- | --- |
| [classic-header-only.csv](tests/e2e/fixtures/classic-header-only.csv) | Two columns and zero data rows |
| [classic-uneven-rows.csv](tests/e2e/fixtures/classic-uneven-rows.csv) | Short row is padded; extra field adds an unnamed column |
| [classic-unclosed-quote.csv](tests/e2e/fixtures/classic-unclosed-quote.csv) | Accepted; the next physical line is joined into the open quoted cell |

The unclosed quote can hide a record boundary. A decision to reject this input
needs a separate parser-policy change. The tests check the full current parsed
shape and analysis so that a policy change is visible.

## Optional full baseline and recording

Use an approved private baseline for a larger live check. Do not copy private
records into the repository. A local operator can keep its path, file hash,
model choice, and mapping instructions in the ignored
`.artifacts/workflow-proof/README.md` note. This private file is not a CI input.

For a recording, select the workflow tab first. Confirm that no file picker or
other window covers it. Make a two-second sample and inspect it before you run
the full workflow. Record the page area without audio. Keep recordings and
login state private. Use a player with 1×, 2×, and 4× speed controls. A video
shows the actions; the exact output assertions provide the data proof.
