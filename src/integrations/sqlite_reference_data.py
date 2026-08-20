"""SQLite storage for complete, versioned reference models."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from src.domain.cde import CDEInfo, CdeType, DataModelSummary, DataModelVersionInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import (
    ReferenceDataCorruptError,
    ReferenceDataUnavailableError,
    ReferenceModel,
    ReferenceModelNotFoundError,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE data_models (
    data_model_key TEXT NOT NULL,
    external_version_number TEXT NOT NULL,
    label TEXT NOT NULL,
    PRIMARY KEY (data_model_key, external_version_number)
);

CREATE TABLE cdes (
    data_model_key TEXT NOT NULL,
    external_version_number TEXT NOT NULL,
    cde_key TEXT NOT NULL,
    cde_id INTEGER,
    description TEXT,
    cde_type TEXT NOT NULL CHECK (cde_type IN ('pv', 'passthrough')),
    PRIMARY KEY (data_model_key, external_version_number, cde_key),
    FOREIGN KEY (data_model_key, external_version_number)
        REFERENCES data_models (data_model_key, external_version_number)
        ON DELETE RESTRICT
);

CREATE TABLE permissible_values (
    data_model_key TEXT NOT NULL,
    external_version_number TEXT NOT NULL,
    cde_key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (data_model_key, external_version_number, cde_key, value),
    FOREIGN KEY (data_model_key, external_version_number, cde_key)
        REFERENCES cdes (data_model_key, external_version_number, cde_key)
        ON DELETE RESTRICT
);
"""


class SqliteReferenceImportConflictError(RuntimeError):
    """The import tried to change an already published external version."""


class SqliteReferenceDataRepository:
    """Convert SQLite rows into the reference-data domain at the boundary."""

    def __init__(self, path: Path) -> None:
        self._path = path
        _require_supported_database(path)

    def list_models(self) -> tuple[DataModelSummary, ...]:
        try:
            with closing(_connect(self._path)) as connection:
                rows = connection.execute(
                    """
                    SELECT data_model_key, external_version_number, label
                    FROM data_models
                    ORDER BY data_model_key, external_version_number
                    """
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ReferenceDataUnavailableError("Reference data is unavailable") from exc

        labels: dict[str, str] = {}
        versions: dict[str, list[DataModelVersionInfo]] = {}
        for row in rows:
            key = _required_text(row, "data_model_key")
            label = _required_text(row, "label")
            existing_label = labels.setdefault(key, label)
            if existing_label != label:
                raise ReferenceDataCorruptError(f"Reference model labels disagree: {key}")
            versions.setdefault(key, []).append(
                DataModelVersionInfo(_required_text(row, "external_version_number"))
            )
        return tuple(
            DataModelSummary(key, labels[key], model_versions)
            for key, model_versions in versions.items()
        )

    def load_model(self, version: DataModelVersionReference) -> ReferenceModel:
        try:
            with closing(_connect(self._path)) as connection:
                return _load_model(connection, version)
        except ReferenceModelNotFoundError:
            raise
        except ReferenceDataCorruptError:
            raise
        except sqlite3.DatabaseError as exc:
            raise ReferenceDataUnavailableError("Reference data is unavailable") from exc


class SqliteReferenceDataImporter:
    """Publish complete external versions in one SQLite transaction."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _initialize_database(path)

    def import_models(self, models: Sequence[ReferenceModel]) -> None:
        identities = [model.version for model in models]
        if len(identities) != len(set(identities)):
            raise SqliteReferenceImportConflictError("Reference import contains duplicate model versions")
        try:
            with closing(_connect(self._path)) as connection, connection:
                for model in models:
                    self._import_model(connection, model)
        except SqliteReferenceImportConflictError:
            raise
        except sqlite3.DatabaseError as exc:
            raise ReferenceDataUnavailableError("Reference data could not be imported") from exc

    def _import_model(self, connection: sqlite3.Connection, model: ReferenceModel) -> None:
        existing = _load_optional_model(connection, model.version)
        if existing is not None:
            if existing != model:
                raise SqliteReferenceImportConflictError(
                    "Reference model version is already published with different content: "
                    f"{model.version.data_model_key}/{model.version.external_version_number}"
                )
            return
        label_row = connection.execute(
            "SELECT label FROM data_models WHERE data_model_key = ? LIMIT 1",
            (model.version.data_model_key,),
        ).fetchone()
        if label_row is not None and _required_text(label_row, "label") != model.label:
            raise SqliteReferenceImportConflictError(
                f"Reference model label is already published: {model.version.data_model_key}"
            )
        identity = (model.version.data_model_key, model.version.external_version_number)
        connection.execute(
            "INSERT INTO data_models (data_model_key, external_version_number, label) VALUES (?, ?, ?)",
            (*identity, model.label),
        )
        for cde in model.catalog:
            connection.execute(
                """
                INSERT INTO cdes (
                    data_model_key, external_version_number, cde_key, cde_id, description, cde_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (*identity, cde.cde_key, cde.cde_id, cde.description, cde.cde_type.value),
            )
            connection.executemany(
                """
                INSERT INTO permissible_values (
                    data_model_key, external_version_number, cde_key, value
                ) VALUES (?, ?, ?, ?)
                """,
                [(*identity, cde.cde_key, value) for value in sorted(model.pvs.get(cde.cde_key) or ())],
            )


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _initialize_database(path: Path) -> None:
    try:
        with closing(_connect(path)) as connection, connection:
            version = _schema_version(connection)
            if version == 0:
                connection.executescript(_SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version != SCHEMA_VERSION:
                raise ReferenceDataCorruptError(
                    f"Reference database schema is unsupported: {version}"
                )
    except ReferenceDataCorruptError:
        raise
    except sqlite3.DatabaseError as exc:
        raise ReferenceDataUnavailableError("Reference database could not be initialized") from exc


def _require_supported_database(path: Path) -> None:
    if not path.is_file():
        raise ReferenceDataUnavailableError(f"Reference database does not exist: {path}")
    try:
        with closing(_connect(path)) as connection:
            version = _schema_version(connection)
    except sqlite3.DatabaseError as exc:
        raise ReferenceDataUnavailableError("Reference database could not be opened") from exc
    if version != SCHEMA_VERSION:
        raise ReferenceDataCorruptError(f"Reference database schema is unsupported: {version}")


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None or not isinstance(row[0], int):
        raise ReferenceDataCorruptError("Reference database schema version is invalid")
    return row[0]


def _load_optional_model(
    connection: sqlite3.Connection,
    version: DataModelVersionReference,
) -> ReferenceModel | None:
    try:
        return _load_model(connection, version)
    except ReferenceModelNotFoundError:
        return None


def _load_model(
    connection: sqlite3.Connection,
    version: DataModelVersionReference,
) -> ReferenceModel:
    identity = (version.data_model_key, version.external_version_number)
    model_row = connection.execute(
        """
        SELECT label
        FROM data_models
        WHERE data_model_key = ? AND external_version_number = ?
        """,
        identity,
    ).fetchone()
    if model_row is None:
        raise ReferenceModelNotFoundError(
            f"Reference model is not published: {version.data_model_key}/{version.external_version_number}"
        )
    cde_rows = connection.execute(
        """
        SELECT cde_key, cde_id, description, cde_type
        FROM cdes
        WHERE data_model_key = ? AND external_version_number = ?
        ORDER BY cde_key
        """,
        identity,
    ).fetchall()
    value_rows = connection.execute(
        """
        SELECT cde_key, value
        FROM permissible_values
        WHERE data_model_key = ? AND external_version_number = ?
        ORDER BY cde_key, value
        """,
        identity,
    ).fetchall()
    cdes = [_cde_from_row(row) for row in cde_rows]
    values: dict[str, set[str]] = {cde.cde_key: set() for cde in cdes}
    for row in value_rows:
        cde_key = _required_text(row, "cde_key")
        if cde_key not in values:
            raise ReferenceDataCorruptError(f"Reference value has no CDE: {cde_key}")
        values[cde_key].add(_text(row, "value"))
    try:
        return ReferenceModel(
            version=version,
            label=_required_text(model_row, "label"),
            catalog=CdeCatalog.from_cdes(cdes),
            pvs=CdePvCatalog.from_mapping({key: frozenset(items) for key, items in values.items()}),
        )
    except ValueError as exc:
        raise ReferenceDataCorruptError("Stored reference model is invalid") from exc


def _cde_from_row(row: sqlite3.Row) -> CDEInfo:
    raw_id = row["cde_id"]
    if raw_id is not None and (isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id < 0):
        raise ReferenceDataCorruptError("Stored reference CDE id is invalid")
    description = row["description"]
    if description is not None and not isinstance(description, str):
        raise ReferenceDataCorruptError("Stored reference CDE description is invalid")
    try:
        cde_type = CdeType(_required_text(row, "cde_type"))
    except ValueError as exc:
        raise ReferenceDataCorruptError("Stored reference CDE type is invalid") from exc
    return CDEInfo(raw_id, _required_text(row, "cde_key"), description, cde_type)


def _required_text(row: sqlite3.Row, field: str) -> str:
    value = _text(row, field)
    if not value:
        raise ReferenceDataCorruptError(f"Stored reference field is invalid: {field}")
    return value


def _text(row: sqlite3.Row, field: str) -> str:
    value = row[field]
    if not isinstance(value, str):
        raise ReferenceDataCorruptError(f"Stored reference field is invalid: {field}")
    return value


__all__ = [
    "SCHEMA_VERSION",
    "SqliteReferenceDataImporter",
    "SqliteReferenceDataRepository",
    "SqliteReferenceImportConflictError",
]
