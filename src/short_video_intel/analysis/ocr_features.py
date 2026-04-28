from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


OCR_FEATURES_VERSION = "ocr-features.v1"
SUPPORTED_BACKENDS = {"easyocr", "paddleocr", "auto"}
KEYWORD_PATTERN = re.compile(r"知识|方法|步骤|避坑|账号|视频|流量|转化|案例|技巧|重点|复盘|问题|解决|原因")


class OcrReader(Protocol):
    def read_text(self, image_path: Path) -> list[str]: ...


def analyze_ocr_features_file(
    *,
    workspace: Path,
    artifact: Path,
    output: Path | None = None,
    features_dir: Path | None = None,
    backend: str = "auto",
    language: str = "ch_sim",
) -> dict[str, Any]:
    resolved_artifact = artifact if artifact.is_absolute() else workspace / artifact
    items = _extract_items(_load_json(resolved_artifact))
    reader_result = _create_reader(backend=backend, language=language)
    if not reader_result["ok"]:
        return _write_missing_result(workspace, output, resolved_artifact, reader_result)
    reader = reader_result["reader"]
    results = [_build_result_item(workspace, item, reader) for item in items]
    feature_root = _resolve_features_dir(workspace=workspace, features_dir=features_dir)
    for item in results:
        _merge_feature_file(feature_root=feature_root, item=item)
    result = _build_file_result(resolved_artifact, results, reader_result["backend"], feature_root)
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    _write_json(resolved_output, result)
    result["output_path"] = str(resolved_output)
    return result


def analyze_ocr_features_item(*, workspace: Path, item: Mapping[str, Any], reader: OcrReader) -> dict[str, Any]:
    frames = _collect_frame_paths(workspace=workspace, item=item)
    frame_texts = [_read_frame_text(reader=reader, frame=frame) for frame in frames]
    feature = _build_ocr_feature(frame_texts)
    return {
        "video_id": _text(item.get("video_id")),
        "video_url": _text(item.get("video_url")),
        "source_name": _text(item.get("source_name")),
        "ocr_subtitle": feature,
    }


def _create_reader(*, backend: str, language: str) -> dict[str, Any]:
    selected = backend.lower().strip()
    if selected not in SUPPORTED_BACKENDS:
        raise ValueError("backend must be easyocr, paddleocr or auto")
    candidates = ["easyocr", "paddleocr"] if selected == "auto" else [selected]
    errors: list[str] = []
    for name in candidates:
        created = _try_create_reader(backend=name, language=language)
        if created["ok"]:
            return created
        errors.append(created.get("error", name))
    return {"ok": False, "error_code": "missing_dependency", "backend": selected, "errors": errors}


def _try_create_reader(*, backend: str, language: str) -> dict[str, Any]:
    try:
        if backend == "easyocr":
            return {"ok": True, "backend": backend, "reader": _EasyOcrReader(language)}
        return {"ok": True, "backend": backend, "reader": _PaddleOcrReader(language)}
    except ImportError as exc:
        return {"ok": False, "error_code": "missing_dependency", "backend": backend, "error": str(exc)}


class _EasyOcrReader:
    def __init__(self, language: str) -> None:
        import easyocr  # type: ignore[import-not-found]

        # EasyOCR 初始化较重，因此只创建一次 reader 供同批视频复用。
        self._reader = easyocr.Reader([language], gpu=False)

    def read_text(self, image_path: Path) -> list[str]:
        return _normalize_ocr_lines(self._reader.readtext(str(image_path), detail=0, paragraph=True))


class _PaddleOcrReader:
    def __init__(self, language: str) -> None:
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]

        # PaddleOCR 的语言参数由调用方注入，避免在代码里硬绑定中文场景。
        self._reader = PaddleOCR(lang="ch" if language == "ch_sim" else language, use_angle_cls=True, show_log=False)

    def read_text(self, image_path: Path) -> list[str]:
        if hasattr(self._reader, "ocr"):
            return _normalize_ocr_lines(self._reader.ocr(str(image_path), cls=True))
        return _normalize_ocr_lines(self._reader.predict(str(image_path)))


def _build_result_item(workspace: Path, item: Mapping[str, Any], reader: OcrReader) -> dict[str, Any]:
    return analyze_ocr_features_item(workspace=workspace, item=item, reader=reader)


def _build_ocr_feature(frame_texts: list[list[str]]) -> dict[str, Any]:
    lines = _dedupe([line for lines in frame_texts for line in lines])
    text = "\n".join(lines)
    frames_count = len(frame_texts)
    text_frames = sum(1 for lines in frame_texts if lines)
    return {
        "text": text,
        "coverage_ratio": _ratio(text_frames, frames_count),
        "readability_score": _readability_score(text),
        "keyword_density": _keyword_density(text),
        "subtitle_consistency_score": _subtitle_consistency_score(frame_texts),
        "frames_count": frames_count,
    }


def _collect_frame_paths(*, workspace: Path, item: Mapping[str, Any]) -> list[Path]:
    frames = item.get("frame_samples") if isinstance(item.get("frame_samples"), list) else []
    paths: list[Path] = []
    for frame in frames:
        path = _frame_path(workspace=workspace, frame=frame)
        if path is not None:
            paths.append(path)
    return paths


def _frame_path(*, workspace: Path, frame: Any) -> Path | None:
    if not isinstance(frame, Mapping) or not frame.get("ok"):
        return None
    path = Path(_text(frame.get("output_path"))).expanduser()
    resolved = path if path.is_absolute() else workspace / path
    if resolved.suffix.lower() not in {".jpg", ".jpeg"}:
        return None
    return resolved if resolved.exists() and resolved.is_file() else None


def _read_frame_text(*, reader: OcrReader, frame: Path) -> list[str]:
    # 单帧 OCR 失败时暴露为空帧特征，不编造字幕内容。
    return _normalize_ocr_lines(reader.read_text(frame))


def _normalize_ocr_lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [_clean_line(value)] if _clean_line(value) else []
    if isinstance(value, Mapping):
        return _normalize_ocr_lines(value.get("text") or value.get("rec_text"))
    if isinstance(value, (list, tuple)):
        return _normalize_sequence(value)
    return []


def _normalize_sequence(values: list[Any] | tuple[Any, ...]) -> list[str]:
    lines: list[str] = []
    for value in values:
        if isinstance(value, str):
            lines.extend(_normalize_ocr_lines(value))
        elif isinstance(value, Mapping):
            lines.extend(_normalize_ocr_lines(value.get("text") or value.get("rec_text") or list(value.values())))
        elif isinstance(value, (list, tuple)):
            lines.extend(_normalize_ocr_tuple(value))
    return lines


def _normalize_ocr_tuple(value: list[Any] | tuple[Any, ...]) -> list[str]:
    if len(value) >= 2 and isinstance(value[1], str):
        return _normalize_ocr_lines(value[1])
    if len(value) >= 2 and isinstance(value[1], (list, tuple)):
        return _normalize_ocr_lines(value[1][0] if value[1] and isinstance(value[1][0], str) else list(value))
    return [line for child in value for line in _normalize_ocr_lines(child)]


def _readability_score(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    readable = sum(1 for char in compact if "\u4e00" <= char <= "\u9fff" or char.isalnum())
    length_score = min(1.0, len(compact) / 80)
    return round((readable / len(compact)) * 0.7 + length_score * 0.3, 4)


def _keyword_density(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    matches = KEYWORD_PATTERN.findall(compact)
    return round(min(1.0, len(matches) * 6 / max(1, len(compact))), 4)


def _subtitle_consistency_score(frame_texts: list[list[str]]) -> float:
    texts = ["".join(lines) for lines in frame_texts if lines]
    if not texts:
        return 0.0
    if len(texts) == 1:
        return 1.0
    scores = [_char_overlap(left, right) for left, right in zip(texts, texts[1:])]
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _char_overlap(left: str, right: str) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        source_items = payload["items"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        source_items = payload["result"].get("items") or payload["result"].get("results") or []
    elif isinstance(payload, list):
        source_items = payload
    else:
        raise ValueError("ocr artifact missing items/results list")
    return [item for item in source_items if isinstance(item, Mapping)]


def _merge_feature_file(*, feature_root: Path, item: Mapping[str, Any]) -> None:
    video_id = _text(item.get("video_id"))
    if not video_id:
        return
    path = feature_root / f"{video_id}.json"
    payload = _load_json(path) if path.exists() else {}
    merged = dict(payload) if isinstance(payload, Mapping) else {}
    merged["ocr_subtitle"] = dict(item["ocr_subtitle"])
    _write_json(path, merged)


def _build_file_result(artifact: Path, results: list[Mapping[str, Any]], backend: str, feature_root: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "analysis_type": "ocr_features",
        "artifact_path": str(artifact),
        "features_dir": str(feature_root),
        "backend": backend,
        "result": {"version": OCR_FEATURES_VERSION, "total": len(results), "results": results},
        "generated_at": _now_iso(),
    }


def _write_missing_result(workspace: Path, output: Path | None, artifact: Path, reader_result: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "ok": False,
        "analysis_type": "ocr_features",
        "artifact_path": str(artifact),
        "error_code": "missing_dependency",
        "backend": _text(reader_result.get("backend")),
        "errors": list(reader_result.get("errors") or [reader_result.get("error")]),
        "generated_at": _now_iso(),
    }
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    _write_json(resolved_output, result)
    result["output_path"] = str(resolved_output)
    return result


def _resolve_features_dir(*, workspace: Path, features_dir: Path | None) -> Path:
    root = features_dir or workspace / "artifacts" / "multimodal" / "features"
    resolved = root if root.is_absolute() else workspace / root
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _resolve_output_path(*, workspace: Path, output: Path | None) -> Path:
    if output is not None:
        return output if output.is_absolute() else workspace / output
    return workspace / "artifacts" / "analysis" / f"ocr_features_{_now_token()}.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in (_clean_line(value) for value in values) if item))

def _clean_line(value: Any) -> str:
    repaired = _repair_mojibake(str(value or ""))
    return re.sub(r"\s+", " ", repaired).strip()

def _repair_mojibake(text: str) -> str:
    if not text or not _looks_like_latin1_mojibake(text):
        return text
    try:
        repaired = text.encode("latin1").decode("gbk")
    except UnicodeError:
        return text
    return repaired if _chinese_ratio(repaired) > _chinese_ratio(text) else text

def _looks_like_latin1_mojibake(text: str) -> bool:
    return any("\u00c0" <= char <= "\u00ff" for char in text)

def _chinese_ratio(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 0.0
    chinese_count = sum(1 for char in compact if "\u4e00" <= char <= "\u9fff")
    return chinese_count / len(compact)

def _ratio(part: int, total: int) -> float:
    return round(part / total, 4) if total > 0 else 0.0

def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _now_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
