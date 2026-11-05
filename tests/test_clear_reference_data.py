from __future__ import annotations

from typing import Any

import pytest

from scripts import clear_reference_data

TABLE = "data-chord-staging-reference-data"
ACCOUNT = "123456789012"
REGION = "us-east-2"


class _Batch:
    def __init__(self, table: _Table) -> None:
        self.table = table

    def __enter__(self) -> _Batch:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def delete_item(self, *, Key: dict[str, str]) -> None:  # noqa: N803 - boto3 API
        self.table.items.remove(Key)
        self.table.deleted.append(Key)


class _Table:
    def __init__(self) -> None:
        self.items = [{"pk": f"model-{index}", "sk": "metadata"} for index in range(5)]
        self.deleted: list[dict[str, str]] = []

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["ConsistentRead"] is True
        assert kwargs["ProjectionExpression"] == "#pk, #sk"
        last = kwargs.get("ExclusiveStartKey")
        following = [item for item in self.items if last is None or item["pk"] > last["pk"]]
        page = following[:2]
        response: dict[str, Any] = {"Items": page}
        if len(following) > 2:
            response["LastEvaluatedKey"] = page[-1]
        return response

    def batch_writer(self) -> _Batch:
        return _Batch(self)


class _Client:
    def __init__(self, service: str, *, table_name: str = TABLE) -> None:
        self.service = service
        self.table_name = table_name

    def get_caller_identity(self) -> dict[str, str]:
        assert self.service == "sts"
        return {"Account": ACCOUNT}

    def describe_table(self, *, TableName: str) -> dict[str, Any]:  # noqa: N803 - boto3 API
        assert self.service == "dynamodb"
        assert TableName == TABLE
        return {"Table": {
            "TableName": self.table_name,
            "TableArn": f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{self.table_name}",
            "KeySchema": [{"AttributeName": "pk"}, {"AttributeName": "sk"}],
        }}


class _Resource:
    def __init__(self, table: _Table) -> None:
        self.table = table

    def Table(self, name: str) -> _Table:  # noqa: N802 - boto3 API
        assert name == TABLE
        return self.table


class _Session:
    def __init__(self, table: _Table, *, table_name: str = TABLE) -> None:
        self.table = table
        self.table_name = table_name

    def client(self, service: str) -> _Client:
        return _Client(service, table_name=self.table_name)

    def resource(self, service: str) -> _Resource:
        assert service == "dynamodb"
        return _Resource(self.table)


def _use_session(monkeypatch: pytest.MonkeyPatch, *, table_name: str = TABLE) -> _Table:
    table = _Table()

    def make_session(*, profile_name: str | None, region_name: str) -> _Session:
        assert profile_name is None
        assert region_name == REGION
        return _Session(table, table_name=table_name)

    monkeypatch.setattr(clear_reference_data.boto3, "Session", make_session)
    return table


def test_preview_counts_all_pages_without_deleting(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _use_session(monkeypatch)
    count = clear_reference_data.clear_reference_data(
        table_name=TABLE, region=REGION, expected_account_id=ACCOUNT
    )
    assert count == 5
    assert len(table.items) == 5
    assert table.deleted == []


def test_execute_deletes_all_pages_and_verifies_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _use_session(monkeypatch)
    count = clear_reference_data.clear_reference_data(
        table_name=TABLE, region=REGION, expected_account_id=ACCOUNT,
        execute=True, confirm_table=TABLE,
    )
    assert count == 5
    assert table.items == []
    assert len(table.deleted) == 5


@pytest.mark.parametrize("wrong_table", [
    "data-model-store-dynamodb-poc", "data-chord-staging-harmonization-cache",
    "data-chord-staging-cde-recommendation-cache", "other-reference-data",
])
def test_rejects_other_tables_before_aws_access(
    monkeypatch: pytest.MonkeyPatch, wrong_table: str
) -> None:
    monkeypatch.setattr(clear_reference_data.boto3, "Session", lambda **_kwargs: pytest.fail("AWS opened"))
    with pytest.raises(ValueError, match="--table"):
        clear_reference_data.clear_reference_data(
            table_name=wrong_table, region=REGION, expected_account_id=ACCOUNT,
            execute=True, confirm_table=wrong_table,
        )


def test_requires_matching_confirmation_before_aws_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clear_reference_data.boto3, "Session", lambda **_kwargs: pytest.fail("AWS opened"))
    with pytest.raises(ValueError, match="--confirm-table"):
        clear_reference_data.clear_reference_data(
            table_name=TABLE, region=REGION, expected_account_id=ACCOUNT, execute=True,
        )


def test_rejects_wrong_account_and_table_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _use_session(monkeypatch, table_name="data-chord-qa-reference-data")
    with pytest.raises(ValueError, match="AWS account"):
        clear_reference_data.clear_reference_data(
            table_name=TABLE, region=REGION, expected_account_id="999999999999",
            execute=True, confirm_table=TABLE,
        )
    with pytest.raises(ValueError, match="AWS table ARN"):
        clear_reference_data.clear_reference_data(
            table_name=TABLE, region=REGION, expected_account_id=ACCOUNT,
            execute=True, confirm_table=TABLE,
        )
    assert table.deleted == []
