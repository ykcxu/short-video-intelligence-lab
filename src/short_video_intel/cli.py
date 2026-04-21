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

    return parser


def _cmd_bootstrap(orchestrator: Orchestrator, _args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.bootstrap()


def _cmd_init_db(orchestrator: Orchestrator, _args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.init_db()


def _cmd_import_targets(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.import_targets(args.input, input_format=args.format)


def _cmd_session_init(orchestrator: Orchestrator, args: argparse.Namespace) -> dict[str, Any]:
    return orchestrator.session_init(args.session_name)


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
