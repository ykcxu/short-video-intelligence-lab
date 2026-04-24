from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def prepare_local_video_analysis_inputs(
    *,
    workspace: Path,
    artifacts_dir: Path,
    artifact: Path | None = None,
    output: Path | None = None,
    frames_per_video: int = 3,
) -> dict[str, Any]:
    resolved_artifact = _resolve_downloader_artifact(workspace=workspace, artifacts_dir=artifacts_dir, artifact=artifact)
    payload = _load_json(resolved_artifact)
    results = _extract_download_results(payload)

    manifest_items: list[dict[str, Any]] = []
    warnings: list[str] = []
    skipped_count = 0

    for raw_item in results:
        item = dict(raw_item) if isinstance(raw_item, Mapping) else {}
        output_path = Path(str(item.get("output_path") or "")).expanduser()
        if not _looks_like_video_download(item, output_path):
            skipped_count += 1
            continue
        if not output_path.exists() or not output_path.is_file():
            warnings.append(f"missing downloaded file: {output_path}")
            skipped_count += 1
            continue

        probe = _ffprobe_video(output_path)
        if not probe.get("ok"):
            warnings.append(f"ffprobe failed: {output_path.name}")
            skipped_count += 1
            continue
        if not _probe_looks_like_real_video(probe):
            warnings.append(f"non-video or still-image media skipped: {output_path.name}")
            skipped_count += 1
            continue

        video_id = _safe_text(item.get("video_id")) or output_path.stem
        sample_dir = artifacts_dir / "analysis-inputs" / "frames" / video_id
        frame_samples = _extract_sample_frames(
            video_path=output_path,
            probe=probe,
            sample_dir=sample_dir,
            frames_per_video=frames_per_video,
        )
        frame_stats = _extract_frame_feature_summary(frame_samples)
        subtitle_hints = _extract_subtitle_hint_summary(frame_samples)
        content_features = _build_content_feature_summary(probe=probe, frame_stats=frame_stats)

        manifest_items.append(
            {
                "video_id": video_id,
                "video_url": _safe_text(item.get("video_url")),
                "source_name": _safe_text(item.get("source_name")),
                "homepage_url": _safe_text(item.get("homepage_url")),
                "download_result_artifact": str(resolved_artifact),
                "download_output_path": str(output_path),
                "download_artifact_path": _safe_text(item.get("artifact_path")),
                "downloader": _safe_text(item.get("downloader")),
                "content_type": _safe_text(item.get("content_type")),
                "file_size": _safe_int(item.get("file_size")) or (output_path.stat().st_size if output_path.exists() else 0),
                "probe": probe,
                "frame_samples": frame_samples,
                "frame_feature_summary": frame_stats,
                "subtitle_hints": subtitle_hints,
                "content_features": content_features,
                "analysis_input": {
                    "video_meta": {
                        "video_id": video_id,
                        "source_name": _safe_text(item.get("source_name")),
                        "video_url": _safe_text(item.get("video_url")),
                    },
                    "video_features": content_features,
                    "frame_feature_summary": frame_stats,
                    "subtitle_hints": subtitle_hints,
                },
            }
        )

    result = {
        "ok": True,
        "analysis_type": "local_video_inputs",
        "artifact_path": str(resolved_artifact),
        "prepared_count": len(manifest_items),
        "skipped_count": skipped_count,
        "frames_per_video": max(0, int(frames_per_video)),
        "generated_at": _now_iso(),
        "items": manifest_items,
        "warnings": warnings,
    }

    output_path = output or (
        artifacts_dir / "analysis-inputs" / f"local_video_inputs_{_now_token()}.json"
    )
    output_path = _resolve_output_path(workspace=workspace, output=output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output_path"] = str(output_path)
    return result


def _resolve_downloader_artifact(*, workspace: Path, artifacts_dir: Path, artifact: Path | None) -> Path:
    if artifact is not None:
        resolved = artifact if artifact.is_absolute() else (workspace / artifact)
        if resolved.exists():
            return resolved
        raise FileNotFoundError(f"artifact not found: {resolved}")

    candidates = sorted(
        (artifacts_dir / "downloader" / "results").glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("no downloader result artifact found under artifacts/downloader/results")
    return candidates[0]


def _resolve_output_path(*, workspace: Path, output: Path) -> Path:
    return output if output.is_absolute() else (workspace / output)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_download_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        results = payload.get("results")
        if isinstance(results, list):
            return [dict(item) for item in results if isinstance(item, Mapping)]
    raise ValueError("downloader artifact missing results list")


def _looks_like_video_download(item: Mapping[str, Any], output_path: Path) -> bool:
    if str(item.get("status") or "").lower() != "success":
        return False
    content_type = str(item.get("content_type") or "").lower()
    if content_type.startswith("video/"):
        return True
    return output_path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _ffprobe_video(video_path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc), "command": command}
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout or "").strip() or f"returncode={completed.returncode}",
            "command": command,
        }
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid ffprobe json: {exc}", "command": command}

    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    format_info = payload.get("format") if isinstance(payload.get("format"), Mapping) else {}
    video_stream = next((item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if isinstance(item, Mapping) and item.get("codec_type") == "audio"), {})

    video_codec = _safe_text(video_stream.get("codec_name"))
    width = _safe_int(video_stream.get("width"))
    height = _safe_int(video_stream.get("height"))
    format_name = _safe_text(format_info.get("format_name"))
    duration_sec = _safe_float(format_info.get("duration"))
    if not (video_codec or width > 0 or height > 0 or (format_name and duration_sec > 0)):
        return {
            "ok": False,
            "error": "ffprobe did not detect a valid video stream",
            "command": command,
        }

    return {
        "ok": True,
        "duration_sec": duration_sec,
        "bit_rate": _safe_int(format_info.get("bit_rate")),
        "format_name": format_name,
        "size_bytes": _safe_int(format_info.get("size")),
        "video": {
            "codec": video_codec,
            "width": width,
            "height": height,
            "pix_fmt": _safe_text(video_stream.get("pix_fmt")),
            "avg_frame_rate": _safe_text(video_stream.get("avg_frame_rate")),
        },
        "audio": {
            "codec": _safe_text(audio_stream.get("codec_name")),
            "sample_rate": _safe_int(audio_stream.get("sample_rate")),
            "channels": _safe_int(audio_stream.get("channels")),
        },
    }


def _extract_sample_frames(
    *,
    video_path: Path,
    probe: Mapping[str, Any],
    sample_dir: Path,
    frames_per_video: int,
) -> list[dict[str, Any]]:
    count = max(0, int(frames_per_video))
    if count <= 0:
        return []
    duration_sec = _safe_float(probe.get("duration_sec"))
    timestamps = _build_sample_timestamps(duration_sec=duration_sec, count=count)
    sample_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps, start=1):
        frame_path = sample_dir / f"frame_{index:02d}.jpg"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(frame_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            outputs.append(
                {
                    "index": index,
                    "timestamp_sec": round(timestamp, 3),
                    "ok": False,
                    "error": str(exc),
                    "output_path": str(frame_path),
                }
            )
            continue
        ok = completed.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 0
        outputs.append(
            {
                "index": index,
                "timestamp_sec": round(timestamp, 3),
                "ok": ok,
                "output_path": str(frame_path),
                "file_size": frame_path.stat().st_size if frame_path.exists() else 0,
                "error": "" if ok else (completed.stderr or completed.stdout or "").strip(),
            }
        )
    return outputs


def _probe_looks_like_real_video(probe: Mapping[str, Any]) -> bool:
    if not probe.get("ok"):
        return False
    duration_sec = _safe_float(probe.get("duration_sec"))
    video_block = probe.get("video") if isinstance(probe.get("video"), Mapping) else {}
    codec = _safe_text(video_block.get("codec")).lower()
    width = _safe_int(video_block.get("width"))
    height = _safe_int(video_block.get("height"))
    format_name = _safe_text(probe.get("format_name")).lower()
    if duration_sec < 1.0:
        return False
    if width <= 0 or height <= 0:
        return False
    if not codec:
        return False
    if codec in {"png", "mjpeg", "jpg", "jpeg"}:
        return False
    if format_name in {"png_pipe", "image2"}:
        return False
    return True


def _extract_frame_feature_summary(frame_samples: list[dict[str, Any]]) -> dict[str, Any]:
    stats_items: list[dict[str, float]] = []
    ok_count = 0
    for frame in frame_samples:
        if not isinstance(frame, Mapping) or not frame.get("ok"):
            continue
        frame_path = Path(str(frame.get("output_path") or "")).expanduser()
        if not frame_path.exists():
            continue
        parsed = _ffmpeg_signalstats(frame_path)
        if not parsed.get("ok"):
            continue
        ok_count += 1
        stats_items.append(parsed)

    if not stats_items:
        return {"ok": False, "sampled_frames": ok_count, "summary": {}}

    brightness_values = [_safe_float(item.get("yavg")) for item in stats_items]
    saturation_values = [_safe_float(item.get("satavg")) for item in stats_items]
    contrast_values = [
        max(0.0, _safe_float(item.get("yhigh")) - _safe_float(item.get("ylow")))
        for item in stats_items
    ]
    brightness_spread = _spread(brightness_values)
    saturation_spread = _spread(saturation_values)
    contrast_spread = _spread(contrast_values)

    return {
        "ok": True,
        "sampled_frames": len(stats_items),
        "summary": {
            "avg_brightness": round(_avg(brightness_values), 3),
            "avg_saturation": round(_avg(saturation_values), 3),
            "avg_contrast_span": round(_avg(contrast_values), 3),
            "brightness_spread": round(brightness_spread, 3),
            "saturation_spread": round(saturation_spread, 3),
            "contrast_spread": round(contrast_spread, 3),
            "brightness_level": _classify_brightness(_avg(brightness_values)),
            "saturation_level": _classify_saturation(_avg(saturation_values)),
            "contrast_level": _classify_contrast(_avg(contrast_values)),
            "visual_rhythm_hint": _classify_visual_rhythm(
                brightness_spread=brightness_spread,
                saturation_spread=saturation_spread,
                contrast_spread=contrast_spread,
            ),
        },
    }


def _build_content_feature_summary(*, probe: Mapping[str, Any], frame_stats: Mapping[str, Any]) -> dict[str, Any]:
    video_block = probe.get("video") if isinstance(probe.get("video"), Mapping) else {}
    width = _safe_int(video_block.get("width"))
    height = _safe_int(video_block.get("height"))
    duration_sec = _safe_float(probe.get("duration_sec"))
    bitrate = _safe_int(probe.get("bit_rate"))
    frame_summary = frame_stats.get("summary") if isinstance(frame_stats.get("summary"), Mapping) else {}

    orientation = "portrait" if height > width else "landscape" if width > height else "square"
    visual_tags = _build_visual_tags(
        duration_sec=duration_sec,
        brightness_level=_safe_text(frame_summary.get("brightness_level")),
        saturation_level=_safe_text(frame_summary.get("saturation_level")),
        contrast_level=_safe_text(frame_summary.get("contrast_level")),
        visual_rhythm_hint=_safe_text(frame_summary.get("visual_rhythm_hint")),
    )
    return {
        "duration_sec": round(duration_sec, 3),
        "duration_bucket": _classify_duration(duration_sec),
        "orientation": orientation,
        "resolution_tier": _classify_resolution(width=width, height=height),
        "bitrate_tier": _classify_bitrate(bitrate),
        "visual_tags": visual_tags,
        "visual_tone": {
            "brightness_level": _safe_text(frame_summary.get("brightness_level")),
            "saturation_level": _safe_text(frame_summary.get("saturation_level")),
            "contrast_level": _safe_text(frame_summary.get("contrast_level")),
            "visual_rhythm_hint": _safe_text(frame_summary.get("visual_rhythm_hint")),
        },
    }


def _extract_subtitle_hint_summary(frame_samples: list[dict[str, Any]]) -> dict[str, Any]:
    stats_items: list[dict[str, float]] = []
    for frame in frame_samples:
        if not isinstance(frame, Mapping) or not frame.get("ok"):
            continue
        frame_path = Path(str(frame.get("output_path") or "")).expanduser()
        if not frame_path.exists():
            continue
        parsed = _ffmpeg_signalstats(frame_path, crop_bottom_ratio=0.28)
        if not parsed.get("ok"):
            continue
        stats_items.append(parsed)

    if not stats_items:
        return {
            "ok": False,
            "ocr_runtime_available": False,
            "readability_hint": "unknown",
            "summary": {},
        }

    brightness_values = [_safe_float(item.get("yavg")) for item in stats_items]
    contrast_values = [
        max(0.0, _safe_float(item.get("yhigh")) - _safe_float(item.get("ylow")))
        for item in stats_items
    ]
    avg_brightness = _avg(brightness_values)
    avg_contrast = _avg(contrast_values)
    return {
        "ok": True,
        "ocr_runtime_available": False,
        "readability_hint": _classify_subtitle_readability(
            avg_brightness=avg_brightness,
            avg_contrast=avg_contrast,
        ),
        "summary": {
            "bottom_band_avg_brightness": round(avg_brightness, 3),
            "bottom_band_avg_contrast": round(avg_contrast, 3),
        },
    }


def _ffmpeg_signalstats(frame_path: Path, *, crop_bottom_ratio: float | None = None) -> dict[str, Any]:
    vf = "signalstats,metadata=print:file=-"
    if crop_bottom_ratio is not None and 0 < crop_bottom_ratio < 1:
        start_ratio = max(0.0, 1.0 - crop_bottom_ratio)
        vf = (
            f"crop=iw:ih*{crop_bottom_ratio:.4f}:0:ih*{start_ratio:.4f},"
            "signalstats,metadata=print:file=-"
        )
    command = [
        "ffmpeg",
        "-i",
        str(frame_path),
        "-vf",
        vf,
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    text = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    mapping: dict[str, Any] = {"ok": completed.returncode == 0}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("lavfi.signalstats."):
            continue
        key, _, raw_value = line.partition("=")
        short_key = key.replace("lavfi.signalstats.", "").strip().lower()
        mapping[short_key] = _safe_float(raw_value)
    if "yavg" not in mapping:
        mapping["ok"] = False
        mapping["error"] = "signalstats missing yavg"
    return mapping


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _spread(values: list[float]) -> float:
    if not values:
        return 0.0
    return max(values) - min(values)


def _classify_brightness(value: float) -> str:
    if value >= 150:
        return "bright"
    if value >= 95:
        return "balanced"
    return "dark"


def _classify_saturation(value: float) -> str:
    if value >= 55:
        return "high"
    if value >= 20:
        return "medium"
    return "low"


def _classify_contrast(value: float) -> str:
    if value >= 140:
        return "high"
    if value >= 80:
        return "medium"
    return "low"


def _classify_visual_rhythm(*, brightness_spread: float, saturation_spread: float, contrast_spread: float) -> str:
    movement_score = brightness_spread * 0.45 + saturation_spread * 0.25 + contrast_spread * 0.30
    if movement_score >= 45:
        return "dynamic"
    if movement_score >= 18:
        return "mixed"
    return "stable"


def _classify_duration(duration_sec: float) -> str:
    if duration_sec < 15:
        return "short"
    if duration_sec < 45:
        return "medium"
    return "long"


def _classify_resolution(*, width: int, height: int) -> str:
    longer_side = max(width, height)
    if longer_side >= 3000:
        return "4k_like"
    if longer_side >= 1800:
        return "high"
    if longer_side >= 1000:
        return "standard"
    return "low"


def _classify_bitrate(bitrate: int) -> str:
    if bitrate >= 4_000_000:
        return "high"
    if bitrate >= 1_500_000:
        return "medium"
    if bitrate > 0:
        return "low"
    return "unknown"


def _classify_subtitle_readability(*, avg_brightness: float, avg_contrast: float) -> str:
    if avg_contrast >= 150 and 40 <= avg_brightness <= 220:
        return "high"
    if avg_contrast >= 90:
        return "medium"
    return "low"


def _build_visual_tags(
    *,
    duration_sec: float,
    brightness_level: str,
    saturation_level: str,
    contrast_level: str,
    visual_rhythm_hint: str,
) -> list[str]:
    tags: list[str] = []
    if visual_rhythm_hint == "stable" and duration_sec >= 15:
        tags.append("possible_talking_head")
    if visual_rhythm_hint == "dynamic":
        tags.append("dynamic_visual_pacing")
    if brightness_level == "dark":
        tags.append("low_exposure_risk")
    if contrast_level == "high":
        tags.append("subject_separation_good")
    if saturation_level == "low":
        tags.append("soft_color_style")
    if saturation_level == "medium" and brightness_level == "balanced":
        tags.append("clean_educational_style")
    return tags


def _build_sample_timestamps(*, duration_sec: float, count: int) -> list[float]:
    if count <= 0:
        return []
    if duration_sec <= 0:
        return [0.0 for _ in range(count)]
    if count == 1:
        return [max(0.0, min(duration_sec * 0.2, max(duration_sec - 0.1, 0.0)))]

    start = max(duration_sec * 0.15, 0.0)
    end = max(duration_sec * 0.85, start)
    if end <= start:
        return [round(start, 3) for _ in range(count)]
    step = (end - start) / max(count - 1, 1)
    return [round(min(start + step * index, max(duration_sec - 0.1, 0.0)), 3) for index in range(count)]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_token() -> str:
    token = _now_iso().replace(":", "").replace("-", "").replace(".", "")
    return token.replace("+", "_plus_").replace("Z", "_z_")
