from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .positive_factors import build_recommendations, score_accounts_from_summary
from .video_fit import analyze_video_fit, batch_analyze_video_fit

FULL_BATCH_ARTIFACT_SUBDIR = Path("collector") / "full-batch"


class AnalysisError(Exception):
    """Structured analysis failure used by the CLI wrapper."""


def generate_weekly_report_from_full_batch(
    *,
    workspace: Path,
    artifacts_dir: Path,
    artifact: Path | None = None,
) -> dict[str, Any]:
    """Generate a structured weekly report (JSON + Markdown) from a full-batch artifact."""

    try:
        resolved_artifact = _resolve_artifact_path(
            workspace=workspace,
            artifacts_dir=artifacts_dir,
            artifact=artifact,
        )
        payload = _load_json(resolved_artifact)
        summary_block = _extract_summary_block(payload)
        chunked_context = _extract_chunked_context(payload)
        scored = score_accounts_from_summary(summary_block)
        recommendations = build_recommendations(scored)
        generated_at = _safe_text(scored.get("generated_at")) or datetime.now(UTC).isoformat()

        score_block = _build_score_block(scored)
        accounts = list(scored.get("accounts") or [])
        top_accounts = list(score_block.get("top_accounts") or [])
        global_summary = _as_dict(scored.get("global_summary"))
        warnings: list[str] = []

        video_fit_summary: dict[str, Any] = {
            "enabled": True,
            "ok": False,
            "total_videos": 0,
            "summary": {},
            "top_videos": [],
        }
        try:
            video_fit_result = analyze_video_fit_from_full_batch(
                workspace=workspace,
                artifacts_dir=artifacts_dir,
                artifact=resolved_artifact,
                output=None,
            )
            if video_fit_result.get("ok"):
                fit_payload = _as_dict(video_fit_result.get("result"))
                fit_summary = _as_dict(fit_payload.get("summary"))
                fit_results = [item for item in list(fit_payload.get("results") or []) if isinstance(item, Mapping)]
                video_fit_summary = {
                    "enabled": True,
                    "ok": True,
                    "total_videos": _safe_int(video_fit_result.get("total_videos")),
                    "summary": fit_summary,
                    "top_videos": [dict(item) for item in fit_results[:5]],
                }
            else:
                warnings.append(_safe_text(_mapping_get(video_fit_result.get("error"), "message")) or "video fit analysis failed")
        except Exception as exc:  # pragma: no cover - defensive wrapper
            warnings.append(f"video fit analysis failed: {type(exc).__name__}: {exc}")

        report_json = {
            "global": {
                "generated_at": generated_at,
                "artifact_path": str(resolved_artifact),
                "scoring_version": _safe_text(score_block.get("scoring_version")),
                "overall_score": _safe_int(score_block.get("overall")),
                "account_count": len(accounts),
                "video_total": _safe_int(global_summary.get("video_total")),
                "detail_success_count": _safe_int(global_summary.get("detail_success_count")),
                "comment_success_count": _safe_int(global_summary.get("comment_success_count")),
                "detail_meaningful_count": _safe_int(global_summary.get("detail_meaningful_count")),
                "comment_meaningful_count": _safe_int(global_summary.get("comment_meaningful_count")),
                "failed_count": _safe_int(global_summary.get("failed_count")),
                "detail_success_rate": global_summary.get("detail_success_rate", 0),
                "comment_success_rate": global_summary.get("comment_success_rate", 0),
            },
            "account": {
                "top_accounts": top_accounts,
                "accounts": accounts,
            },
            "recommendations": recommendations,
            "video_fit_summary": video_fit_summary,
        }
        if warnings:
            report_json["warnings"] = warnings
        if chunked_context:
            report_json.update(chunked_context)

        report_markdown = _build_weekly_report_markdown(
            report_json=report_json,
            recommendations=recommendations,
            warnings=warnings,
        )

        return {
            "ok": True,
            "analysis_type": "weekly_report",
            "artifact_path": str(resolved_artifact),
            "generated_at": generated_at,
            "report_json": report_json,
            "report_markdown": report_markdown,
        }
    except AnalysisError as exc:
        return _error_result_weekly_report(exc)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _error_result_weekly_report(AnalysisError(f"{type(exc).__name__}: {exc}"))


def generate_phase1_chunked_report(
    *,
    workspace: Path,
    artifacts_dir: Path,
    artifact: Path | None = None,
) -> dict[str, Any]:
    """Generate a focused operations report for a phase1 chunked master artifact."""

    try:
        if artifact is None:
            resolved_artifact = _find_latest_phase1_chunked_artifact(artifacts_dir / FULL_BATCH_ARTIFACT_SUBDIR)
        else:
            resolved_artifact = _resolve_artifact_path(
                workspace=workspace,
                artifacts_dir=artifacts_dir,
                artifact=artifact,
            )
        payload = _load_json(resolved_artifact)
        if _safe_text(_mapping_get(payload, "mode")) != "phase1_chunked":
            raise AnalysisError("artifact is not a phase1_chunked master artifact")

        summary_block = _extract_summary_block(payload)
        scored = score_accounts_from_summary(summary_block)
        recommendations = build_recommendations(scored)
        score_block = _build_score_block(scored)
        global_summary = _as_dict(summary_block.get("global_summary"))
        account_summary = [
            dict(item)
            for item in list(summary_block.get("account_summary") or [])
            if isinstance(item, Mapping)
        ]
        rerun_priority_chunks = [
            dict(item)
            for item in list(payload.get("rerun_priority_chunks") or [])
            if isinstance(item, Mapping)
        ]
        slowest_chunks = [
            dict(item)
            for item in list(payload.get("slowest_chunks") or [])
            if isinstance(item, Mapping)
        ]
        failed_chunks = [
            dict(item)
            for item in list(payload.get("failed_chunks") or [])
            if isinstance(item, Mapping)
        ]

        report_json = {
            "global": {
                "artifact_path": str(resolved_artifact),
                "targets_loaded": _safe_int(payload.get("targets_loaded")),
                "chunk_count": _safe_int(payload.get("chunk_count")),
                "chunk_size": _safe_int(payload.get("chunk_size")),
                "chunk_success_count": _safe_int(global_summary.get("chunk_success_count")),
                "chunk_failed_count": _safe_int(global_summary.get("chunk_failed_count")),
                "failed_target_count": _safe_int(global_summary.get("failed_count")),
                "video_total": _safe_int(global_summary.get("video_total")),
                "detail_attempted": _safe_int(global_summary.get("detail_attempted")),
                "detail_success_count": _safe_int(global_summary.get("detail_success_count")),
                "detail_meaningful_count": _safe_int(global_summary.get("detail_meaningful_count")),
                "comment_attempted": _safe_int(global_summary.get("comment_attempted")),
                "comment_success_count": _safe_int(global_summary.get("comment_success_count")),
                "comment_meaningful_count": _safe_int(global_summary.get("comment_meaningful_count")),
                "detail_success_rate": global_summary.get("detail_success_rate", 0),
                "comment_success_rate": global_summary.get("comment_success_rate", 0),
                "total_duration_sec": global_summary.get("total_duration_sec", 0),
                "rerun_targets_count": _safe_int(payload.get("rerun_targets_count")),
            },
            "top_accounts": list(score_block.get("top_accounts") or []),
            "accounts": account_summary,
            "rerun_priority_chunks": rerun_priority_chunks,
            "slowest_chunks": slowest_chunks,
            "failed_chunks": failed_chunks,
            "rerun_manifest_path": _safe_text(payload.get("rerun_manifest_path")),
            "rerun_command_example": _safe_text(payload.get("rerun_command_example")),
            "recommendations": recommendations,
        }
        report_markdown = _build_phase1_chunked_markdown(report_json)
        return {
            "ok": True,
            "analysis_type": "phase1_chunked_report",
            "artifact_path": str(resolved_artifact),
            "report_json": report_json,
            "report_markdown": report_markdown,
        }
    except AnalysisError as exc:
        return {
            "ok": False,
            "analysis_type": "phase1_chunked_report",
            "artifact_path": None,
            "report_json": {},
            "report_markdown": "",
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return {
            "ok": False,
            "analysis_type": "phase1_chunked_report",
            "artifact_path": None,
            "report_json": {},
            "report_markdown": "",
            "error": {
                "type": "AnalysisError",
                "message": f"{type(exc).__name__}: {exc}",
            },
        }


def export_phase1_rerun_manifest(
    *,
    workspace: Path,
    artifacts_dir: Path,
    artifact: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    """Extract rerun targets from a phase1 chunked master artifact."""

    try:
        if artifact is None:
            resolved_artifact = _find_latest_phase1_chunked_artifact(artifacts_dir / FULL_BATCH_ARTIFACT_SUBDIR)
        else:
            resolved_artifact = _resolve_artifact_path(
                workspace=workspace,
                artifacts_dir=artifacts_dir,
                artifact=artifact,
            )
        payload = _load_json(resolved_artifact)
        if _safe_text(_mapping_get(payload, "mode")) != "phase1_chunked":
            raise AnalysisError("artifact is not a phase1_chunked master artifact")

        rerun_targets: list[dict[str, Any]] = []
        for item in list(payload.get("failed_chunks") or []):
            if not isinstance(item, Mapping):
                continue
            for target in list(item.get("failed_targets") or []):
                if isinstance(target, Mapping):
                    rerun_targets.append(dict(target))

        manifest = {
            "ok": True,
            "analysis_type": "phase1_rerun_manifest_export",
            "artifact_path": str(resolved_artifact),
            "targets_loaded": len(rerun_targets),
            "targets": rerun_targets,
            "rerun_command_example": _safe_text(payload.get("rerun_command_example")),
        }
        if output is not None:
            output_path = _resolve_output_path(workspace=workspace, output=output)
            _write_json(output_path, manifest)
            manifest["output_path"] = str(output_path)
        return manifest
    except AnalysisError as exc:
        return _error_result(exc, analysis_type="phase1_rerun_manifest_export")
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _error_result(
            AnalysisError(f"{type(exc).__name__}: {exc}"),
            analysis_type="phase1_rerun_manifest_export",
        )


def get_phase1_status_overview(
    *,
    workspace: Path,
    artifacts_dir: Path,
) -> dict[str, Any]:
    """Return a compact overview of the latest phase1-related artifacts."""

    try:
        full_batch_root = artifacts_dir / FULL_BATCH_ARTIFACT_SUBDIR
        latest_full_batch = _find_latest_standard_full_batch_artifact(full_batch_root)
        latest_chunked = _find_latest_phase1_chunked_artifact(full_batch_root)

        full_batch_payload = _load_json(latest_full_batch)
        full_batch_summary = _extract_summary_block(full_batch_payload)
        full_batch_global = _as_dict(full_batch_summary.get("global_summary"))

        chunked_payload = _load_json(latest_chunked)
        chunked_summary = _extract_summary_block(chunked_payload)
        chunked_global = _as_dict(chunked_summary.get("global_summary"))

        result = {
            "ok": True,
            "analysis_type": "phase1_status_overview",
            "workspace": str(workspace),
            "latest_full_batch": _build_artifact_overview(
                path=latest_full_batch,
                payload=full_batch_payload,
                global_summary=full_batch_global,
            ),
            "latest_phase1_chunked": _build_artifact_overview(
                path=latest_chunked,
                payload=chunked_payload,
                global_summary=chunked_global,
            ),
        }
        result["markdown"] = _build_phase1_status_overview_markdown(result)
        return result
    except AnalysisError as exc:
        return _error_result(exc, analysis_type="phase1_status_overview")
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _error_result(
            AnalysisError(f"{type(exc).__name__}: {exc}"),
            analysis_type="phase1_status_overview",
        )


def analyze_positive_factors(
    *,
    workspace: Path,
    artifacts_dir: Path,
    artifact: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    """Analyze a full-batch artifact and return scores plus recommendations."""

    try:
        resolved_artifact = _resolve_artifact_path(
            workspace=workspace,
            artifacts_dir=artifacts_dir,
            artifact=artifact,
        )
        payload = _load_json(resolved_artifact)
        summary_block = _extract_summary_block(payload)
        scored = score_accounts_from_summary(summary_block)
        recommendations = build_recommendations(scored)
        result = {
            "ok": True,
            "analysis_type": "positive_factors",
            "generated_at": scored.get("generated_at"),
            "artifact_path": str(resolved_artifact),
            "score": _build_score_block(scored),
            "recommendations": recommendations,
        }
        if output is not None:
            output_path = _resolve_output_path(workspace=workspace, output=output)
            _write_json(output_path, result)
            result["output_path"] = str(output_path)
        return result
    except AnalysisError as exc:
        return _error_result(exc)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _error_result(AnalysisError(f"{type(exc).__name__}: {exc}"))


def _resolve_artifact_path(*, workspace: Path, artifacts_dir: Path, artifact: Path | None) -> Path:
    if artifact is not None:
        resolved = _resolve_user_path(workspace=workspace, value=artifact)
        if resolved.is_dir():
            resolved = _find_latest_full_batch_artifact(resolved)
        if not resolved.exists():
            raise AnalysisError(f"artifact not found: {resolved}")
        if not resolved.is_file():
            raise AnalysisError(f"artifact is not a file: {resolved}")
        return resolved

    return _find_latest_full_batch_artifact(artifacts_dir / FULL_BATCH_ARTIFACT_SUBDIR)


def _find_latest_full_batch_artifact(root: Path) -> Path:
    if not root.exists():
        raise AnalysisError(f"full-batch artifact directory not found: {root}")
    if root.is_file():
        return root

    candidates = [
        path
        for path in root.rglob("*.json")
        if path.is_file() and FULL_BATCH_ARTIFACT_SUBDIR.as_posix() in path.as_posix()
    ]
    if not candidates:
        raise AnalysisError(f"no full-batch artifact json files found under: {root}")

    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return candidates[0]


def _find_latest_standard_full_batch_artifact(root: Path) -> Path:
    if not root.exists():
        raise AnalysisError(f"full-batch artifact directory not found: {root}")
    candidates = sorted(
        [
            path
            for path in root.rglob("*.json")
            if path.is_file() and FULL_BATCH_ARTIFACT_SUBDIR.as_posix() in path.as_posix()
        ],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    for candidate in candidates:
        try:
            payload = _load_json(candidate)
        except Exception:
            continue
        if _safe_text(_mapping_get(payload, "mode")) != "phase1_chunked":
            return candidate
    raise AnalysisError(f"no standard full-batch artifact json files found under: {root}")


def _find_latest_phase1_chunked_artifact(root: Path) -> Path:
    if not root.exists():
        raise AnalysisError(f"full-batch artifact directory not found: {root}")
    if root.is_file():
        payload = _load_json(root)
        if _safe_text(_mapping_get(payload, "mode")) == "phase1_chunked":
            return root
        raise AnalysisError(f"artifact is not a phase1_chunked master artifact: {root}")

    candidates = sorted(
        [
            path
            for path in root.rglob("*.json")
            if path.is_file() and FULL_BATCH_ARTIFACT_SUBDIR.as_posix() in path.as_posix()
        ],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    for candidate in candidates:
        try:
            payload = _load_json(candidate)
        except Exception:
            continue
        if _safe_text(_mapping_get(payload, "mode")) == "phase1_chunked":
            return candidate
    raise AnalysisError(f"no phase1_chunked master artifact json files found under: {root}")


def _build_artifact_overview(
    *,
    path: Path,
    payload: Mapping[str, Any],
    global_summary: Mapping[str, Any],
) -> dict[str, Any]:
    stat = path.stat()
    return {
        "artifact_path": str(path),
        "artifact_name": path.name,
        "mode": _safe_text(_mapping_get(payload, "mode")) or "full_batch",
        "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "target_count": _safe_int(global_summary.get("target_count") or payload.get("targets_loaded")),
        "video_total": _safe_int(global_summary.get("video_total")),
        "detail_success_count": _safe_int(global_summary.get("detail_success_count")),
        "comment_success_count": _safe_int(global_summary.get("comment_success_count")),
        "detail_meaningful_count": _safe_int(global_summary.get("detail_meaningful_count")),
        "comment_meaningful_count": _safe_int(global_summary.get("comment_meaningful_count")),
        "failed_count": _safe_int(global_summary.get("failed_count")),
        "chunk_count": _safe_int(global_summary.get("chunk_count") or payload.get("chunk_count")),
        "chunk_failed_count": _safe_int(global_summary.get("chunk_failed_count")),
        "rerun_targets_count": _safe_int(payload.get("rerun_targets_count")),
    }


def _extract_summary_block(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AnalysisError("artifact payload must be a JSON object")

    for candidate in (
        payload.get("summary_block"),
        _mapping_get(payload.get("batch"), "summary_block"),
        payload.get("summary"),
        _mapping_get(payload.get("batch"), "summary"),
    ):
        if isinstance(candidate, Mapping):
            candidate_dict = dict(candidate)
            if "global_summary" in candidate_dict or "account_summary" in candidate_dict:
                return candidate_dict

    chunked_fallback = _build_summary_block_from_chunked_payload(payload)
    if chunked_fallback:
        return chunked_fallback

    raise AnalysisError("summary block not found in artifact")


def _build_score_block(scored: Mapping[str, Any]) -> dict[str, Any]:
    accounts = list(scored.get("accounts") or [])
    if not accounts:
        overall_score = 0
    else:
        total_scores = [_safe_int(account.get("total_score")) for account in accounts if isinstance(account, Mapping)]
        overall_score = round(sum(total_scores) / len(total_scores)) if total_scores else 0

    top_accounts = []
    for account in accounts[:3]:
        if not isinstance(account, Mapping):
            continue
        top_accounts.append(
            {
                "rank": account.get("rank", 0),
                "source_name": account.get("source_name", ""),
                "homepage_url": account.get("homepage_url", ""),
                "total_score": account.get("total_score", 0),
                "signals": account.get("signals", {}),
            }
        )

    return {
        "overall": overall_score,
        "scoring_version": scored.get("scoring_version", ""),
        "weights": scored.get("weights", {}),
        "baselines": scored.get("baselines", {}),
        "accounts": accounts,
        "top_accounts": top_accounts,
    }


def _resolve_output_path(*, workspace: Path, output: Path) -> Path:
    resolved = _resolve_user_path(workspace=workspace, value=output)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_user_path(*, workspace: Path, value: Path) -> Path:
    resolved = Path(value).expanduser()
    if not resolved.is_absolute():
        resolved = workspace / resolved
    return resolved.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _mapping_get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def analyze_video_fit_from_file(
    *,
    workspace: Path,
    input_path: Path,
    output: Path | None = None,
) -> dict[str, Any]:
    """Analyze one or many video details from a JSON file."""

    try:
        resolved_input = _resolve_user_path(workspace=workspace, value=input_path)
        if not resolved_input.exists() or not resolved_input.is_file():
            raise AnalysisError(f"input file not found: {resolved_input}")

        payload = _load_json(resolved_input)
        if isinstance(payload, list):
            batch_result = batch_analyze_video_fit([dict(item) if isinstance(item, Mapping) else {"video_detail": item} for item in payload])
            result = {
                "ok": True,
                "analysis_type": "video_fit_batch",
                "input_path": str(resolved_input),
                "result": batch_result,
            }
        else:
            single_result = analyze_video_fit(dict(payload) if isinstance(payload, Mapping) else {"video_detail": payload})
            result = {
                "ok": True,
                "analysis_type": "video_fit_single",
                "input_path": str(resolved_input),
                "result": single_result,
            }

        if output is not None:
            output_path = _resolve_output_path(workspace=workspace, output=output)
            _write_json(output_path, result)
            result["output_path"] = str(output_path)
        return result
    except AnalysisError as exc:
        return _error_result(exc, analysis_type="video_fit")
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _error_result(AnalysisError(f"{type(exc).__name__}: {exc}"), analysis_type="video_fit")


def analyze_video_fit_from_full_batch(
    *,
    workspace: Path,
    artifacts_dir: Path,
    artifact: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    """Analyze video fit in batch based on a full-batch artifact."""

    try:
        resolved_artifact = _resolve_artifact_path(
            workspace=workspace,
            artifacts_dir=artifacts_dir,
            artifact=artifact,
        )
        payload = _load_json(resolved_artifact)
        batch_results = _extract_full_batch_results(payload)
        items = _build_video_fit_batch_items(batch_results)
        batch_result = batch_analyze_video_fit(items)
        enriched_results = _merge_video_fit_results_with_context(
            contexts=items,
            fit_results=list(batch_result.get("results") or []),
        )

        result = {
            "ok": True,
            "analysis_type": "video_fit_from_full_batch",
            "artifact_path": str(resolved_artifact),
            "total_videos": len(items),
            "result": {
                "version": batch_result.get("version", ""),
                "summary": batch_result.get("summary", {}),
                "results": enriched_results,
            },
        }

        if output is not None:
            output_path = _resolve_output_path(workspace=workspace, output=output)
            _write_json(output_path, result)
            result["output_path"] = str(output_path)
        return result
    except AnalysisError as exc:
        return _error_result_video_fit_from_full_batch(exc)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        return _error_result_video_fit_from_full_batch(AnalysisError(f"{type(exc).__name__}: {exc}"))


def _extract_full_batch_results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise AnalysisError("artifact payload must be a JSON object")

    candidates = (
        _mapping_get(payload.get("batch"), "results"),
        payload.get("results"),
    )
    for candidate in candidates:
        if isinstance(candidate, list):
            return [dict(item) for item in candidate if isinstance(item, Mapping)]

    raise AnalysisError("batch.results not found in artifact")


def _build_video_fit_batch_items(batch_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for result_item in batch_results:
        target = _as_dict(result_item.get("target"))
        source_name = _safe_text(target.get("source_name"))
        homepage_url = _safe_text(target.get("homepage_url"))

        for video_item in list(result_item.get("video_items") or []):
            if not isinstance(video_item, Mapping):
                continue
            candidate = _as_dict(video_item.get("candidate"))
            detail_result = _as_dict(video_item.get("detail_result"))
            metrics = _as_dict(detail_result.get("metrics"))
            video_url = _safe_text(
                candidate.get("video_url")
                or detail_result.get("video_url")
                or _build_video_url_from_candidate(candidate)
            )
            video_id = _safe_text(candidate.get("video_id"))

            item = {
                "video_detail": {
                    "metrics": metrics,
                    "source_name": source_name,
                    "homepage_url": homepage_url,
                    "video_url": video_url,
                    "video_id": video_id,
                }
            }
            items.append(item)
    return items


def _merge_video_fit_results_with_context(
    *,
    contexts: list[dict[str, Any]],
    fit_results: list[Any],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for index, context in enumerate(contexts):
        video_detail = _as_dict(context.get("video_detail"))
        fit_result = fit_results[index] if index < len(fit_results) and isinstance(fit_results[index], Mapping) else {}
        merged.append(
            {
                "index": index,
                "source_name": video_detail.get("source_name", ""),
                "homepage_url": video_detail.get("homepage_url", ""),
                "video_url": video_detail.get("video_url", ""),
                "video_id": video_detail.get("video_id", ""),
                "metrics": video_detail.get("metrics", {}),
                "fit": dict(fit_result),
            }
        )
    return merged


def _build_video_url_from_candidate(candidate: Mapping[str, Any]) -> str:
    video_id = _safe_text(candidate.get("video_id"))
    if not video_id:
        return ""
    return f"https://www.douyin.com/video/{video_id}"


def _error_result_video_fit_from_full_batch(exc: AnalysisError) -> dict[str, Any]:
    return {
        "ok": False,
        "analysis_type": "video_fit_from_full_batch",
        "artifact_path": None,
        "total_videos": 0,
        "result": {"summary": {}, "results": []},
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }


def _error_result(exc: AnalysisError, *, analysis_type: str = "positive_factors") -> dict[str, Any]:
    return {
        "ok": False,
        "analysis_type": analysis_type,
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "score": None,
        "recommendations": [],
    }


def _error_result_weekly_report(exc: AnalysisError) -> dict[str, Any]:
    return {
        "ok": False,
        "analysis_type": "weekly_report",
        "artifact_path": None,
        "generated_at": None,
        "report_json": {
            "global": {},
            "account": {"top_accounts": [], "accounts": []},
            "recommendations": [],
            "video_fit_summary": {"enabled": True, "ok": False, "total_videos": 0, "summary": {}, "top_videos": []},
        },
        "report_markdown": "",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _build_weekly_report_markdown(
    *,
    report_json: Mapping[str, Any],
    recommendations: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    global_block = _as_dict(report_json.get("global"))
    account_block = _as_dict(report_json.get("account"))
    top_accounts = [item for item in list(account_block.get("top_accounts") or []) if isinstance(item, Mapping)]
    video_fit_summary = _as_dict(report_json.get("video_fit_summary"))
    video_fit_stats = _as_dict(video_fit_summary.get("summary"))

    lines = [
        "# 周报（short_video_intel）",
        "",
        "## 全局概览",
        f"- 生成时间：{_safe_text(global_block.get('generated_at'))}",
        f"- 总体评分：{_safe_int(global_block.get('overall_score'))}",
        f"- 账号数：{_safe_int(global_block.get('account_count'))}",
        f"- 视频总量：{_safe_int(global_block.get('video_total'))}",
        f"- 详情成功率：{global_block.get('detail_success_rate', 0)}",
        f"- 评论成功率：{global_block.get('comment_success_rate', 0)}",
        f"- 有效详情数：{_safe_int(global_block.get('detail_meaningful_count'))}",
        f"- 有效评论数：{_safe_int(global_block.get('comment_meaningful_count'))}",
        "",
        "## 账号TOP3",
    ]
    if top_accounts:
        for item in top_accounts[:3]:
            lines.append(
                f"- #{_safe_int(item.get('rank'))} {_safe_text(item.get('source_name'))}：总分 {_safe_int(item.get('total_score'))}"
            )
    else:
        lines.append("- 暂无账号评分结果")

    lines.extend(["", "## 建议（优先级排序）"])
    if recommendations:
        for rec in recommendations[:10]:
            actions = list(rec.get("actions") or [])
            lines.append(
                f"- [{_safe_text(rec.get('priority'))}] {_safe_text(rec.get('source_name') or rec.get('title'))}：{_safe_text(rec.get('reason'))}"
            )
            for action in actions[:3]:
                lines.append(f"  - {action}")
    else:
        lines.append("- 暂无建议")

    lines.extend(
        [
            "",
            "## 视频匹配（可选融合）",
            f"- 融合状态：{'ok' if video_fit_summary.get('ok') else 'failed'}",
            f"- 样本数：{_safe_int(video_fit_summary.get('total_videos'))}",
            f"- 匹配摘要：{json.dumps(video_fit_stats, ensure_ascii=False)}",
        ]
    )

    failed_chunks = report_json.get("failed_chunks")
    if isinstance(failed_chunks, list) and failed_chunks:
        lines.extend(["", "## Chunk失败摘要"])
        for item in failed_chunks[:10]:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "- chunk #{idx}：failed_count={failed}，artifact={artifact}".format(
                    idx=_safe_int(item.get("chunk_index")),
                    failed=_safe_int(item.get("failed_count")),
                    artifact=_safe_text(item.get("artifact_path")),
                )
            )

    slowest_chunks = report_json.get("slowest_chunks")
    if isinstance(slowest_chunks, list) and slowest_chunks:
        lines.extend(["", "## 慢Chunk TOP"])
        for item in slowest_chunks[:5]:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                "- chunk #{idx}：duration_sec={duration}，status={status}，artifact={artifact}".format(
                    idx=_safe_int(item.get("chunk_index")),
                    duration=item.get("duration_sec", 0),
                    status=_safe_text(item.get("status")),
                    artifact=_safe_text(item.get("artifact_path")),
                )
            )

    rerun_priority_chunks = report_json.get("rerun_priority_chunks")
    if isinstance(rerun_priority_chunks, list) and rerun_priority_chunks:
        lines.extend(["", "## 补跑优先级"])
        for item in rerun_priority_chunks[:10]:
            if not isinstance(item, Mapping):
                continue
            target_names = "、".join(str(name) for name in list(item.get("target_names") or [])[:3])
            lines.append(
                "- chunk #{idx}：priority={priority}，reason={reason}，targets={targets}".format(
                    idx=_safe_int(item.get("chunk_index")),
                    priority=item.get("priority_score", 0),
                    reason=_safe_text(item.get("reason")),
                    targets=target_names or "-",
                )
            )

    rerun_command_example = report_json.get("rerun_command_example")
    if isinstance(rerun_command_example, str) and rerun_command_example.strip():
        lines.extend(["", "## 推荐重跑命令", "```powershell", rerun_command_example.strip(), "```"])

    if warnings:
        lines.extend(["", "## Warnings"])
        for warning in warnings:
            lines.append(f"- {warning}")

    return "\n".join(lines).strip() + "\n"


def _build_phase1_chunked_markdown(report_json: Mapping[str, Any]) -> str:
    global_block = _as_dict(report_json.get("global"))
    top_accounts = [item for item in list(report_json.get("top_accounts") or []) if isinstance(item, Mapping)]
    rerun_priority_chunks = [item for item in list(report_json.get("rerun_priority_chunks") or []) if isinstance(item, Mapping)]
    slowest_chunks = [item for item in list(report_json.get("slowest_chunks") or []) if isinstance(item, Mapping)]
    failed_chunks = [item for item in list(report_json.get("failed_chunks") or []) if isinstance(item, Mapping)]
    recommendations = [item for item in list(report_json.get("recommendations") or []) if isinstance(item, Mapping)]

    lines = [
        "# Phase1 Chunked 运行总览",
        "",
        "## 全局概览",
        f"- artifact：{_safe_text(global_block.get('artifact_path'))}",
        f"- targets_loaded：{_safe_int(global_block.get('targets_loaded'))}",
        f"- chunk_count：{_safe_int(global_block.get('chunk_count'))}",
        f"- chunk_size：{_safe_int(global_block.get('chunk_size'))}",
        f"- chunk_success_count：{_safe_int(global_block.get('chunk_success_count'))}",
        f"- chunk_failed_count：{_safe_int(global_block.get('chunk_failed_count'))}",
        f"- failed_target_count：{_safe_int(global_block.get('failed_target_count'))}",
        f"- video_total：{_safe_int(global_block.get('video_total'))}",
        f"- detail_meaningful_count：{_safe_int(global_block.get('detail_meaningful_count'))}",
        f"- comment_meaningful_count：{_safe_int(global_block.get('comment_meaningful_count'))}",
        f"- detail_success_rate：{global_block.get('detail_success_rate', 0)}",
        f"- comment_success_rate：{global_block.get('comment_success_rate', 0)}",
        f"- total_duration_sec：{global_block.get('total_duration_sec', 0)}",
        "",
        "## 账号TOP3",
    ]
    if top_accounts:
        for item in top_accounts[:3]:
            lines.append(
                f"- #{_safe_int(item.get('rank'))} {_safe_text(item.get('source_name'))}：总分 {_safe_int(item.get('total_score'))}"
            )
    else:
        lines.append("- 暂无账号评分结果")

    lines.extend(["", "## 补跑优先级"])
    if rerun_priority_chunks:
        for item in rerun_priority_chunks[:10]:
            target_names = "、".join(str(name) for name in list(item.get("target_names") or [])[:4])
            lines.append(
                "- chunk #{idx}：priority={priority}，reason={reason}，targets={targets}".format(
                    idx=_safe_int(item.get("chunk_index")),
                    priority=item.get("priority_score", 0),
                    reason=_safe_text(item.get("reason")),
                    targets=target_names or "-",
                )
            )
    else:
        lines.append("- 当前无补跑优先级条目")

    lines.extend(["", "## 慢Chunk TOP"])
    if slowest_chunks:
        for item in slowest_chunks[:5]:
            lines.append(
                "- chunk #{idx}：duration_sec={duration}，status={status}".format(
                    idx=_safe_int(item.get("chunk_index")),
                    duration=item.get("duration_sec", 0),
                    status=_safe_text(item.get("status")),
                )
            )
    else:
        lines.append("- 暂无慢 chunk 数据")

    lines.extend(["", "## 失败Chunk"])
    if failed_chunks:
        for item in failed_chunks[:10]:
            target_names = "、".join(str(name) for name in list(item.get("target_names") or [])[:4])
            lines.append(
                "- chunk #{idx}：failed_count={failed}，targets={targets}".format(
                    idx=_safe_int(item.get("chunk_index")),
                    failed=_safe_int(item.get("failed_count")),
                    targets=target_names or "-",
                )
            )
    else:
        lines.append("- 当前无失败 chunk")

    lines.extend(["", "## 建议（优先级排序）"])
    if recommendations:
        for rec in recommendations[:10]:
            lines.append(
                f"- [{_safe_text(rec.get('priority'))}] {_safe_text(rec.get('source_name') or rec.get('title'))}：{_safe_text(rec.get('reason'))}"
            )
    else:
        lines.append("- 暂无建议")

    rerun_command_example = _safe_text(report_json.get("rerun_command_example"))
    if rerun_command_example:
        lines.extend(["", "## 推荐重跑命令", "```powershell", rerun_command_example, "```"])

    rerun_manifest_path = _safe_text(report_json.get("rerun_manifest_path"))
    if rerun_manifest_path:
        lines.extend(["", "## 重跑清单", f"- rerun_manifest_path：{rerun_manifest_path}"])

    return "\n".join(lines).strip() + "\n"


def _build_phase1_status_overview_markdown(result: Mapping[str, Any]) -> str:
    latest_full_batch = _as_dict(result.get("latest_full_batch"))
    latest_chunked = _as_dict(result.get("latest_phase1_chunked"))
    lines = [
        "# Phase1 状态总览",
        "",
        "## 最新 Full Batch",
        f"- artifact：{_safe_text(latest_full_batch.get('artifact_name'))}",
        f"- updated_at：{_safe_text(latest_full_batch.get('updated_at'))}",
        f"- target_count：{_safe_int(latest_full_batch.get('target_count'))}",
        f"- video_total：{_safe_int(latest_full_batch.get('video_total'))}",
        f"- detail_meaningful_count：{_safe_int(latest_full_batch.get('detail_meaningful_count'))}",
        f"- comment_meaningful_count：{_safe_int(latest_full_batch.get('comment_meaningful_count'))}",
        f"- failed_count：{_safe_int(latest_full_batch.get('failed_count'))}",
        "",
        "## 最新 Phase1 Chunked",
        f"- artifact：{_safe_text(latest_chunked.get('artifact_name'))}",
        f"- updated_at：{_safe_text(latest_chunked.get('updated_at'))}",
        f"- target_count：{_safe_int(latest_chunked.get('target_count'))}",
        f"- chunk_count：{_safe_int(latest_chunked.get('chunk_count'))}",
        f"- chunk_failed_count：{_safe_int(latest_chunked.get('chunk_failed_count'))}",
        f"- rerun_targets_count：{_safe_int(latest_chunked.get('rerun_targets_count'))}",
        f"- detail_meaningful_count：{_safe_int(latest_chunked.get('detail_meaningful_count'))}",
        f"- comment_meaningful_count：{_safe_int(latest_chunked.get('comment_meaningful_count'))}",
    ]
    return "\n".join(lines).strip() + "\n"


def _build_summary_block_from_chunked_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if _safe_text(payload.get("mode")) != "phase1_chunked":
        return {}

    chunks = [item for item in list(payload.get("chunks") or []) if isinstance(item, Mapping)]
    if not chunks:
        return {}

    account_summary: list[dict[str, Any]] = []
    for chunk in chunks:
        for account in list(chunk.get("account_summary") or []):
            if isinstance(account, Mapping):
                account_summary.append(dict(account))

    summary = _as_dict(payload.get("summary"))
    global_summary = {
        "target_count": _safe_int(payload.get("targets_loaded")),
        "video_total": _safe_int(summary.get("video_total")),
        "detail_attempted": sum(_safe_int(item.get("detail_attempted")) for item in chunks),
        "detail_success_count": sum(
            _safe_int(item.get("detail_success_count"))
            for item in (_as_dict(chunk) for chunk in chunks)
        ),
        "detail_success_rate": 0,
        "comment_attempted": sum(_safe_int(item.get("comment_attempted")) for item in chunks),
        "comment_success_count": sum(
            _safe_int(item.get("comment_success_count"))
            for item in (_as_dict(chunk) for chunk in chunks)
        ),
        "comment_success_rate": 0,
        "detail_meaningful_count": _safe_int(summary.get("detail_meaningful_count")),
        "comment_meaningful_count": _safe_int(summary.get("comment_meaningful_count")),
        "failed_count": _safe_int(summary.get("failed_count")),
        "chunk_count": _safe_int(payload.get("chunk_count")) or len(chunks),
        "chunk_success_count": _safe_int(summary.get("chunk_success_count"))
        or sum(1 for item in chunks if _safe_text(item.get("status")) == "success"),
        "chunk_failed_count": _safe_int(summary.get("chunk_failed_count"))
        or sum(1 for item in chunks if _safe_text(item.get("status")) == "failed"),
        "total_duration_sec": summary.get("total_duration_sec", 0),
    }
    if global_summary["detail_attempted"] > 0:
        global_summary["detail_success_rate"] = round(
            global_summary["detail_success_count"] / global_summary["detail_attempted"], 6
        )
    if global_summary["comment_attempted"] > 0:
        global_summary["comment_success_rate"] = round(
            global_summary["comment_success_count"] / global_summary["comment_attempted"], 6
        )
    return {
        "account_summary": account_summary,
        "global_summary": global_summary,
    }


def _extract_chunked_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    if _safe_text(payload.get("mode")) != "phase1_chunked":
        return {}

    context: dict[str, Any] = {}
    for key in ("failed_chunks", "slowest_chunks", "rerun_priority_chunks", "rerun_command_example", "rerun_manifest_path"):
        value = payload.get(key)
        if value:
            context[key] = value
    return context
