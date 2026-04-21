from __future__ import annotations

import hashlib
import ast
import importlib
import json
from pathlib import Path
from typing import Any

from .browser.session_manager import init_session_state
from .collector.comment_collector import collect_video_comments
from .collector.homepage_collector import collect_homepage_videos
from .collector.targets_loader import load_targets_from_path
from .collector.video_collector import collect_video_detail
from .config import AppConfig
from .downloader import build_download_jobs, run_download_jobs


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

    def crawl_homepage(self, homepage_url: str, *, max_items: int = 50) -> dict[str, Any]:
        self.bootstrap()
        result = collect_homepage_videos(self.config, homepage_url=homepage_url, max_items=max_items)
        artifact_path = self._write_artifact(
            category="collector/homepage",
            stem=f"homepage_{self._hash_text(homepage_url)}",
            payload=result,
        )
        result["artifact_path"] = str(artifact_path)
        return result

    def crawl_video_detail(self, video_url: str) -> dict[str, Any]:
        self.bootstrap()
        result = collect_video_detail(self.config, video_url=video_url)
        artifact_path = self._write_artifact(
            category="collector/video",
            stem=f"video_detail_{self._hash_text(video_url)}",
            payload=result,
        )
        result["artifact_path"] = str(artifact_path)
        return result

    def crawl_video_comments(self, video_url: str, *, max_pages: int = 3) -> dict[str, Any]:
        self.bootstrap()
        result = collect_video_comments(self.config, video_url=video_url, max_pages=max_pages)
        artifact_path = self._write_artifact(
            category="collector/comments",
            stem=f"video_comments_{self._hash_text(video_url)}",
            payload=result,
        )
        result["artifact_path"] = str(artifact_path)
        return result

    def create_download_jobs(
        self,
        *,
        videos_file: str | Path,
        output_dir: str | Path | None = None,
        run: bool = False,
    ) -> dict[str, Any]:
        self.bootstrap()
        videos_path = Path(videos_file)
        if not videos_path.exists():
            raise FileNotFoundError(videos_path)

        with videos_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if not isinstance(payload, list):
            raise ValueError("videos_file must contain a JSON array")

        resolved_output_dir = Path(output_dir) if output_dir else (self.config.downloads_dir / "stub")
        jobs = build_download_jobs(payload, resolved_output_dir)
        artifact_path = self._write_artifact(
            category="downloader/jobs",
            stem=f"download_jobs_{videos_path.stem}",
            payload={"jobs": jobs},
        )
        result: dict[str, Any] = {
            "videos_file": str(videos_path),
            "jobs_count": len(jobs),
            "jobs_artifact_path": str(artifact_path),
            "preview": jobs[:3],
        }
        if run:
            run_results = run_download_jobs(jobs)
            run_artifact_path = self._write_artifact(
                category="downloader/results",
                stem=f"download_results_{videos_path.stem}",
                payload={"results": run_results},
            )
            result["run_results_count"] = len(run_results)
            result["run_artifact_path"] = str(run_artifact_path)
            result["run_preview"] = run_results[:3]
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

    def _write_artifact(self, *, category: str, stem: str, payload: dict[str, Any]) -> Path:
        category_path = self.config.artifacts_dir / category
        category_path.mkdir(parents=True, exist_ok=True)
        filename = f"{stem}_{self._now_token()}.json"
        output_path = category_path / filename
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return output_path

    def _hash_text(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

    def _now_token(self) -> str:
        token = self._now_iso().replace(":", "").replace("-", "").replace(".", "")
        token = token.replace("+", "_plus_").replace("Z", "_z_")
        return token

    def _now_iso(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
