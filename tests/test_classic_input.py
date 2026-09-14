"""Read and analyze the classic input boundary without service calls.

Malformed-file tests characterize the current permissive reader. Their outputs
are not an approved rejection policy. A stricter policy must update these tests
deliberately, especially because an unclosed quote can consume later records.
"""

from pathlib import Path

from netrias_client import TabularColumn, TabularDataset, TabularFormat, read_tabular

from src.domain.column_profile import ColumnProfile, DistinctValue
from src.domain.manifest import ConfidenceBucket
from src.stage_1_upload.schemas import ColumnSummary
from src.stage_1_upload.services import analyze_columns

FIXTURES = Path(__file__).parent / "e2e" / "fixtures"


def test_classic_header_only_file_has_columns_but_no_data() -> None:
    # Given: a valid file with exactly two headers and no records.
    source = FIXTURES / "classic-header-only.csv"
    assert source.read_text(encoding="utf-8") == "record_id,diagnosis\n"

    # When: the application's reader and Stage 1 analyzer process the file.
    dataset = read_tabular(source)
    analysis = analyze_columns(source)

    # Then: both columns remain identifiable, but neither contains a value.
    assert dataset == TabularDataset(
        columns=[TabularColumn("col_0000", 0, "record_id"), TabularColumn("col_0001", 1, "diagnosis")],
        rows=[],
        source_format=TabularFormat.CSV,
    )
    assert analysis == (
        0,
        [
            ColumnSummary(
                column_name="record_id",
                column_key="col_0000",
                source_index=0,
                header="record_id",
                inferred_type="unknown",
                has_non_empty_values=False,
                confidence_bucket=ConfidenceBucket.LOW,
                confidence_score=0.0,
            ),
            ColumnSummary(
                column_name="diagnosis",
                column_key="col_0001",
                source_index=1,
                header="diagnosis",
                inferred_type="unknown",
                has_non_empty_values=False,
                confidence_bucket=ConfidenceBucket.LOW,
                confidence_score=0.0,
            ),
        ],
        {
            "col_0000": ColumnProfile("col_0000", 0, (), 0),
            "col_0001": ColumnProfile("col_0001", 0, (), 0),
        },
    )


def test_classic_uneven_rows_characterize_padding_and_unnamed_column() -> None:
    # Given: a two-header file with one short record and one extra-field record.
    source = FIXTURES / "classic-uneven-rows.csv"
    assert source.read_text(encoding="utf-8") == ("record_id,diagnosis\nSHORT\nEXTRA,No match,unexpected field\n")

    # When: the application's reader and Stage 1 analyzer process the file.
    dataset = read_tabular(source)
    analysis = analyze_columns(source)

    # Then: current permissive parsing pads short rows and adds an unnamed column.
    # This characterizes acceptance; it does not require future parsers to accept it.
    assert dataset == TabularDataset(
        columns=[
            TabularColumn("col_0000", 0, "record_id"),
            TabularColumn("col_0001", 1, "diagnosis"),
            TabularColumn("col_0002", 2, ""),
        ],
        rows=[["SHORT", "", ""], ["EXTRA", "No match", "unexpected field"]],
        source_format=TabularFormat.CSV,
    )
    assert analysis == (
        2,
        [
            ColumnSummary(
                column_name="record_id",
                column_key="col_0000",
                source_index=0,
                header="record_id",
                inferred_type="text",
                has_non_empty_values=True,
                confidence_bucket=ConfidenceBucket.HIGH,
                confidence_score=1.0,
            ),
            ColumnSummary(
                column_name="diagnosis",
                column_key="col_0001",
                source_index=1,
                header="diagnosis",
                inferred_type="text",
                has_non_empty_values=True,
                confidence_bucket=ConfidenceBucket.MEDIUM,
                confidence_score=0.5,
            ),
            ColumnSummary(
                column_name="",
                column_key="col_0002",
                source_index=2,
                header="",
                inferred_type="text",
                has_non_empty_values=True,
                confidence_bucket=ConfidenceBucket.MEDIUM,
                confidence_score=0.5,
            ),
        ],
        {
            "col_0000": ColumnProfile("col_0000", 2, (DistinctValue("SHORT", 1), DistinctValue("EXTRA", 1)), 0),
            "col_0001": ColumnProfile("col_0001", 2, (DistinctValue("No match", 1),), 1),
            "col_0002": ColumnProfile("col_0002", 2, (DistinctValue("unexpected field", 1),), 1),
        },
    )


def test_classic_unclosed_quote_characterizes_later_record_consumption() -> None:
    # Given: an unclosed quoted diagnosis before a second physical data line.
    source = FIXTURES / "classic-unclosed-quote.csv"
    assert source.read_text(encoding="utf-8") == (
        'record_id,diagnosis\nUNCLOSED,"open diagnosis\nNEXT,Ductal Carcinoma NOS\n'
    )

    # When: the application's reader and Stage 1 analyzer process the file.
    dataset = read_tabular(source)
    analysis = analyze_columns(source)

    # Then: current permissive parsing consumes the later record into one field.
    # This is a safety-relevant characterization, not a desired input contract.
    combined_value = "open diagnosis\nNEXT,Ductal Carcinoma NOS\n"
    assert dataset == TabularDataset(
        columns=[TabularColumn("col_0000", 0, "record_id"), TabularColumn("col_0001", 1, "diagnosis")],
        rows=[["UNCLOSED", combined_value]],
        source_format=TabularFormat.CSV,
    )
    assert analysis == (
        1,
        [
            ColumnSummary(
                column_name="record_id",
                column_key="col_0000",
                source_index=0,
                header="record_id",
                inferred_type="text",
                has_non_empty_values=True,
                confidence_bucket=ConfidenceBucket.HIGH,
                confidence_score=1.0,
            ),
            ColumnSummary(
                column_name="diagnosis",
                column_key="col_0001",
                source_index=1,
                header="diagnosis",
                inferred_type="text",
                has_non_empty_values=True,
                confidence_bucket=ConfidenceBucket.HIGH,
                confidence_score=1.0,
            ),
        ],
        {
            "col_0000": ColumnProfile("col_0000", 1, (DistinctValue("UNCLOSED", 1),), 0),
            "col_0001": ColumnProfile("col_0001", 1, (DistinctValue(combined_value, 1),), 0),
        },
    )
