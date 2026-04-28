from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.short_video_intel.analysis.asr_features import analyze_asr_features_file
from src.short_video_intel.analysis.local_video_fit import analyze_local_video_item
from src.short_video_intel.analysis.multimodal_fusion import analyze_multimodal_inputs_file
from src.short_video_intel.analysis.multimodal_inputs import prepare_multimodal_inputs
from src.short_video_intel.analysis.ocr_features import analyze_ocr_features_file
from src.short_video_intel.analysis.person_visual_features import analyze_person_visual_features_file
from src.short_video_intel.analysis.script_structure import analyze_script_structure_file

DEFAULT_FEATURES_DIR = "artifacts/multimodal/features"
DEFAULT_OUTPUT_DIR = "artifacts/analysis"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """按账号抽样执行多模态分析，优先用于小批量验证而不是直接全量重跑。"""
    args = _parse_args(argv)
    workspace = args.workspace.resolve()
    run_id = args.run_id or _now_token()
    selected = _select_items(_load_items(_resolve_inputs(workspace, args.inputs)), args)
    outputs = _write_batch_inputs(workspace=workspace, items=selected, run_id=run_id)
    if args.dry_run:
        payload = _build_dry_run_payload(run_id=run_id, selected=selected, outputs=outputs)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    local_fit = _write_local_fit(workspace=workspace, items=selected, run_id=run_id)
    features_dir = _resolve_path(workspace, args.features_dir)
    steps = _run_feature_steps(workspace=workspace, batch_inputs=outputs["batch_inputs"], features_dir=features_dir, args=args, run_id=run_id)
    multimodal_inputs = _prepare_inputs_twice(workspace=workspace, local_fit=local_fit, features_dir=features_dir, run_id=run_id)
    script_result = analyze_script_structure_file(workspace=workspace, artifact=multimodal_inputs, features_dir=features_dir, output=_analysis_path(workspace, f"script_structure_batch_{run_id}.json"))
    final_inputs = _analysis_path(workspace, f"multimodal_inputs_batch_final_{run_id}.json")
    prepare_multimodal_inputs(workspace=workspace, local_fit_artifact=local_fit, features_dir=features_dir, output=final_inputs)
    fusion = analyze_multimodal_inputs_file(workspace=workspace, artifact=final_inputs, output=_analysis_path(workspace, f"multimodal_fusion_batch_{run_id}.json"))
    payload = _build_run_payload(run_id=run_id, selected=selected, outputs=outputs, local_fit=local_fit, steps=steps, script_result=script_result, final_inputs=final_inputs, fusion=fusion)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if fusion.get("ok") else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量执行本地视频多模态分析。")
    parser.add_argument("--workspace", type=Path, default=ROOT, help="工作区根目录。")
    parser.add_argument("--inputs", type=Path, default=None, help="local_video_inputs JSON，默认取最新。")
    parser.add_argument("--features-dir", type=Path, default=Path(DEFAULT_FEATURES_DIR), help="每视频特征目录。")
    parser.add_argument("--max-per-account", type=int, default=3, help="每个账号最多抽样视频数。")
    parser.add_argument("--limit", type=int, default=None, help="总视频上限。")
    parser.add_argument("--run-id", default=None, help="输出文件后缀。")
    parser.add_argument("--model-size", default="tiny", help="ASR 模型大小。")
    parser.add_argument("--skip-asr", action="store_true", help="跳过 ASR。")
    parser.add_argument("--skip-ocr", action="store_true", help="跳过 OCR。")
    parser.add_argument("--skip-person-visual", action="store_true", help="跳过人脸/姿态/主体。")
    parser.add_argument("--dry-run", action="store_true", help="只输出抽样计划。")
    return parser.parse_args(list(argv) if argv is not None else None)


def _resolve_inputs(workspace: Path, value: Path | None) -> Path:
    if value is not None:
        return _resolve_path(workspace, value)
    candidates = sorted((workspace / "artifacts" / "analysis-inputs").glob("local_video_inputs_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("未找到 local_video_inputs_*.json")
    return candidates[0]


def _load_items(path: Path) -> list[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") if isinstance(payload, Mapping) else None
    if not isinstance(items, list):
        raise ValueError("local video inputs missing items list")
    return [item for item in items if isinstance(item, Mapping)]


def _select_items(items: list[Mapping[str, Any]], args: argparse.Namespace) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    counts: dict[str, int] = {}
    for item in items:
        account = _text(item.get("source_name")) or "unknown"
        if counts.get(account, 0) >= max(1, args.max_per_account):
            continue
        selected.append(dict(item))
        counts[account] = counts.get(account, 0) + 1
        if args.limit is not None and len(selected) >= args.limit:
            break
    return selected


def _write_batch_inputs(*, workspace: Path, items: list[Mapping[str, Any]], run_id: str) -> dict[str, Path]:
    path = _analysis_path(workspace, f"local_video_inputs_batch_{run_id}.json")
    _write_json(path, {"items": items, "generated_at": _now_iso()})
    return {"batch_inputs": path}


def _write_local_fit(*, workspace: Path, items: list[Mapping[str, Any]], run_id: str) -> Path:
    results = []
    for index, item in enumerate(items):
        results.append({"index": index, "video_id": _text(item.get("video_id")), "video_url": _text(item.get("video_url")), "source_name": _text(item.get("source_name")), "content_features": dict(item.get("content_features") or {}), "frame_feature_summary": dict(item.get("frame_feature_summary") or {}), "fit": analyze_local_video_item(item)})
    path = _analysis_path(workspace, f"local_video_fit_batch_{run_id}.json")
    _write_json(path, {"ok": True, "analysis_type": "local_video_fit_batch", "result": {"total": len(results), "results": results}, "generated_at": _now_iso()})
    return path


def _run_feature_steps(*, workspace: Path, batch_inputs: Path, features_dir: Path, args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    steps: dict[str, Any] = {}
    if not args.skip_asr:
        steps["asr"] = analyze_asr_features_file(workspace=workspace, artifact=batch_inputs, features_dir=features_dir, output=_analysis_path(workspace, f"asr_features_batch_{run_id}.json"), model_size=args.model_size)
    if not args.skip_ocr:
        steps["ocr"] = analyze_ocr_features_file(workspace=workspace, artifact=batch_inputs, features_dir=features_dir, output=_analysis_path(workspace, f"ocr_features_batch_{run_id}.json"))
    if not args.skip_person_visual:
        steps["person_visual"] = analyze_person_visual_features_file(workspace=workspace, artifact=batch_inputs, features_dir=features_dir, output=_analysis_path(workspace, f"person_visual_features_batch_{run_id}.json"))
    return steps


def _prepare_inputs_twice(*, workspace: Path, local_fit: Path, features_dir: Path, run_id: str) -> Path:
    output = _analysis_path(workspace, f"multimodal_inputs_batch_pre_script_{run_id}.json")
    prepare_multimodal_inputs(workspace=workspace, local_fit_artifact=local_fit, features_dir=features_dir, output=output)
    return output


def _build_dry_run_payload(*, run_id: str, selected: list[Mapping[str, Any]], outputs: Mapping[str, Path]) -> dict[str, Any]:
    return {"ok": True, "dry_run": True, "run_id": run_id, "selected_count": len(selected), "accounts": _account_counts(selected), "outputs": {key: str(value) for key, value in outputs.items()}}


def _build_run_payload(**kwargs: Any) -> dict[str, Any]:
    selected = kwargs["selected"]
    return {"ok": True, "run_id": kwargs["run_id"], "selected_count": len(selected), "accounts": _account_counts(selected), "outputs": _stringify_outputs(kwargs), "fusion_summary": kwargs["fusion"].get("result", {}).get("summary", {})}


def _stringify_outputs(values: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, Path):
            output[key] = str(value)
        elif isinstance(value, Mapping) and value.get("output_path"):
            output[key] = str(value.get("output_path"))
    return output


def _account_counts(items: list[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        account = _text(item.get("source_name")) or "unknown"
        counts[account] = counts.get(account, 0) + 1
    return counts


def _analysis_path(workspace: Path, name: str) -> Path:
    return workspace / DEFAULT_OUTPUT_DIR / name


def _resolve_path(workspace: Path, value: Path) -> Path:
    return value if value.is_absolute() else workspace / value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _now_token() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


if __name__ == "__main__":
    raise SystemExit(main())
