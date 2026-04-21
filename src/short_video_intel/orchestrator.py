from __future__ import annotations

import hashlib
import ast
import importlib
import json
from pathlib import Path
from typing import Any

from .browser.session_manager import init_session_state
from .collector.targets_loader import load_targets_from_path
from .config import AppConfig


class Orchestrator:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def bootstrap(self) -> dict[str, str]:
        directories = self._core_directories()
        for directory in directories.values():
            directory.mkdir(parents=True, exist_ok=True)
        return {
            "workspace": str(self.config.workspace),
            "data_dir": str(self.config.data_dir),
            "database_url": self.config.database_url,
            "targets_dir": str(directories["targets_dir"]),
            "sessions_dir": str(directories["sessions_dir"]),
            "imports_dir": str(directories["imports_dir"]),
        }

    def init_db(self) -> dict[str, Any]:
        self.bootstrap()
        db_api = self._load_db_api()
        if db_api is not None and hasattr(db_api, "create_db_and_tables"):
            db_api.create_db_and_tables(self.config.database_url)
            return {
                "backend": "db-module",
                "database_url": self.config.database_url,
                "database_path": str(self._resolve_sqlite_path()) if self._resolve_sqlite_path() is not None else None,
                "created": True,
            }

        db_path = self._resolve_sqlite_path()
        created = False
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            if not db_path.exists():
                db_path.touch()
                created = True
        return {
            "backend": "placeholder-file",
            "database_url": self.config.database_url,
            "database_path": str(db_path) if db_path is not None else None,
            "created": created,
        }

    def import_targets(
        self,
        source_path: str | Path,
        *,
        input_format: str = "auto",
    ) -> dict[str, Any]:
        self.bootstrap()
        source_path = Path(source_path)
        targets = load_targets_from_path(source_path, input_format=input_format)

        db_api = self._load_db_api()
        if (
            db_api is not None
            and hasattr(db_api, "get_session")
            and hasattr(db_api, "HomepageTarget")
        ):
            created_count = 0
            updated_count = 0
            with db_api.get_session(self.config.database_url) as session:
                for target in targets:
                    model = db_api.HomepageTarget
                    existing = (
                        session.query(model)
                        .filter(model.homepage_url == target["homepage_url"])
                        .one_or_none()
                    )
                    tags_value = self._decode_tags_json(target["tags_json"])
                    if existing is None:
                        session.add(
                            model(
                                platform="douyin",
                                homepage_url=target["homepage_url"],
                                source_name=target.get("source_name") or None,
                                category_lv1=target.get("category_lv1") or None,
                                category_lv2=target.get("category_lv2") or None,
                                tags_json=tags_value,
                                status=target.get("status", "active"),
                                notes=target.get("notes"),
                            )
                        )
                        created_count += 1
                    else:
                        existing.source_name = target.get("source_name") or None
                        existing.category_lv1 = target.get("category_lv1") or None
                        existing.category_lv2 = target.get("category_lv2") or None
                        existing.tags_json = tags_value
                        if target.get("status"):
                            existing.status = target["status"]
                        if "notes" in target:
                            existing.notes = target.get("notes")
                        updated_count += 1
            return {
                "backend": "db-module",
                "source_path": str(source_path),
                "imported_count": len(targets),
                "created_count": created_count,
                "updated_count": updated_count,
                "preview": targets[:3],
            }

        fallback_path = self._write_import_targets_fallback(source_path, targets)
        return {
            "backend": "jsonl-fallback",
            "source_path": str(source_path),
            "fallback_path": str(fallback_path),
            "imported_count": len(targets),
            "preview": targets[:3],
        }

    def session_init(self, session_name: str) -> dict[str, Any]:
        self.bootstrap()
        result = init_session_state(self.config, session_name)
        db_api = self._load_db_api()
        if (
            db_api is not None
            and hasattr(db_api, "get_session")
            and hasattr(db_api, "CrawlSession")
        ):
            with db_api.get_session(self.config.database_url) as session:
                model = db_api.CrawlSession
                existing = (
                    session.query(model)
                    .filter(model.session_name == result["session_name"])
                    .one_or_none()
                )
                if existing is None:
                    session.add(
                        model(
                            session_name=result["session_name"],
                            state_file_path=result["state_path"],
                            cookie_file_path=None,
                            login_status="placeholder",
                            remarks="placeholder state created by session-init",
                        )
                    )
                else:
                    existing.state_file_path = result["state_path"]
                    existing.login_status = "placeholder"
                    existing.remarks = "placeholder state refreshed by session-init"
        if result.get("playwright_detected"):
            result["notice"] = "[session-init] 检测到 Playwright，可后续接入真实会话。"
        else:
            result["notice"] = "[session-init] 未检测到 Playwright，仅生成占位 state。"
        return result

    def _core_directories(self) -> dict[str, Path]:
        return {
            "data_dir": self.config.data_dir,
            "targets_dir": self.config.data_dir / "targets",
            "sessions_dir": self.config.data_dir / "sessions",
            "imports_dir": self.config.data_dir / "imports",
        }

    def _load_db_api(self) -> Any | None:
        try:
            return importlib.import_module(".db", package=__package__)
        except ModuleNotFoundError:
            return None

    def _resolve_sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if not self.config.database_url.startswith(prefix):
            return None
        raw_path = self.config.database_url[len(prefix) :]
        if raw_path == ":memory:":
            return None
        db_path = Path(raw_path)
        if not db_path.is_absolute():
            db_path = self.config.workspace / db_path
        return db_path

    def _write_import_targets_fallback(
        self,
        source_path: Path,
        targets: list[dict[str, Any]],
    ) -> Path:
        imports_dir = self.config.data_dir / "imports" / "targets"
        imports_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(str(source_path.resolve()).encode("utf-8")).hexdigest()[:8]
        output_path = imports_dir / f"{source_path.stem}_{digest}.jsonl"
        payload = [dict(item, imported_source=str(source_path)) for item in targets]
        with output_path.open("w", encoding="utf-8") as handle:
            for row in payload:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return output_path

    def _decode_tags_json(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(value)
                except (ValueError, SyntaxError):
                    parsed = [part.strip().strip("\"'") for part in value.strip("[]").split(",") if part.strip()]
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
            if isinstance(parsed, tuple):
                return [str(item) for item in parsed]
            return [str(parsed)]
        return [str(value)]
