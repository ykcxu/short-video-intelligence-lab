from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from ..config import AppConfig

INVALID_SESSION_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def detect_playwright() -> bool:
    return find_spec("playwright") is not None


def capture_session_state(
    config: AppConfig,
    session_name: str,
    homepage_url: str = "https://www.douyin.com/",
    wait_seconds: int = 120,
) -> dict[str, Any]:
    session_name = _normalize_session_name(session_name)
    state_dir = config.data_dir / "sessions" / session_name
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"
    warnings: list[str] = []

    playwright_detected = detect_playwright()
    if not playwright_detected:
        return {
            "ok": False,
            "reason": "no_playwright",
            "session_name": session_name,
            "state_path": str(state_path),
            "mirrored_storage_state": None,
            "playwright_detected": False,
            "warnings": warnings,
        }

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - import guard
        return {
            "ok": False,
            "reason": "no_playwright",
            "session_name": session_name,
            "state_path": str(state_path),
            "mirrored_storage_state": None,
            "playwright_detected": False,
            "warnings": [f"playwright import failed: {type(exc).__name__}: {exc}"],
        }

    mirrored_storage_state: str | None = None
    wait_seconds = max(0, int(wait_seconds))
    storage_state_path = state_path

    try:
        with sync_playwright() as playwright:
            chromium = playwright.chromium
            launch_kwargs = {
                "headless": False,
            }
            browser = None
            user_data_dir = getattr(config.browser, "user_data_dir", None)
            if user_data_dir:
                context = chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    **launch_kwargs,
                )
                page = context.pages[0] if context.pages else context.new_page()
            else:
                browser = chromium.launch(**launch_kwargs)
                context = browser.new_context()
                page = context.new_page()

            try:
                page.goto(homepage_url, wait_until="domcontentloaded", timeout=config.browser.timeout_ms)
            except Exception as exc:  # pragma: no cover - browser runtime safety
                warnings.append(f"page.goto failed: {type(exc).__name__}: {exc}")

            warnings.append(f"waiting {wait_seconds} seconds for manual login")
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            context.storage_state(path=str(storage_state_path))

            try:
                mirrored_storage_state = _mirror_storage_state(config, state_path)
            except Exception as exc:  # pragma: no cover - runtime safety
                warnings.append(f"mirror storage_state failed: {type(exc).__name__}: {exc}")

            try:
                context.close()
            except Exception as exc:  # pragma: no cover - runtime safety
                warnings.append(f"context.close failed: {type(exc).__name__}: {exc}")
            if browser is not None:
                try:
                    browser.close()
                except Exception as exc:  # pragma: no cover - runtime safety
                    warnings.append(f"browser.close failed: {type(exc).__name__}: {exc}")
    except Exception as exc:  # pragma: no cover - structured fallback
        return {
            "ok": False,
            "reason": "session_capture_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "session_name": session_name,
            "state_path": str(state_path),
            "mirrored_storage_state": mirrored_storage_state,
            "playwright_detected": True,
            "warnings": warnings,
        }

    return {
        "ok": True,
        "session_name": session_name,
        "state_path": str(state_path),
        "mirrored_storage_state": mirrored_storage_state,
        "playwright_detected": True,
        "warnings": warnings,
    }


def init_session_state(config: AppConfig, session_name: str) -> dict[str, Any]:
    session_name = _normalize_session_name(session_name)
    sessions_dir = config.data_dir / "sessions" / session_name
    sessions_dir.mkdir(parents=True, exist_ok=True)

    state_path = sessions_dir / "state.json"
    created = False
    if not state_path.exists():
        created = True
        payload = {
            "session_name": session_name,
            "status": "placeholder",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "playwright_detected": detect_playwright(),
            "notes": "placeholder state generated by session-init; replace with real storage state later",
        }
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "session_name": session_name,
        "state_dir": str(sessions_dir),
        "state_path": str(state_path),
        "created": created,
        "playwright_detected": detect_playwright(),
    }


def _mirror_storage_state(config: AppConfig, source_state_path: Path) -> str | None:
    mirror_path = getattr(config.browser, "storage_state", None)
    if mirror_path is None:
        return None

    mirror_path = Path(mirror_path)
    mirror_path.parent.mkdir(parents=True, exist_ok=True)
    mirror_path.write_text(source_state_path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(mirror_path)


def _normalize_session_name(session_name: str) -> str:
    cleaned = session_name.strip()
    if not cleaned:
        raise ValueError("session_name must not be empty")
    cleaned = INVALID_SESSION_CHARS.sub("_", cleaned)
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        raise ValueError("session_name becomes empty after sanitization")
    return cleaned


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
