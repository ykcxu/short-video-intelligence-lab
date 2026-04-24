from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def download_via_http(
    media_url: str,
    output_path: str | Path,
    *,
    referer: str | None = None,
    user_agent: str | None = None,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    headers = {
        "Accept": "*/*",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = str(referer)
    if user_agent:
        headers["User-Agent"] = str(user_agent)

    request = Request(str(media_url), headers=headers)
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            content_type = str(response.headers.get("Content-Type") or "")
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
            return {
                "status": "success",
                "downloader": "browser_http",
                "media_url": str(media_url),
                "output_path": str(destination),
                "file_size": destination.stat().st_size if destination.exists() else 0,
                "content_type": content_type,
                "fallback_used": False,
            }
    except HTTPError as exc:
        return {
            "status": "failed",
            "downloader": "browser_http",
            "media_url": str(media_url),
            "output_path": str(destination),
            "file_size": 0,
            "error": f"HTTPError {exc.code}: {exc.reason}",
            "fallback_used": False,
        }
    except URLError as exc:
        return {
            "status": "failed",
            "downloader": "browser_http",
            "media_url": str(media_url),
            "output_path": str(destination),
            "file_size": 0,
            "error": f"URLError: {exc.reason}",
            "fallback_used": False,
        }
    except Exception as exc:  # pragma: no cover
        return {
            "status": "failed",
            "downloader": "browser_http",
            "media_url": str(media_url),
            "output_path": str(destination),
            "file_size": 0,
            "error": str(exc),
            "fallback_used": False,
        }

