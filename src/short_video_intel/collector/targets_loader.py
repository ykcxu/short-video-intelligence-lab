from __future__ import annotations

import ast
import csv
import json
from pathlib import Path
from typing import Any

REQUIRED_TARGET_FIELDS = (
    "homepage_url",
    "source_name",
    "category_lv1",
    "category_lv2",
    "tags_json",
)


def load_targets_from_path(
    path: str | Path,
    *,
    input_format: str = "auto",
) -> list[dict[str, Any]]:
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"target file not found: {source_path}")

    normalized_format = input_format.lower()
    if normalized_format == "auto":
        suffix = source_path.suffix.lower()
        if suffix == ".csv":
            normalized_format = "csv"
        elif suffix == ".json":
            normalized_format = "json"
        else:
            raise ValueError(f"unsupported target file format: {source_path.suffix or '<none>'}")

    if normalized_format == "csv":
        return load_targets_from_csv(source_path)
    if normalized_format == "json":
        return load_targets_from_json(source_path)

    raise ValueError(f"unsupported input_format: {input_format}")


def load_targets_from_csv(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _validate_headers(reader.fieldnames or [], source_path)
        return [
            _normalize_target_row(row, row_index=index + 1, source_path=source_path)
            for index, row in enumerate(reader)
        ]


def load_targets_from_json(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for index, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            rows.append(_normalize_target_row(json.loads(stripped), row_index=index, source_path=source_path))
        return rows

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        for key in ("targets", "items", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                records = value
                break
        else:
            records = [payload]
    else:
        raise ValueError(f"unsupported JSON payload type: {type(payload).__name__}")

    return [
        _normalize_target_row(record, row_index=index + 1, source_path=source_path)
        for index, record in enumerate(records)
    ]


def _validate_headers(headers: list[str], source_path: Path) -> None:
    missing = [field for field in REQUIRED_TARGET_FIELDS if field not in headers]
    if missing:
        raise ValueError(
            f"{source_path} is missing required columns: {', '.join(missing)}"
        )


def _normalize_target_row(
    record: Any,
    *,
    row_index: int,
    source_path: Path,
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(
            f"{source_path} row {row_index} must be an object, got {type(record).__name__}"
        )

    normalized = dict(record)
    for field in REQUIRED_TARGET_FIELDS:
        if field not in normalized:
            raise ValueError(f"{source_path} row {row_index} is missing field: {field}")

    homepage_url = _normalize_text(normalized.get("homepage_url"))
    source_name = _normalize_text(normalized.get("source_name"))
    if not homepage_url:
        raise ValueError(f"{source_path} row {row_index} has empty homepage_url")
    if not source_name:
        raise ValueError(f"{source_path} row {row_index} has empty source_name")

    normalized["homepage_url"] = homepage_url
    normalized["source_name"] = source_name
    normalized["category_lv1"] = _normalize_text(normalized.get("category_lv1"))
    normalized["category_lv2"] = _normalize_text(normalized.get("category_lv2"))
    normalized["tags_json"] = _normalize_tags_json(normalized.get("tags_json"), source_path, row_index)
    normalized["row_index"] = row_index
    normalized["source_path"] = str(source_path)
    return normalized


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_tags_json(value: Any, source_path: Path, row_index: int) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "[]"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                if "," in text:
                    parsed = [part.strip().strip("\"'") for part in text.strip("[]").split(",") if part.strip()]
                else:
                    parsed = [text]
            else:
                if not isinstance(parsed, (list, tuple, set)):
                    parsed = [parsed]
        return json.dumps(parsed, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)
