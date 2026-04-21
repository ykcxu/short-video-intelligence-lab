from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def detect_ytdlp() -> dict[str, Any]:
    candidates: list[tuple[list[str], str]] = []

    exe_path = shutil.which("yt-dlp")
    if exe_path:
        candidates.append(([exe_path, "--version"], exe_path))

    candidates.append(([sys.executable, "-m", "yt_dlp", "--version"], f"{sys.executable} -m yt_dlp"))

    errors: list[str] = []
    for command, label in candidates:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            errors.append(f"{label}: {exc}")
            continue

        version = _first_nonempty(completed.stdout, completed.stderr).strip()
        if completed.returncode == 0 and version:
            return {
                "available": True,
                "command": label,
                "command_args": command[:-1],
                "version": version,
                "returncode": completed.returncode,
            }

        errors.append(
            f"{label}: returncode={completed.returncode}, stderr={_first_nonempty(completed.stderr)}"
        )

    return {
        "available": False,
        "command": None,
        "command_args": None,
        "version": None,
        "returncode": None,
        "error": " ; ".join(errors) if errors else "yt-dlp not found",
    }


def run_ytdlp_download(video_url: str, output_path: str | Path) -> dict[str, Any]:
    requested_output_path = Path(output_path)
    result: dict[str, Any] = {
        "status": "failed",
        "downloader": "ytdlp",
        "output_path": str(requested_output_path),
        "file_size": 0,
        "fallback_used": False,
    }

    if not _normalize_text(video_url):
        result["error"] = "video_url is empty"
        return result

    if not str(requested_output_path).strip():
        result["error"] = "output_path is empty"
        return result

    detection = detect_ytdlp()
    if not detection.get("available"):
        result["error"] = detection.get("error", "yt-dlp is unavailable")
        return result

    command_args = detection.get("command_args")
    if not isinstance(command_args, list) or not command_args:
        result["error"] = "yt-dlp detected, but command arguments are unavailable"
        return result

    command = _build_download_command(video_url, requested_output_path, command_args)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        result["error"] = str(exc)
        return result

    if completed.returncode != 0:
        result["error"] = _compose_process_error(completed)
        return result

    actual_path = _locate_downloaded_file(requested_output_path)
    if actual_path is None:
        result["error"] = "yt-dlp completed successfully, but no output file was found"
        return result

    result.update(
        {
            "status": "success",
            "output_path": str(actual_path),
            "file_size": actual_path.stat().st_size,
            "command": command,
            "ytdlp_version": detection.get("version"),
        }
    )
    return result


def _build_download_command(video_url: str, output_path: Path, command_args: list[str]) -> list[str]:
    return [
        *command_args,
        "--no-playlist",
        "--newline",
        "-o",
        str(output_path),
        video_url,
    ]


def _locate_downloaded_file(requested_output_path: Path) -> Path | None:
    if requested_output_path.exists() and requested_output_path.is_file():
        return requested_output_path

    if requested_output_path.exists() and requested_output_path.is_dir():
        candidates = sorted(
            (path for path in requested_output_path.iterdir() if path.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]

    parent = requested_output_path.parent
    stem = requested_output_path.stem
    if parent.exists():
        candidates = sorted(
            (path for path in parent.glob(f"{stem}*") if path.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]

    return None


def _compose_process_error(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = _first_nonempty(completed.stderr, completed.stdout).strip()
    if stderr:
        return f"returncode={completed.returncode}, {stderr}"
    return f"returncode={completed.returncode}"


def _first_nonempty(*values: str | None) -> str:
    for value in values:
        if value and str(value).strip():
            return str(value)
    return ""


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
