from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .analysis.reporting import (
    analyze_positive_factors,
    analyze_video_fit_from_file,
    analyze_video_fit_from_full_batch,
)
from .browser.session_manager import INVALID_SESSION_CHARS
from .config import load_config
from .orchestrator import Orchestrator


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="short-video-intel")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional config file path (default: ./config.yaml).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Optional workspace root path (default: current directory).",
    )
    parser.add_argument(
        "--session-name",
        dest="session_name_override",
        default=None,
        help="Optional session name override for browser.storage_state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "bootstrap",
        help="Create the core workspace directories and print the resolved paths.",
    ).set_defaults(func=_cmd_bootstrap)

    subparsers.add_parser(
        "init-db",
        help="Initialize the database backend through the orchestrator/db adapter.",
    ).set_defaults(func=_cmd_init_db)

    import_parser = subparsers.add_parser(
        "import-targets",
        help="Import homepage target lists from CSV or JSON.",
    )
    import_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to the CSV/JSON target file.",
    )
    import_parser.add_argument(
        "--format",
        choices=("auto", "csv", "tsv", "json"),
        default="auto",
        help="Override input format detection.",
    )
    import_parser.set_defaults(func=_cmd_import_targets)

    session_parser = subparsers.add_parser(
        "session-init",
        help="Create a placeholder browser session state file.",
    )
    session_parser.add_argument(
        "--session-name",
        required=True,
        help="Logical session name used to name the state file.",
    )
    session_parser.set_defaults(func=_cmd_session_init)

    session_capture_parser = subparsers.add_parser(
        "session-capture",
        help="Open a browser window for manual login and capture storage_state.",
    )
    session_capture_parser.add_argument(
        "--session-name",
        required=True,
        help="Logical session name used to name the captured state file.",
    )
    session_capture_parser.add_argument(
        "--homepage-url",
        default="https://www.douyin.com/",
        help="Homepage URL to open before waiting for manual login.",
    )
    session_capture_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="Seconds to wait before saving the storage_state.",
    )
    session_capture_parser.set_defaults(func=_cmd_session_capture)

    crawl_homepage_parser = subparsers.add_parser(
        "crawl-homepage",
        help="Run homepage collection skeleton for one homepage URL.",
    )
    crawl_homepage_parser.add_argument("--homepage-url", required=True, help="Douyin homepage URL.")
    crawl_homepage_parser.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Target max items for collection (skeleton keeps this as a hint).",
    )
    crawl_homepage_parser.set_defaults(func=_cmd_crawl_homepage)

    crawl_video_parser = subparsers.add_parser(
        "crawl-video-detail",
        help="Run video detail collection skeleton for one video URL.",
    )
    crawl_video_parser.add_argument("--video-url", required=True, help="Douyin video URL.")
    crawl_video_parser.set_defaults(func=_cmd_crawl_video_detail)

    crawl_comments_parser = subparsers.add_parser(
        "crawl-video-comments",
        help="Run video comments collection skeleton for one video URL.",
    )
    crawl_comments_parser.add_argument("--video-url", required=True, help="Douyin video URL.")
    crawl_comments_parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Maximum pages to probe (skeleton placeholder).",
    )
    crawl_comments_parser.set_defaults(func=_cmd_crawl_video_comments)

    download_jobs_parser = subparsers.add_parser(
        "build-download-jobs",
        help="Build downloader stub jobs from a JSON video list and optionally run them.",
    )
    download_jobs_parser.add_argument(
        "--videos-file",
        required=True,
        type=Path,
        help="Path to JSON file containing a list of video objects (with video_url).",
    )
    download_jobs_parser.add_argument(
        "--output-dir",
        default=None,
        type=Path,
        help="Optional output directory for downloader stub artifacts.",
    )
    download_jobs_parser.add_argument(
        "--run",
        action="store_true",
        help="Execute the generated stub download jobs immediately.",
    )
    download_jobs_parser.set_defaults(func=_cmd_build_download_jobs)

    batch_parser = subparsers.add_parser(
        "crawl-targets-batch",
        help="Batch crawl homepage targets from file or DB.",
    )
    batch_parser.add_argument(
        "--source-file",
        type=Path,
        default=None,
        help="Target source file (csv/tsv/json). Required unless --from-db is set.",
    )
    batch_parser.add_argument(
        "--format",
        choices=("auto", "csv", "tsv", "json"),
        default="auto",
        help="Input format for --source-file.",
    )
    batch_parser.add_argument(
        "--from-db",
        action="store_true",
        help="Load targets from DB instead of file.",
    )
    batch_parser.add_argument(
        "--status",
        default="active",
        help="DB mode: filter by target status.",
    )
    batch_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="DB mode: optional limit.",
    )
    batch_parser.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Per homepage max extracted videos.",
    )
    batch_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Batch worker count (ThreadPool).",
    )
    batch_parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Persist batch crawl results to DB via db.upsert helpers.",
    )
    batch_parser.set_defaults(func=_cmd_crawl_targets_batch)

    full_batch_parser = subparsers.add_parser(
        "crawl-targets-full-batch",
        help="Batch crawl homepage targets and optionally enrich videos with detail/comments.",
    )
    full_batch_parser.add_argument(
        "--source-file",
        type=Path,
        default=None,
        help="Target source file (csv/tsv/json). Required unless --from-db is set.",
    )
    full_batch_parser.add_argument(
        "--format",
        choices=("auto", "csv", "tsv", "json"),
        default="auto",
        help="Input format for --source-file.",
    )
    full_batch_parser.add_argument(
        "--from-db",
        action="store_true",
        help="Load targets from DB instead of file.",
    )
    full_batch_parser.add_argument(
        "--status",
        default="active",
        help="DB mode: filter by target status.",
    )
    full_batch_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="DB mode: optional limit.",
    )
    full_batch_parser.add_argument(
        "--with-video-detail",
        action="store_true",
        help="Collect a video detail payload for each extracted video candidate.",
    )
    full_batch_parser.add_argument(
        "--with-comments",
        action="store_true",
        help="Collect a comment scan payload for each extracted video candidate.",
    )
    full_batch_parser.add_argument(
        "--comment-pages",
        type=int,
        default=3,
        help="Requested comment pagination depth when --with-comments is enabled.",
    )
    full_batch_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Batch worker count (ThreadPool).",
    )
    full_batch_parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Persist batch crawl results to DB via db.upsert helpers.",
    )
    full_batch_parser.set_defaults(func=_cmd_crawl_targets_full_batch)

    analysis_parser = subparsers.add_parser(
        "analyze-positive-factors",
        help="Score positive factors from a full-batch artifact and export recommendations.",
    )
    analysis_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to a full-batch JSON artifact (default: latest under artifacts/collector/full-batch).",
    )
    analysis_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save the analysis result as JSON.",
    )
    analysis_parser.set_defaults(func=_cmd_analyze_positive_factors)

    video_fit_parser = subparsers.add_parser(
        "analyze-video-fit",
        help="Analyze whether video detail payloads fit account growth strategy.",
    )
    video_fit_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to JSON file. Accepts one video detail object or a list of batch items.",
    )
    video_fit_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save fit analysis result JSON.",
    )
    video_fit_parser.set_defaults(func=_cmd_analyze_video_fit)

    video_fit_full_batch_parser = subparsers.add_parser(
        "analyze-video-fit-full-batch",
        help="Analyze video fit directly from full-batch crawl artifact.",
    )
    video_fit_full_batch_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to full-batch artifact JSON (default: latest under artifacts/collector/full-batch).",
    )
    video_fit_full_batch_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save fit analysis JSON.",
    )
    video_fit_full_batch_parser.set_defaults(func=_cmd_analyze_video_fit_full_batch)

    return parser


def _cmd_bootstrap(orchestrator: Orchestrator, _args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.bootstrap()


def _cmd_init_db(orchestrator: Orchestrator, _args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.init_db()


def _cmd_import_targets(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.import_targets(args.input, input_format=args.format)


def _cmd_session_init(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.session_init(args.session_name)


def _cmd_session_capture(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.session_capture(
        args.session_name,
        homepage_url=args.homepage_url,
        wait_seconds=args.wait_seconds,
    )


def _cmd_crawl_homepage(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_homepage(args.homepage_url, max_items=args.max_items)


def _cmd_crawl_video_detail(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_video_detail(args.video_url)


def _cmd_crawl_video_comments(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_video_comments(args.video_url, max_pages=args.max_pages)


def _cmd_build_download_jobs(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.create_download_jobs(
        videos_file=args.videos_file,
        output_dir=args.output_dir,
        run=args.run,
    )


def _cmd_crawl_targets_batch(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_targets_batch(
        source_file=args.source_file,
        input_format=args.format,
        from_db=args.from_db,
        status=args.status,
        limit=args.limit,
        max_items=args.max_items,
        max_workers=args.workers,
        persist_db=args.persist_db,
    )


def _cmd_crawl_targets_full_batch(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_targets_full_batch(
        source_file=args.source_file,
        input_format=args.format,
        from_db=args.from_db,
        status=args.status,
        limit=args.limit,
        max_workers=args.workers,
        with_video_detail=args.with_video_detail,
        with_comments=args.with_comments,
        comment_pages=args.comment_pages,
        persist_db=args.persist_db,
    )


def _cmd_analyze_positive_factors(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return analyze_positive_factors(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
        output=args.output,
    )


def _cmd_analyze_video_fit(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return analyze_video_fit_from_file(
        workspace=orchestrator.config.workspace,
        input_path=args.input,
        output=args.output,
    )


def _cmd_analyze_video_fit_full_batch(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return analyze_video_fit_from_full_batch(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
        output=args.output,
    )


def _print_result(result: dict[str, Any]) -> None:
    notice = result.pop("notice", None)
    if notice:
        print(notice, file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _normalize_session_name_cli(session_name: str) -> str:
    cleaned = str(session_name).strip()
    if not cleaned:
        raise ValueError("session_name must not be empty")
    cleaned = INVALID_SESSION_CHARS.sub("_", cleaned)
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        raise ValueError("session_name becomes empty after sanitization")
    return cleaned


def _merge_notice(existing: Any, appended: str) -> str:
    if existing is None:
        return appended
    existing_text = str(existing).strip()
    if not existing_text:
        return appended
    return f"{existing_text}\n{appended}"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        orchestrator = Orchestrator(load_config(path=args.config, workspace=args.workspace))

        if args.session_name_override is not None:
            normalized_session_name = _normalize_session_name_cli(args.session_name_override)
            session_state_path = (
                orchestrator.config.workspace / "data" / "sessions" / normalized_session_name / "state.json"
            )
            orchestrator.config.browser.storage_state = session_state_path

        result = args.func(orchestrator, args)
        if args.session_name_override is not None:
            notice = (
                f"[session-override] enabled: browser.storage_state -> {orchestrator.config.browser.storage_state}"
            )
            result["notice"] = _merge_notice(result.get("notice"), notice)
    except Exception as exc:  # pragma: no cover - defensive CLI guard
        result = {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }

    _print_result(result)
    if isinstance(result, dict) and result.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
