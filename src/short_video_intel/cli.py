from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
        choices=("auto", "csv", "json"),
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

    return parser


def _cmd_bootstrap(orchestrator: Orchestrator, _args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.bootstrap()


def _cmd_init_db(orchestrator: Orchestrator, _args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.init_db()


def _cmd_import_targets(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.import_targets(args.input, input_format=args.format)


def _cmd_session_init(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.session_init(args.session_name)


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


def _print_result(result: dict[str, Any]) -> None:
    notice = result.pop("notice", None)
    if notice:
        print(notice, file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    orchestrator = Orchestrator(load_config(path=args.config, workspace=args.workspace))

    try:
        result = args.func(orchestrator, args)
    except Exception as exc:  # pragma: no cover - defensive CLI guard
        parser.exit(status=1, message=f"error: {exc}\n")

    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
