from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PERSON_VISUAL_VERSION = "person-visual-features.v1"


def analyze_person_visual_features_file(
    *,
    workspace: Path,
    artifact: Path,
    output: Path | None = None,
    features_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_artifact = artifact if artifact.is_absolute() else workspace / artifact
    items = _extract_items(json.loads(resolved_artifact.read_text(encoding="utf-8")))
    deps = _load_dependencies()
    if not deps["ok"]:
        return _write_missing_result(workspace, output, resolved_artifact, deps)
    results = [_analyze_item(workspace=workspace, item=item, deps=deps) for item in items]
    feature_root = _resolve_features_dir(workspace=workspace, features_dir=features_dir)
    for item in results:
        _merge_feature_file(feature_root=feature_root, item=item)
    result = _build_file_result(artifact=resolved_artifact, results=results, feature_root=feature_root)
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    _write_json(resolved_output, result)
    result["output_path"] = str(resolved_output)
    return result


def _analyze_item(*, workspace: Path, item: Mapping[str, Any], deps: Mapping[str, Any]) -> dict[str, Any]:
    frames = _collect_frame_paths(workspace=workspace, item=item)
    frame_results = [_analyze_frame(frame=frame, deps=deps) for frame in frames]
    face = _build_face_quality(frame_results)
    pose = _build_pose_quality(frame_results)
    subject = _build_person_subject(frame_results)
    return {**_base_item(item), "ok": True, "frames_count": len(frames), "face_quality": face, "pose_quality": pose, "person_subject": subject}


def _analyze_frame(*, frame: Path, deps: Mapping[str, Any]) -> dict[str, Any]:
    cv2 = deps["cv2"]
    image_bgr = cv2.imread(str(frame))
    if image_bgr is None:
        return {"ok": False}
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    faces = deps["face_detector"].detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
    people, _weights = deps["person_detector"].detectMultiScale(image_bgr, winStride=(8, 8), padding=(8, 8), scale=1.05)
    return {"ok": True, "width": width, "height": height, "faces": [_rect_box(rect, width, height) for rect in faces], "people": [_rect_box(rect, width, height) for rect in people]}


def _rect_box(rect: Any, width: int, height: int) -> dict[str, float]:
    x, y, w, h = [float(value) for value in rect]
    return {"x": x / width, "y": y / height, "w": w / width, "h": h / height, "area": max(0.0, (w / width) * (h / height))}


def _build_face_quality(frames: list[Mapping[str, Any]]) -> dict[str, Any]:
    face_boxes = [box for frame in frames for box in frame.get("faces", []) if isinstance(box, Mapping)]
    best = max(face_boxes, key=lambda box: _num(box.get("area")), default={})
    detected_frames = sum(1 for frame in frames if frame.get("faces"))
    center = _center_score(best)
    return {
        "version": PERSON_VISUAL_VERSION,
        "face_detected": bool(face_boxes),
        "face_count": round(len(face_boxes) / max(1, len(frames)), 3),
        "face_ratio": round(_num(best.get("area")), 4),
        "center_score": center,
        "sharpness_score": 0.5,
        "expression_positive_score": 0.5,
        "occlusion_risk": round(1.0 - detected_frames / max(1, len(frames)), 3),
    }


def _build_pose_quality(frames: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "version": PERSON_VISUAL_VERSION,
        "pose_detected": False,
        "facing_camera_score": 0.0,
        "upper_body_visible": False,
        "gesture_activity_score": 0.0,
        "stability_score": 0.0,
        "missing_reason": "当前环境未接入姿态关键点模型，避免用人物框伪造姿态。",
    }


def _build_person_subject(frames: list[Mapping[str, Any]]) -> dict[str, Any]:
    people = [box for frame in frames for box in frame.get("people", []) if isinstance(box, Mapping)]
    best = max(people, key=lambda box: _num(box.get("area")), default={})
    subject_ratio = _num(best.get("w")) * _num(best.get("h"))
    return {
        "version": PERSON_VISUAL_VERSION,
        "person_detected": bool(people),
        "person_count": round(len(people) / max(1, len(frames)), 3),
        "subject_ratio": round(subject_ratio, 4),
        "center_score": _center_score(best),
        "background_clutter_score": round(max(0.0, 1.0 - subject_ratio * 3.0), 3) if people else 1.0,
    }


def _center_score(box: Mapping[str, Any]) -> float:
    if not box:
        return 0.0
    cx = _num(box.get("x")) + _num(box.get("w")) / 2
    cy = _num(box.get("y")) + _num(box.get("h")) / 2
    distance = abs(cx - 0.5) + abs(cy - 0.5)
    return round(max(0.0, 1.0 - distance), 3)


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
    return resolved if resolved.exists() and resolved.suffix.lower() in {".jpg", ".jpeg", ".png"} else None


def _extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
        source = payload["items"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("result"), Mapping):
        result = _as_dict(payload.get("result"))
        source = result.get("items") or result.get("results") or []
    elif isinstance(payload, list):
        source = payload
    else:
        raise ValueError("person visual artifact missing items/results list")
    return [item for item in source if isinstance(item, Mapping)]


def _merge_feature_file(*, feature_root: Path, item: Mapping[str, Any]) -> None:
    video_id = _text(item.get("video_id"))
    if not video_id or not item.get("ok"):
        return
    path = feature_root / f"{video_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = dict(payload) if isinstance(payload, Mapping) else {}
    for key in ("face_quality", "pose_quality", "person_subject"):
        merged[key] = _as_dict(item.get(key))
    _write_json(path, merged)


def _build_file_result(*, artifact: Path, results: list[Mapping[str, Any]], feature_root: Path) -> dict[str, Any]:
    return {"ok": True, "analysis_type": "person_visual_features", "artifact_path": str(artifact), "features_dir": str(feature_root), "result": {"version": PERSON_VISUAL_VERSION, "total": len(results), "summary": _summarize(results), "results": results}, "generated_at": _now_iso()}


def _summarize(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = max(1, len(results))
    return {"face_detected_count": sum(1 for item in results if _as_dict(item.get("face_quality")).get("face_detected")), "pose_detected_count": sum(1 for item in results if _as_dict(item.get("pose_quality")).get("pose_detected")), "person_detected_count": sum(1 for item in results if _as_dict(item.get("person_subject")).get("person_detected")), "total": len(results), "face_detected_ratio": round(sum(1 for item in results if _as_dict(item.get("face_quality")).get("face_detected")) / total, 3)}


def _write_missing_result(workspace: Path, output: Path | None, artifact: Path, deps: Mapping[str, Any]) -> dict[str, Any]:
    result = {"ok": False, "analysis_type": "person_visual_features", "artifact_path": str(artifact), "error_code": "missing_dependency", "errors": deps.get("errors", []), "generated_at": _now_iso()}
    resolved_output = _resolve_output_path(workspace=workspace, output=output)
    _write_json(resolved_output, result)
    result["output_path"] = str(resolved_output)
    return result


def _load_dependencies() -> dict[str, Any]:
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        return {"ok": False, "errors": [str(exc)]}
    face_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_detector = cv2.CascadeClassifier(face_path)
    person_detector = cv2.HOGDescriptor()
    person_detector.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return {"ok": True, "cv2": cv2, "face_detector": face_detector, "person_detector": person_detector}


def _resolve_features_dir(*, workspace: Path, features_dir: Path | None) -> Path:
    root = features_dir or workspace / "artifacts" / "multimodal" / "features"
    resolved = root if root.is_absolute() else workspace / root
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _resolve_output_path(*, workspace: Path, output: Path | None) -> Path:
    if output is not None:
        return output if output.is_absolute() else workspace / output
    return workspace / "artifacts" / "analysis" / f"person_visual_features_{_now_token()}.json"


def _base_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"video_id": _text(item.get("video_id")), "video_url": _text(item.get("video_url")), "source_name": _text(item.get("source_name"))}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
