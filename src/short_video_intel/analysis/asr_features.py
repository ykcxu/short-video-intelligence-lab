from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


ASR_FEATURES_VERSION = "asr-features.v1"
OPENING_HOOK_PATTERNS = ("你知道", "千万别", "很多人", "很多家长", "为什么", "到底", "一个方法", "三招", "别再")
PUNCTUATION_PATTERN = re.compile(r"[\s，。！？、,.!?；;：:\"'“”‘’（）()《》<>\[\]{}-]+")
DEFAULT_SAMPLE_RATE = "16000"
SECONDS_PER_MINUTE = 60.0


def analyze_asr_features_file(
    *,
    workspace: Path,
    artifact: Path,
    output: Path | None = None,
    features_dir: Path | None = None,
    model_size: str = "small",
    language: str = "zh",
) -> dict[str, Any]:
    """从本地视频 manifest 抽取 ASR 特征，并写入可被多模态流程合并的 JSON。"""
    resolved_artifact = artifact if artifact.is_absolute() else workspace / artifact
    items = _extract_items(json.loads(resolved_artifact.read_text(encoding="utf-8")))
    results = analyze_asr_features_items(items=items, model_size=model_size, language=language)
    result = _build_file_result(artifact=resolved_artifact, results=results)
    _write_feature_files(workspace=workspace, features_dir=features_dir, results=results)
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(resolved_output)
    return result


def analyze_asr_features_items(
    *,
    items: Iterable[Mapping[str, Any]],
    model_size: str = "small",
    language: str = "zh",
    model_factory: Callable[[str], Any] | None = None,
) -> list[dict[str, Any]]:
    """批量处理视频条目；依赖缺失时明确失败，禁止伪造转写文本。"""
    loaded_factory = model_factory or _load_whisper_model_factory()
    if loaded_factory is None:
        return [_build_missing_dependency_result(item) for item in items]
    model = loaded_factory(model_size)
    return [_analyze_item(item=item, model=model, language=language) for item in items]


def _analyze_item(*, item: Mapping[str, Any], model: Any, language: str) -> dict[str, Any]:
    """处理单个视频，保持失败粒度在视频级，方便下游继续合并其它视频。"""
    base = _build_base_item(item)
    video_path = _resolve_video_path(item)
    if not video_path:
        return {**base, "ok": False, "error": "missing_video_path", "asr_speech": _empty_feature()}
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "audio.wav"
            _extract_wav(video_path=Path(video_path), wav_path=wav_path)
            segments, info = model.transcribe(str(wav_path), language=language)
            feature = _build_speech_feature(segments=list(segments), info=info)
            return {**base, "ok": True, "asr_speech": feature}
    except Exception as exc:  # noqa: BLE001
        # ASR 是离线抽取链路，失败必须显式暴露，避免下游把空文本当真实转写。
        return {**base, "ok": False, "error": type(exc).__name__, "message": str(exc), "asr_speech": _empty_feature()}


def _build_speech_feature(*, segments: list[Any], info: Any) -> dict[str, Any]:
    """将 faster-whisper 分段转成稳定的 asr_speech 字段。"""
    normalized = [_segment_to_dict(segment) for segment in segments]
    transcript = "".join(item["text"] for item in normalized).strip()
    duration = _duration_sec(info=info, segments=normalized)
    speech_time = sum(max(0.0, item["end"] - item["start"]) for item in normalized)
    return {
        "version": ASR_FEATURES_VERSION,
        "transcript": transcript,
        "speech_rate_cpm": _speech_rate_cpm(text=transcript, duration_sec=duration),
        "pause_ratio": _pause_ratio(duration_sec=duration, speech_time_sec=speech_time),
        "opening_hook_score": _opening_hook_score(transcript),
        "segments_count": len(normalized),
        "duration_sec": round(duration, 3),
    }


def _segment_to_dict(segment: Any) -> dict[str, Any]:
    if isinstance(segment, Mapping):
        return {"start": _num(segment.get("start")), "end": _num(segment.get("end")), "text": _text(segment.get("text"))}
    return {"start": _num(getattr(segment, "start", 0.0)), "end": _num(getattr(segment, "end", 0.0)), "text": _text(getattr(segment, "text", ""))}


def _duration_sec(*, info: Any, segments: list[Mapping[str, Any]]) -> float:
    duration = _num(getattr(info, "duration", 0.0) if not isinstance(info, Mapping) else info.get("duration"))
    if duration > 0:
        return duration
    return max((_num(segment.get("end")) for segment in segments), default=0.0)


def _speech_rate_cpm(*, text: str, duration_sec: float) -> float:
    if duration_sec <= 0:
        return 0.0
    chars = len(PUNCTUATION_PATTERN.sub("", text))
    return round(chars / duration_sec * SECONDS_PER_MINUTE, 2)


def _pause_ratio(*, duration_sec: float, speech_time_sec: float) -> float:
    if duration_sec <= 0:
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - speech_time_sec / duration_sec)), 3)


def _opening_hook_score(transcript: str) -> float:
    opening = transcript[:80]
    hits = sum(1 for pattern in OPENING_HOOK_PATTERNS if pattern in opening)
    return round(min(1.0, hits / 2.0), 3)


def _extract_wav(*, video_path: Path, wav_path: Path) -> None:
    """用 ffmpeg 抽取单声道 wav，统一 Whisper 输入格式。"""
    command = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", DEFAULT_SAMPLE_RATE, str(wav_path)]
    # Windows 默认 GBK 可能无法解码 ffmpeg 输出，统一用二进制捕获，失败时再安全解码。
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        message = _decode_process_output(completed.stderr or completed.stdout)
        raise RuntimeError(message or f"ffmpeg failed with code {completed.returncode}")


def _decode_process_output(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip()


def _load_whisper_model_factory() -> Callable[[str], Any] | None:
    """可选导入 faster-whisper，避免未安装时模块导入失败。"""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    return WhisperModel


def _extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("local_video_inputs"), list):
        source_items = payload["local_video_inputs"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        source_items = payload["items"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        result = _as_dict(payload.get("result"))
        source_items = result.get("items") or result.get("results") or []
    elif isinstance(payload, list):
        source_items = payload
    else:
        raise ValueError("asr artifact missing local_video_inputs/items list")
    return [item for item in source_items if isinstance(item, Mapping)]


def _build_file_result(*, artifact: Path, results: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "ok": all(bool(item.get("ok")) for item in results),
        "analysis_type": "asr_features",
        "artifact_path": str(artifact),
        "result": {"version": ASR_FEATURES_VERSION, "total": len(results), "summary": _summarize(results), "results": results},
        "generated_at": _now_iso(),
    }


def _build_base_item(item: Mapping[str, Any]) -> dict[str, Any]:
    video_url = _text(item.get("video_url") or item.get("url"))
    return {"video_id": _text(item.get("video_id")) or _extract_video_id(video_url), "video_url": video_url, "source_name": _text(item.get("source_name"))}


def _build_missing_dependency_result(item: Mapping[str, Any]) -> dict[str, Any]:
    base = _build_base_item(item)
    return {**base, "ok": False, "error": "missing_dependency", "dependency": "faster-whisper", "asr_speech": _empty_feature()}


def _empty_feature() -> dict[str, Any]:
    return {"version": ASR_FEATURES_VERSION, "transcript": "", "speech_rate_cpm": 0.0, "pause_ratio": 0.0, "opening_hook_score": 0.0, "segments_count": 0, "duration_sec": 0.0}


def _resolve_video_path(item: Mapping[str, Any]) -> str:
    return _text(item.get("download_output_path") or item.get("video_path"))


def _write_feature_files(*, workspace: Path, features_dir: Path | None, results: list[Mapping[str, Any]]) -> None:
    """按 video_id 合并写入多模态特征目录，只覆盖 asr_speech 字段。"""
    root = _resolve_features_dir(workspace=workspace, features_dir=features_dir)
    root.mkdir(parents=True, exist_ok=True)
    for result in results:
        if not result.get("ok"):
            continue
        video_id = _text(result.get("video_id"))
        if not video_id:
            continue
        path = root / f"{video_id}.json"
        existing = _load_existing_feature(path)
        existing["asr_speech"] = _as_dict(result.get("asr_speech"))
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_existing_feature(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _summarize(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    ok_count = sum(1 for item in results if item.get("ok"))
    missing_count = sum(1 for item in results if item.get("error") == "missing_dependency")
    return {"ok_count": ok_count, "failed_count": len(results) - ok_count, "missing_dependency_count": missing_count}


def _resolve_features_dir(*, workspace: Path, features_dir: Path | None) -> Path:
    if features_dir is not None:
        return features_dir if features_dir.is_absolute() else workspace / features_dir
    return workspace / "artifacts" / "multimodal" / "features"


def _resolve_output_path(*, workspace: Path, output: Path | None) -> Path:
    if output is not None:
        return output if output.is_absolute() else workspace / output
    return workspace / "artifacts" / "analysis" / f"asr_features_{_now_token()}.json"


def _extract_video_id(video_url: str) -> str:
    matched = re.search(r"/video/(\d+)", video_url)
    return matched.group(1) if matched else ""


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
