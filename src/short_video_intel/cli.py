from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .analysis import reporting as reporting_module
from .analysis.reporting import (
    analyze_positive_factors,
    build_project_progress_dashboard,
    export_phase1_rerun_manifest,
    generate_phase1_chunked_report,
    get_phase1_status_overview,
    list_phase1_recent_runs,
    summarize_homepage_batch,
    analyze_video_fit_from_file,
    analyze_video_fit_from_full_batch,
)
from .analysis.local_video_inputs import prepare_local_video_analysis_inputs
from .analysis.local_video_fit import analyze_local_video_inputs_file
from .analysis.multimodal_inputs import prepare_multimodal_inputs
from .analysis.multimodal_fusion import analyze_multimodal_inputs_file
from .browser.session_manager import INVALID_SESSION_CHARS
from .config import load_config
from .orchestrator import Orchestrator

generate_weekly_report_from_full_batch = getattr(reporting_module, "generate_weekly_report_from_full_batch", None)


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

    debug_homepage_parser = subparsers.add_parser(
        "open-debug-homepage",
        help="Open a debug browser with remote debugging enabled for manual page preparation.",
    )
    debug_homepage_parser.add_argument("--session-name", required=True, help="Session name to load storage_state from.")
    debug_homepage_parser.add_argument("--homepage-url", required=True, help="Homepage URL to open.")
    debug_homepage_parser.add_argument("--cdp-port", type=int, default=9222, help="Remote debugging port.")
    debug_homepage_parser.add_argument("--hold-seconds", type=int, default=1800, help="How long to keep the browser open.")
    debug_homepage_parser.set_defaults(func=_cmd_open_debug_homepage)

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

    crawl_homepage_cdp_parser = subparsers.add_parser(
        "crawl-homepage-cdp",
        help="Capture a homepage from an already-open Chromium debug page via CDP.",
    )
    crawl_homepage_cdp_parser.add_argument("--homepage-url", required=True, help="Douyin homepage URL.")
    crawl_homepage_cdp_parser.add_argument("--cdp-url", default="http://127.0.0.1:9222", help="CDP endpoint URL.")
    crawl_homepage_cdp_parser.add_argument("--max-items", type=int, default=50, help="Target max items for extraction.")
    crawl_homepage_cdp_parser.set_defaults(func=_cmd_crawl_homepage_cdp)

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
    download_jobs_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Download worker count when --run is enabled.",
    )
    download_jobs_parser.set_defaults(func=_cmd_build_download_jobs)

    download_jobs_from_artifact_parser = subparsers.add_parser(
        "build-download-jobs-from-artifact",
        help="Extract videos from homepage/full-batch artifacts and build download jobs.",
    )
    download_jobs_from_artifact_parser.add_argument(
        "--artifact",
        required=True,
        type=Path,
        help="Collector artifact path: homepage, batch, full-batch, or phase1 chunked master/chunk.",
    )
    download_jobs_from_artifact_parser.add_argument(
        "--output-dir",
        default=None,
        type=Path,
        help="Optional output directory for generated download jobs and downloaded files.",
    )
    download_jobs_from_artifact_parser.add_argument(
        "--max-videos",
        type=int,
        default=None,
        help="Optional cap on extracted videos before building jobs.",
    )
    download_jobs_from_artifact_parser.add_argument(
        "--run",
        action="store_true",
        help="Execute generated download jobs immediately.",
    )
    download_jobs_from_artifact_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Download worker count when --run is enabled.",
    )
    download_jobs_from_artifact_parser.set_defaults(func=_cmd_build_download_jobs_from_artifact)

    run_download_jobs_parser = subparsers.add_parser(
        "run-download-jobs",
        help="Execute a previously generated download jobs artifact.",
    )
    run_download_jobs_parser.add_argument(
        "--jobs-file",
        required=True,
        type=Path,
        help="Path to a download jobs JSON artifact.",
    )
    run_download_jobs_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Download worker count.",
    )
    run_download_jobs_parser.set_defaults(func=_cmd_run_download_jobs_file)

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
    full_batch_parser.add_argument(
        "--video-limit-per-target",
        type=int,
        default=None,
        help="Optional cap on how many videos per homepage will run detail collection.",
    )
    full_batch_parser.add_argument(
        "--comment-video-limit-per-target",
        type=int,
        default=None,
        help="Optional cap on how many videos per homepage will run comment collection.",
    )
    full_batch_parser.set_defaults(func=_cmd_crawl_targets_full_batch)

    phase1_batch_parser = subparsers.add_parser(
        "run-phase1-batch",
        help="Run phase1 batch crawl with full enrichments and DB persistence.",
    )
    phase1_batch_parser.add_argument(
        "--source-file",
        type=Path,
        default=None,
        help="Target source file (csv/tsv/json). If omitted, defaults to DB mode.",
    )
    phase1_batch_parser.add_argument(
        "--format",
        choices=("auto", "csv", "tsv", "json"),
        default="auto",
        help="Input format for --source-file.",
    )
    phase1_batch_parser.add_argument(
        "--from-db",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Whether to load targets from DB. Defaults to true when --source-file is omitted.",
    )
    phase1_batch_parser.add_argument(
        "--status",
        default="active",
        help="DB mode: filter by target status.",
    )
    phase1_batch_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="DB mode: optional limit.",
    )
    phase1_batch_parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Batch worker count (ThreadPool).",
    )
    phase1_batch_parser.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Reserved compatibility argument for phase1 runs.",
    )
    phase1_batch_parser.add_argument(
        "--comment-pages",
        type=int,
        default=2,
        help="Requested comment pagination depth.",
    )
    phase1_batch_parser.add_argument(
        "--video-limit-per-target",
        type=int,
        default=10,
        help="Slow-net friendly cap: only enrich the first N videos per homepage for detail.",
    )
    phase1_batch_parser.add_argument(
        "--comment-video-limit-per-target",
        type=int,
        default=5,
        help="Slow-net friendly cap: only collect comments for the first N videos per homepage.",
    )
    phase1_batch_parser.add_argument(
        "--browser-timeout-ms",
        type=int,
        default=None,
        help="Optional temporary override for browser timeout during this run.",
    )
    phase1_batch_parser.add_argument(
        "--session-name",
        dest="command_session_name",
        default=None,
        help="Command-level session override (higher priority than global --session-name).",
    )
    phase1_batch_parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Optional: split targets into small batches of N accounts and emit one artifact per chunk.",
    )
    phase1_batch_parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.0,
        help="Optional pause between chunks for slow network environments.",
    )
    phase1_batch_parser.set_defaults(func=_cmd_run_phase1_batch)

    weekly_report_parser = subparsers.add_parser(
        "generate-weekly-report",
        help="Generate weekly report from full-batch artifact.",
    )
    weekly_report_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to full-batch artifact JSON (default: latest under artifacts/collector/full-batch).",
    )
    weekly_report_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write JSON report payload.",
    )
    weekly_report_parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional path to write Markdown report.",
    )
    weekly_report_parser.set_defaults(func=_cmd_generate_weekly_report)

    phase1_chunked_report_parser = subparsers.add_parser(
        "generate-phase1-chunked-report",
        help="Generate operations report from a phase1_chunked master artifact.",
    )
    phase1_chunked_report_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to phase1_chunked master artifact JSON (default: latest chunked master under artifacts/collector/full-batch).",
    )
    phase1_chunked_report_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write JSON report payload.",
    )
    phase1_chunked_report_parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional path to write Markdown report.",
    )
    phase1_chunked_report_parser.set_defaults(func=_cmd_generate_phase1_chunked_report)

    export_rerun_parser = subparsers.add_parser(
        "export-phase1-rerun-manifest",
        help="Export failed targets from a phase1_chunked master artifact into a rerun manifest JSON.",
    )
    export_rerun_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to phase1_chunked master artifact JSON (default: latest chunked master under artifacts/collector/full-batch).",
    )
    export_rerun_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save the rerun manifest JSON.",
    )
    export_rerun_parser.set_defaults(func=_cmd_export_phase1_rerun_manifest)

    phase1_status_parser = subparsers.add_parser(
        "phase1-status-overview",
        help="Show a compact overview of the latest full-batch and phase1_chunked artifacts.",
    )
    phase1_status_parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional path to write Markdown overview.",
    )
    phase1_status_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write JSON overview.",
    )
    phase1_status_parser.set_defaults(func=_cmd_phase1_status_overview)

    project_progress_parser = subparsers.add_parser(
        "project-progress",
        help="Show a dashboard-style progress view for downloads, detail, and comments.",
    )
    project_progress_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write JSON progress output.",
    )
    project_progress_parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional path to write Markdown progress output.",
    )
    project_progress_parser.add_argument(
        "--download-target-per-account",
        type=int,
        default=50,
        help="Target number of downloaded videos per account used for the progress bars.",
    )
    project_progress_parser.set_defaults(func=_cmd_project_progress)

    recent_runs_parser = subparsers.add_parser(
        "phase1-recent-runs",
        help="List recent phase1-related artifacts across collector and analysis directories.",
    )
    recent_runs_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of recent items to return.",
    )
    recent_runs_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write JSON overview.",
    )
    recent_runs_parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional path to write Markdown overview.",
    )
    recent_runs_parser.set_defaults(func=_cmd_phase1_recent_runs)

    homepage_summary_parser = subparsers.add_parser(
        "summarize-homepage-batch",
        help="Summarize a homepage batch artifact into an account-level result table.",
    )
    homepage_summary_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to homepage batch artifact JSON (default: latest under artifacts/collector/batch).",
    )
    homepage_summary_parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write JSON summary.",
    )
    homepage_summary_parser.add_argument(
        "--md-output",
        type=Path,
        default=None,
        help="Optional path to write Markdown summary.",
    )
    homepage_summary_parser.set_defaults(func=_cmd_summarize_homepage_batch)

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

    local_video_inputs_parser = subparsers.add_parser(
        "prepare-local-video-inputs",
        help="Build local analysis-ready manifest from downloaded video result artifacts.",
    )
    local_video_inputs_parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to downloader results artifact JSON (default: latest under artifacts/downloader/results).",
    )
    local_video_inputs_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save local video input manifest JSON.",
    )
    local_video_inputs_parser.add_argument(
        "--frames-per-video",
        type=int,
        default=3,
        help="How many sample frames to extract per valid video.",
    )
    local_video_inputs_parser.set_defaults(func=_cmd_prepare_local_video_inputs)

    local_video_fit_parser = subparsers.add_parser(
        "analyze-local-video-fit",
        help="Analyze local video manifests generated from downloaded mp4 files.",
    )
    local_video_fit_parser.add_argument(
        "--artifact",
        required=True,
        type=Path,
        help="Path to local_video_inputs manifest JSON.",
    )
    local_video_fit_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save local video fit result JSON.",
    )
    local_video_fit_parser.set_defaults(func=_cmd_analyze_local_video_fit)

    multimodal_fit_parser = subparsers.add_parser(
        "analyze-multimodal-fit",
        help="Analyze multimodal feature artifact and produce account-fit suggestions.",
    )
    multimodal_fit_parser.add_argument(
        "--artifact",
        required=True,
        type=Path,
        help="Path to multimodal feature artifact JSON.",
    )
    multimodal_fit_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save multimodal fusion result JSON.",
    )
    multimodal_fit_parser.set_defaults(func=_cmd_analyze_multimodal_fit)

    multimodal_inputs_parser = subparsers.add_parser(
        "prepare-multimodal-inputs",
        help="Merge local video fit results with per-video multimodal feature JSON files.",
    )
    multimodal_inputs_parser.add_argument(
        "--local-fit-artifact",
        required=True,
        type=Path,
        help="Path to local video fit result JSON.",
    )
    multimodal_inputs_parser.add_argument(
        "--features-dir",
        type=Path,
        default=None,
        help="Directory containing per-video feature JSON files named by video_id.",
    )
    multimodal_inputs_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save multimodal input artifact JSON.",
    )
    multimodal_inputs_parser.set_defaults(func=_cmd_prepare_multimodal_inputs)

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


def _cmd_open_debug_homepage(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.open_debug_homepage(
        args.session_name,
        homepage_url=args.homepage_url,
        cdp_port=args.cdp_port,
        hold_seconds=args.hold_seconds,
    )


def _cmd_crawl_homepage(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_homepage(args.homepage_url, max_items=args.max_items)


def _cmd_crawl_homepage_cdp(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_homepage_via_cdp(
        args.homepage_url,
        cdp_url=args.cdp_url,
        max_items=args.max_items,
    )


def _cmd_crawl_video_detail(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_video_detail(args.video_url)


def _cmd_crawl_video_comments(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.crawl_video_comments(args.video_url, max_pages=args.max_pages)


def _cmd_build_download_jobs(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.create_download_jobs(
        videos_file=args.videos_file,
        output_dir=args.output_dir,
        run=args.run,
        max_workers=args.workers,
    )


def _cmd_build_download_jobs_from_artifact(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.create_download_jobs_from_artifact(
        artifact_path=args.artifact,
        output_dir=args.output_dir,
        run=args.run,
        max_videos=args.max_videos,
        max_workers=args.workers,
    )


def _cmd_run_download_jobs_file(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.run_download_jobs_file(
        jobs_file=args.jobs_file,
        max_workers=args.workers,
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
        video_limit_per_target=args.video_limit_per_target,
        comment_video_limit_per_target=args.comment_video_limit_per_target,
    )


def _cmd_run_phase1_batch(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    from_db = args.from_db if args.from_db is not None else args.source_file is None
    original_timeout = orchestrator.config.browser.timeout_ms
    try:
        if args.browser_timeout_ms is not None and args.browser_timeout_ms > 0:
            orchestrator.config.browser.timeout_ms = args.browser_timeout_ms
        if args.chunk_size is not None and args.chunk_size > 0:
            return orchestrator.run_phase1_chunked(
                source_file=args.source_file,
                input_format=args.format,
                from_db=from_db,
                status=args.status,
                limit=args.limit,
                max_items=args.max_items,
                max_workers=args.workers,
                comment_pages=args.comment_pages,
                persist_db=True,
                video_limit_per_target=args.video_limit_per_target,
                comment_video_limit_per_target=args.comment_video_limit_per_target,
                chunk_size=args.chunk_size,
                pause_seconds=args.pause_seconds,
            )
        return orchestrator.crawl_targets_full_batch(
            source_file=args.source_file,
            input_format=args.format,
            from_db=from_db,
            status=args.status,
            limit=args.limit,
            max_workers=args.workers,
            with_video_detail=True,
            with_comments=True,
            comment_pages=args.comment_pages,
            persist_db=True,
            video_limit_per_target=args.video_limit_per_target,
            comment_video_limit_per_target=args.comment_video_limit_per_target,
        )
    finally:
        orchestrator.config.browser.timeout_ms = original_timeout


def _cmd_generate_weekly_report(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    if generate_weekly_report_from_full_batch is None:
        return {
            "ok": False,
            "error": {
                "type": "NotImplementedError",
                "message": "generate_weekly_report_from_full_batch is not available in analysis.reporting",
            },
        }

    result = generate_weekly_report_from_full_batch(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
    )

    if not isinstance(result, dict):
        return {
            "ok": False,
            "error": {
                "type": "TypeError",
                "message": "generate_weekly_report_from_full_batch must return a dict",
            },
        }

    if args.json_output is not None:
        json_output_path = _resolve_cli_output_path(orchestrator, args.json_output)
        with json_output_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        result["json_output_path"] = str(json_output_path)

    if args.md_output is not None:
        markdown_content = _extract_markdown_report(result)
        if markdown_content is None:
            return {
                "ok": False,
                "error": {
                    "type": "ValueError",
                    "message": "markdown output requested but report markdown text not found in result",
                },
            }
        md_output_path = _resolve_cli_output_path(orchestrator, args.md_output)
        md_output_path.write_text(markdown_content, encoding="utf-8")
        result["md_output_path"] = str(md_output_path)

    return result


def _cmd_generate_phase1_chunked_report(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    result = generate_phase1_chunked_report(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
    )

    if args.json_output is not None:
        json_output_path = _resolve_cli_output_path(orchestrator, args.json_output)
        with json_output_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        result["json_output_path"] = str(json_output_path)

    if args.md_output is not None:
        markdown_content = _extract_markdown_report(result)
        if markdown_content is None:
            return {
                "ok": False,
                "error": {
                    "type": "ValueError",
                    "message": "markdown output requested but report markdown text not found in result",
                },
            }
        md_output_path = _resolve_cli_output_path(orchestrator, args.md_output)
        md_output_path.write_text(markdown_content, encoding="utf-8")
        result["md_output_path"] = str(md_output_path)

    return result


def _cmd_export_phase1_rerun_manifest(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return export_phase1_rerun_manifest(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
        output=args.output,
    )


def _cmd_phase1_status_overview(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    result = get_phase1_status_overview(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
    )
    if args.json_output is not None:
        json_output_path = _resolve_cli_output_path(orchestrator, args.json_output)
        with json_output_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        result["json_output_path"] = str(json_output_path)
    if args.md_output is not None:
        markdown_content = _extract_markdown_report(result)
        if markdown_content is None:
            return {
                "ok": False,
                "error": {
                    "type": "ValueError",
                    "message": "markdown output requested but overview markdown text not found in result",
                },
            }
        md_output_path = _resolve_cli_output_path(orchestrator, args.md_output)
        md_output_path.write_text(markdown_content, encoding="utf-8")
        result["md_output_path"] = str(md_output_path)
    return result


def _cmd_phase1_recent_runs(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    result = list_phase1_recent_runs(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        limit=args.limit,
    )
    if args.json_output is not None:
        json_output_path = _resolve_cli_output_path(orchestrator, args.json_output)
        with json_output_path.open("w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        result["json_output_path"] = str(json_output_path)
    if args.md_output is not None:
        markdown_content = _extract_markdown_report(result)
        if markdown_content is None:
            return {
                "ok": False,
                "error": {
                    "type": "ValueError",
                    "message": "markdown output requested but recent-runs markdown text not found in result",
                },
            }
        md_output_path = _resolve_cli_output_path(orchestrator, args.md_output)
        md_output_path.write_text(markdown_content, encoding="utf-8")
        result["md_output_path"] = str(md_output_path)
    return result


def _cmd_project_progress(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    result = build_project_progress_dashboard(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        download_target_per_account=int(args.download_target_per_account),
    )
    if not result.get("ok"):
        return result

    if args.json_output is not None:
        output_path = orchestrator.config.workspace / args.json_output if not args.json_output.is_absolute() else args.json_output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["json_output_path"] = str(output_path)

    if args.md_output is not None:
        output_path = orchestrator.config.workspace / args.md_output if not args.md_output.is_absolute() else args.md_output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(str(result.get("markdown") or ""), encoding="utf-8")
        result["md_output_path"] = str(output_path)

    return result


def _cmd_summarize_homepage_batch(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    result = summarize_homepage_batch(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
        output=args.json_output,
    )
    if args.md_output is not None:
        markdown_content = _extract_markdown_report(result)
        if markdown_content is None:
            return {
                "ok": False,
                "error": {
                    "type": "ValueError",
                    "message": "markdown output requested but homepage summary markdown text not found in result",
                },
            }
        md_output_path = _resolve_cli_output_path(orchestrator, args.md_output)
        md_output_path.write_text(markdown_content, encoding="utf-8")
        result["md_output_path"] = str(md_output_path)
    return result


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


def _cmd_prepare_local_video_inputs(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return prepare_local_video_analysis_inputs(
        workspace=orchestrator.config.workspace,
        artifacts_dir=orchestrator.config.artifacts_dir,
        artifact=args.artifact,
        output=args.output,
        frames_per_video=args.frames_per_video,
    )


def _cmd_analyze_local_video_fit(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return analyze_local_video_inputs_file(
        workspace=orchestrator.config.workspace,
        artifact=args.artifact,
        output=args.output,
    )


def _cmd_analyze_multimodal_fit(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return analyze_multimodal_inputs_file(
        workspace=orchestrator.config.workspace,
        artifact=args.artifact,
        output=args.output,
    )


def _cmd_prepare_multimodal_inputs(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return prepare_multimodal_inputs(
        workspace=orchestrator.config.workspace,
        local_fit_artifact=args.local_fit_artifact,
        features_dir=args.features_dir,
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


def _resolve_cli_output_path(orchestrator: Orchestrator, value: Path) -> Path:
    resolved = Path(value).expanduser()
    if not resolved.is_absolute():
        resolved = orchestrator.config.workspace / resolved
    resolved = resolved.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _extract_markdown_report(result: dict[str, Any]) -> str | None:
    markdown_candidates = (
        result.get("markdown"),
        result.get("md"),
        result.get("report_markdown"),
        result.get("markdown_report"),
        result.get("weekly_markdown"),
    )
    for candidate in markdown_candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    report_block = result.get("report")
    if isinstance(report_block, dict):
        for key in ("markdown", "md", "content"):
            value = report_block.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        orchestrator = Orchestrator(load_config(path=args.config, workspace=args.workspace))

        command_session_name = getattr(args, "command_session_name", None)
        session_name_override = command_session_name or args.session_name_override
        if session_name_override is not None:
            normalized_session_name = _normalize_session_name_cli(session_name_override)
            session_state_path = (
                orchestrator.config.workspace / "data" / "sessions" / normalized_session_name / "state.json"
            )
            orchestrator.config.browser.storage_state = session_state_path

        result = args.func(orchestrator, args)
        if session_name_override is not None:
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
