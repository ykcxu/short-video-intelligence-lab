from __future__ import annotations

import hashlib
import ast
import importlib
import json
import time
from pathlib import Path
from typing import Any

from .browser.session_manager import capture_session_state, init_session_state, launch_debug_browser
from .collector.comment_collector import collect_video_comments
from .collector.homepage_collector import collect_homepage_videos, collect_homepage_videos_via_cdp
from .collector.target_source import load_targets_from_db, load_targets_from_file
from .collector.targets_loader import load_targets_from_path
from .collector.video_collector import collect_video_detail
from .config import AppConfig
from .downloader import build_download_jobs, run_download_jobs
from .pipelines import run_batch_full_collect, run_batch_homepage_crawl


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

    def session_capture(
        self,
        session_name: str,
        *,
        homepage_url: str = "https://www.douyin.com/",
        wait_seconds: int = 120,
    ) -> dict[str, Any]:
        self.bootstrap()
        result = capture_session_state(
            self.config,
            session_name,
            homepage_url=homepage_url,
            wait_seconds=wait_seconds,
        )
        if result.get("ok"):
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
                                cookie_file_path=result.get("mirrored_storage_state"),
                                login_status="captured",
                                remarks="manual session capture with storage_state",
                            )
                        )
                    else:
                        existing.state_file_path = result["state_path"]
                        existing.cookie_file_path = result.get("mirrored_storage_state")
                        existing.login_status = "captured"
                        existing.remarks = "manual session capture refreshed"
            result["notice"] = "[session-capture] 已保存 storage_state，可用于后续采集。"
        return result

    def open_debug_homepage(
        self,
        session_name: str,
        *,
        homepage_url: str,
        cdp_port: int = 9222,
        hold_seconds: int = 1800,
    ) -> dict[str, Any]:
        self.bootstrap()
        return launch_debug_browser(
            self.config,
            session_name,
            homepage_url=homepage_url,
            cdp_port=cdp_port,
            hold_seconds=hold_seconds,
        )

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

    def crawl_homepage_via_cdp(self, homepage_url: str, *, cdp_url: str, max_items: int = 50) -> dict[str, Any]:
        self.bootstrap()
        result = collect_homepage_videos_via_cdp(cdp_url, homepage_url=homepage_url, max_items=max_items)
        artifact_path = self._write_artifact(
            category="collector/homepage",
            stem=f"homepage_cdp_{self._hash_text(homepage_url)}",
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

    def crawl_targets_batch(
        self,
        *,
        source_file: str | Path | None = None,
        input_format: str = "auto",
        from_db: bool = False,
        status: str = "active",
        limit: int | None = None,
        max_items: int = 50,
        max_workers: int = 1,
        persist_db: bool = False,
    ) -> dict[str, Any]:
        self.bootstrap()
        targets = self._load_targets_for_batch(
            source_file=source_file,
            input_format=input_format,
            from_db=from_db,
            status=status,
            limit=limit,
        )

        batch_result = run_batch_homepage_crawl(
            self.config,
            targets=targets,
            max_items=max_items,
            max_workers=max_workers,
        )
        summary: dict[str, Any] = {
            "source": "db" if from_db else "file",
            "targets_loaded": len(targets),
            "batch": batch_result,
            "summary": batch_result.get("summary_block", {}),
        }

        if persist_db:
            summary["persistence"] = self._persist_batch_results(batch_result)

        artifact_path = self._write_artifact(
            category="collector/batch",
            stem="batch_homepage_crawl",
            payload=summary,
        )
        summary["artifact_path"] = str(artifact_path)
        return summary

    def crawl_targets_full_batch(
        self,
        *,
        source_file: str | Path | None = None,
        input_format: str = "auto",
        from_db: bool = False,
        status: str = "active",
        limit: int | None = None,
        max_items: int = 50,
        max_workers: int = 1,
        with_video_detail: bool = False,
        with_comments: bool = False,
        comment_pages: int = 3,
        persist_db: bool = False,
        video_limit_per_target: int | None = None,
        comment_video_limit_per_target: int | None = None,
    ) -> dict[str, Any]:
        self.bootstrap()
        targets = self._load_targets_for_batch(
            source_file=source_file,
            input_format=input_format,
            from_db=from_db,
            status=status,
            limit=limit,
        )

        batch_result = run_batch_full_collect(
            self.config,
            targets=targets,
            with_video_detail=with_video_detail,
            with_comments=with_comments,
            comment_pages=comment_pages,
            max_items=max_items,
            max_workers=max_workers,
            video_limit_per_target=video_limit_per_target,
            comment_video_limit_per_target=comment_video_limit_per_target,
        )
        summary: dict[str, Any] = {
            "source": "db" if from_db else "file",
            "targets_loaded": len(targets),
            "batch": batch_result,
            "summary": batch_result.get("summary_block", {}),
        }

        artifact_path = self._write_artifact(
            category="collector/full-batch",
            stem="batch_full_collect",
            payload=summary,
        )
        summary["artifact_path"] = str(artifact_path)

        if persist_db:
            summary["persistence"] = self._persist_full_batch_results(
                batch_result,
                raw_json_path=str(artifact_path),
            )

        return summary

    def run_phase1_chunked(
        self,
        *,
        source_file: str | Path | None = None,
        input_format: str = "auto",
        from_db: bool = False,
        status: str = "active",
        limit: int | None = None,
        max_items: int = 50,
        max_workers: int = 1,
        comment_pages: int = 2,
        persist_db: bool = True,
        video_limit_per_target: int | None = None,
        comment_video_limit_per_target: int | None = None,
        chunk_size: int = 3,
        pause_seconds: float = 0.0,
    ) -> dict[str, Any]:
        self.bootstrap()
        targets = self._load_targets_for_batch(
            source_file=source_file,
            input_format=input_format,
            from_db=from_db,
            status=status,
            limit=limit,
        )
        normalized_chunk_size = max(1, int(chunk_size))
        chunks = [
            targets[index : index + normalized_chunk_size]
            for index in range(0, len(targets), normalized_chunk_size)
        ]

        chunk_reports: list[dict[str, Any]] = []
        rerun_targets: list[dict[str, Any]] = []
        total_success = 0
        total_failed = 0
        total_videos = 0
        total_detail_attempted = 0
        total_detail_success = 0
        total_comment_attempted = 0
        total_comment_success = 0
        total_comment_items_seen = 0
        total_comment_entries_seen = 0
        total_comment_reply_entries_seen = 0
        total_detail_meaningful = 0
        total_comment_meaningful = 0
        account_summary_rollup: list[dict[str, Any]] = []

        for chunk_index, chunk_targets in enumerate(chunks, start=1):
            batch_result = run_batch_full_collect(
                self.config,
                targets=chunk_targets,
                with_video_detail=True,
                with_comments=True,
                comment_pages=comment_pages,
                max_items=max_items,
                max_workers=max_workers,
                video_limit_per_target=video_limit_per_target,
                comment_video_limit_per_target=comment_video_limit_per_target,
            )
            chunk_payload: dict[str, Any] = {
                "source": "db" if from_db else "file",
                "chunk_index": chunk_index,
                "chunk_size": len(chunk_targets),
                "targets_loaded": len(chunk_targets),
                "targets": [dict(target) for target in chunk_targets],
                "batch": batch_result,
                "summary": batch_result.get("summary_block", {}),
            }
            chunk_artifact_path = self._write_artifact(
                category="collector/full-batch-chunks",
                stem=f"phase1_chunk_{chunk_index:03d}",
                payload=chunk_payload,
            )
            chunk_payload["artifact_path"] = str(chunk_artifact_path)

            if persist_db:
                chunk_payload["persistence"] = self._persist_full_batch_results(
                    batch_result,
                    raw_json_path=str(chunk_artifact_path),
                )

            global_summary = self._as_mapping(
                self._as_mapping(chunk_payload.get("summary")).get("global_summary")
            )
            account_summary = [
                dict(item)
                for item in list(self._as_mapping(chunk_payload.get("summary")).get("account_summary") or [])
                if isinstance(item, dict)
            ]
            failures_in_chunk = list(batch_result.get("failures") or [])
            failed_targets_in_chunk: list[dict[str, Any]] = []
            for failure_item in failures_in_chunk:
                failure_mapping = self._as_mapping(failure_item)
                target_mapping = self._as_mapping(failure_mapping.get("target"))
                if target_mapping:
                    failed_targets_in_chunk.append(target_mapping)
                    rerun_targets.append(dict(target_mapping))

            total_success += int(batch_result.get("success_count") or 0)
            total_failed += int(batch_result.get("failed_count") or 0)
            total_videos += int(global_summary.get("video_total") or 0)
            total_detail_attempted += int(global_summary.get("detail_attempted") or 0)
            total_detail_success += int(global_summary.get("detail_success_count") or 0)
            total_comment_attempted += int(global_summary.get("comment_attempted") or 0)
            total_comment_success += int(global_summary.get("comment_success_count") or 0)
            total_comment_items_seen += int(global_summary.get("comment_items_seen") or 0)
            total_comment_entries_seen += int(global_summary.get("comment_entries_seen") or 0)
            total_comment_reply_entries_seen += int(global_summary.get("comment_reply_entries_seen") or 0)
            total_detail_meaningful += int(global_summary.get("detail_meaningful_count") or 0)
            total_comment_meaningful += int(global_summary.get("comment_meaningful_count") or 0)
            chunk_duration_sec = self._to_float(batch_result.get("duration_sec"))
            detail_success_rate = self._to_ratio(
                int(global_summary.get("detail_success_count") or 0),
                int(global_summary.get("detail_attempted") or 0),
            )
            comment_success_rate = self._to_ratio(
                int(global_summary.get("comment_success_count") or 0),
                int(global_summary.get("comment_attempted") or 0),
            )
            detail_meaningful_rate = self._to_ratio(
                int(global_summary.get("detail_meaningful_count") or 0),
                int(global_summary.get("detail_attempted") or 0),
            )
            comment_meaningful_rate = self._to_ratio(
                int(global_summary.get("comment_meaningful_count") or 0),
                int(global_summary.get("comment_attempted") or 0),
            )

            chunk_status = "failed" if failures_in_chunk else "success"
            chunk_report = {
                "chunk_index": chunk_index,
                "status": chunk_status,
                "chunk_size": len(chunk_targets),
                "artifact_path": str(chunk_artifact_path),
                "duration_sec": chunk_duration_sec,
                "success_count": int(batch_result.get("success_count") or 0),
                "failed_count": int(batch_result.get("failed_count") or 0),
                "video_total": int(global_summary.get("video_total") or 0),
                "detail_attempted": int(global_summary.get("detail_attempted") or 0),
                "detail_success_count": int(global_summary.get("detail_success_count") or 0),
                "comment_attempted": int(global_summary.get("comment_attempted") or 0),
                "comment_success_count": int(global_summary.get("comment_success_count") or 0),
                "detail_meaningful_count": int(global_summary.get("detail_meaningful_count") or 0),
                "comment_meaningful_count": int(global_summary.get("comment_meaningful_count") or 0),
                "detail_success_rate": detail_success_rate,
                "comment_success_rate": comment_success_rate,
                "detail_meaningful_rate": detail_meaningful_rate,
                "comment_meaningful_rate": comment_meaningful_rate,
                "targets": [dict(target) for target in chunk_targets],
                "target_names": self._collect_target_names(chunk_targets),
                "account_summary": account_summary,
                "failed_targets": failed_targets_in_chunk,
            }
            chunk_reports.append(chunk_report)
            account_summary_rollup.extend(
                self._attach_chunk_context_to_accounts(
                    account_summary,
                    chunk_index=chunk_index,
                    chunk_status=chunk_status,
                    chunk_artifact_path=chunk_artifact_path,
                )
            )

            if pause_seconds > 0 and chunk_index < len(chunks):
                time.sleep(max(0.0, float(pause_seconds)))

        rerun_manifest_path: str | None = None
        rerun_command_example: str | None = None
        if rerun_targets:
            rerun_manifest_payload = {
                "mode": "phase1_chunked_rerun_manifest",
                "source": "db" if from_db else "file",
                "targets_loaded": len(rerun_targets),
                "targets": rerun_targets,
            }
            rerun_manifest_artifact = self._write_artifact(
                category="collector/full-batch-chunks",
                stem="phase1_chunked_rerun_manifest",
                payload=rerun_manifest_payload,
            )
            rerun_manifest_path = str(rerun_manifest_artifact)
            rerun_command_example = self._build_phase1_rerun_command_example(
                rerun_manifest_artifact,
                comment_pages=comment_pages,
                max_workers=max_workers,
                video_limit_per_target=video_limit_per_target,
                comment_video_limit_per_target=comment_video_limit_per_target,
            )

        slowest_chunks = sorted(
            chunk_reports,
            key=lambda item: self._to_float(item.get("duration_sec")),
            reverse=True,
        )[:5]
        rerun_priority_chunks = self._build_rerun_priority_chunks(chunk_reports)
        total_duration_sec = round(sum(self._to_float(item.get("duration_sec")) for item in chunk_reports), 6)
        summary_block = {
            "account_summary": account_summary_rollup,
            "global_summary": {
                "target_count": len(targets),
                "video_total": total_videos,
                "detail_attempted": total_detail_attempted,
                "detail_success_count": total_detail_success,
                "detail_success_rate": self._to_ratio(total_detail_success, total_detail_attempted),
                "comment_attempted": total_comment_attempted,
                "comment_success_count": total_comment_success,
                "comment_success_rate": self._to_ratio(total_comment_success, total_comment_attempted),
                "comment_items_seen": total_comment_items_seen,
                "comment_entries_seen": total_comment_entries_seen,
                "comment_reply_entries_seen": total_comment_reply_entries_seen,
                "detail_meaningful_count": total_detail_meaningful,
                "comment_meaningful_count": total_comment_meaningful,
                "failed_count": total_failed,
                "with_video_detail": True,
                "with_comments": True,
                "comment_pages": comment_pages,
                "max_items": max_items,
                "video_limit_per_target": video_limit_per_target,
                "comment_video_limit_per_target": comment_video_limit_per_target,
                "chunk_count": len(chunks),
                "chunk_success_count": sum(1 for item in chunk_reports if item.get("status") == "success"),
                "chunk_failed_count": sum(1 for item in chunk_reports if item.get("status") == "failed"),
                "total_duration_sec": total_duration_sec,
            },
        }

        result: dict[str, Any] = {
            "ok": True,
            "mode": "phase1_chunked",
            "source": "db" if from_db else "file",
            "targets_loaded": len(targets),
            "chunk_size": normalized_chunk_size,
            "chunk_count": len(chunks),
            "with_video_detail": True,
            "with_comments": True,
            "comment_pages": comment_pages,
            "max_items": max_items,
            "video_limit_per_target": video_limit_per_target,
            "comment_video_limit_per_target": comment_video_limit_per_target,
            "chunks": chunk_reports,
            "summary_block": summary_block,
            "summary": {
                "success_count": total_success,
                "failed_count": total_failed,
                "video_total": total_videos,
                "detail_meaningful_count": total_detail_meaningful,
                "comment_meaningful_count": total_comment_meaningful,
                "chunk_success_count": sum(1 for item in chunk_reports if item.get("status") == "success"),
                "chunk_failed_count": sum(1 for item in chunk_reports if item.get("status") == "failed"),
                "total_duration_sec": total_duration_sec,
            },
            "failed_chunks": [item for item in chunk_reports if item.get("status") == "failed"],
            "slowest_chunks": slowest_chunks,
            "rerun_priority_chunks": rerun_priority_chunks,
            "rerun_targets_count": len(rerun_targets),
        }
        if rerun_manifest_path is not None:
            result["rerun_manifest_path"] = rerun_manifest_path
        if rerun_command_example is not None:
            result["rerun_command_example"] = rerun_command_example
        artifact_path = self._write_artifact(
            category="collector/full-batch",
            stem="phase1_chunked_master",
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

    def _load_targets_for_batch(
        self,
        *,
        source_file: str | Path | None,
        input_format: str,
        from_db: bool,
        status: str,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        if from_db:
            return load_targets_from_db(
                self.config.database_url,
                status=status,
                limit=limit,
            )
        if source_file is None:
            raise ValueError("source_file is required when from_db is False")
        return load_targets_from_file(source_file, input_format=input_format)

    def _persist_batch_results(self, batch_result: dict[str, Any]) -> dict[str, Any]:
        db_api = self._load_db_api()
        if db_api is None or not hasattr(db_api, "get_session") or not hasattr(
            db_api, "persist_homepage_crawl_result"
        ):
            return {
                "enabled": True,
                "backend": "unavailable",
                "persisted_count": 0,
                "failed_count": 0,
                "failures": ["db upsert helpers unavailable"],
            }

        persisted_count = 0
        failed_count = 0
        persistence_items: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        with db_api.get_session(self.config.database_url) as session:
            for item in batch_result.get("results", []):
                target = item.get("target", {})
                crawl_result = item.get("crawl_result", {})
                raw_json_path = crawl_result.get("artifact_path") if isinstance(crawl_result, dict) else None
                try:
                    row = db_api.persist_homepage_crawl_result(
                        session,
                        target,
                        crawl_result,
                        raw_json_path=raw_json_path,
                    )
                    persistence_items.append(row)
                    persisted_count += 1
                except Exception as exc:  # pragma: no cover - runtime safety
                    failed_count += 1
                    failures.append(
                        {
                            "homepage_url": target.get("homepage_url"),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        return {
            "enabled": True,
            "backend": "db-module",
            "persisted_count": persisted_count,
            "failed_count": failed_count,
            "items": persistence_items,
            "failures": failures,
        }

    def _persist_full_batch_results(
        self,
        batch_result: dict[str, Any],
        *,
        raw_json_path: str | None = None,
    ) -> dict[str, Any]:
        db_api = self._load_db_api()
        required_attrs = (
            "get_session",
            "persist_homepage_crawl_result",
            "upsert_video_from_candidate",
            "insert_video_snapshot",
        )
        comment_persist_helper = getattr(db_api, "persist_video_comments_result", None) if db_api is not None else None
        if db_api is None or any(not hasattr(db_api, attr) for attr in required_attrs):
            return {
                "enabled": True,
                "backend": "unavailable",
                "homepage_persisted_count": 0,
                "detail_snapshots_inserted": 0,
                "comment_results_seen": 0,
                "comments_persisted_count": 0,
                "comment_replies_persisted_count": 0,
                "failed_count": 0,
                "failures": ["db upsert helpers unavailable"],
            }

        homepage_persisted_count = 0
        detail_snapshots_inserted = 0
        comment_results_seen = 0
        comments_persisted_count = 0
        comment_replies_persisted_count = 0
        failed_count = 0
        failures: list[dict[str, Any]] = []
        persistence_items: list[dict[str, Any]] = []

        with db_api.get_session(self.config.database_url) as session:
            for item in batch_result.get("results", []):
                target = item.get("target", {})
                homepage_result = item.get("homepage_result") or item.get("crawl_result") or {}
                homepage_raw_json_path = (
                    homepage_result.get("artifact_path")
                    if isinstance(homepage_result, dict)
                    else None
                ) or raw_json_path

                try:
                    homepage_summary = db_api.persist_homepage_crawl_result(
                        session,
                        target,
                        homepage_result,
                        raw_json_path=homepage_raw_json_path,
                    )
                    persistence_items.append(
                        {
                            "homepage_url": homepage_summary["homepage_url"],
                            "homepage_target_id": homepage_summary["homepage_target_id"],
                            "homepage_persisted": True,
                            "detail_snapshots_inserted": 0,
                            "comment_results_seen": 0,
                            "comments_persisted_count": 0,
                            "comment_replies_persisted_count": 0,
                        }
                    )
                    homepage_persisted_count += 1
                except Exception as exc:  # pragma: no cover - runtime safety
                    failed_count += 1
                    failures.append(
                        {
                            "homepage_url": target.get("homepage_url"),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue

                homepage_target_id = homepage_summary["homepage_target_id"]
                item_detail_snapshots = 0
                item_comment_results_seen = 0
                item_comments_persisted = 0
                item_comment_replies_persisted = 0

                for video_item in item.get("video_items", []):
                    candidate = video_item.get("candidate") or {}
                    detail_result = video_item.get("detail_result")
                    comments_result = video_item.get("comments_result")
                    has_detail_result = isinstance(detail_result, dict)
                    has_comments_result = isinstance(comments_result, dict)

                    if has_comments_result:
                        item_comment_results_seen += 1
                        comment_results_seen += 1

                    if not has_detail_result and not has_comments_result:
                        continue

                    try:
                        video = db_api.upsert_video_from_candidate(
                            session=session,
                            target_id=homepage_target_id,
                            candidate=candidate if isinstance(candidate, dict) else {},
                            raw_json_path=(
                                detail_result.get("artifact_path")
                                if has_detail_result and isinstance(detail_result.get("artifact_path"), str)
                                else None
                            )
                            or raw_json_path,
                        )
                        if has_detail_result:
                            metrics = dict(detail_result.get("metrics") or {})
                            metrics.setdefault(
                                "capture_source",
                                detail_result.get("backend") or "collector_stub",
                            )
                            db_api.insert_video_snapshot(
                                session=session,
                                video_id_fk=video.id,
                                metrics=metrics,
                                capture_source=metrics.get("capture_source") or "collector_stub",
                                raw_json_path=(
                                    detail_result.get("artifact_path")
                                    if isinstance(detail_result.get("artifact_path"), str)
                                    else None
                                )
                                or raw_json_path,
                            )
                            item_detail_snapshots += 1
                            detail_snapshots_inserted += 1

                        if has_comments_result and comment_persist_helper is not None:
                            comments_raw_json_path = (
                                comments_result.get("artifact_path")
                                if isinstance(comments_result.get("artifact_path"), str)
                                else None
                            ) or raw_json_path
                            persisted_comments_result = self._persist_video_comments_result(
                                comment_persist_helper,
                                session=session,
                                video=video,
                                comments_result=comments_result,
                                raw_json_path=comments_raw_json_path,
                            )
                            item_comments_persisted, item_comment_replies_persisted = self._extract_comment_persistence_counts(
                                persisted_comments_result,
                                comments_result,
                            )
                            comments_persisted_count += item_comments_persisted
                            comment_replies_persisted_count += item_comment_replies_persisted
                    except Exception as exc:  # pragma: no cover - runtime safety
                        failed_count += 1
                        failures.append(
                            {
                                "homepage_url": target.get("homepage_url"),
                                "video_url": candidate.get("video_url"),
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )

                persistence_items[-1]["detail_snapshots_inserted"] = item_detail_snapshots
                persistence_items[-1]["comment_results_seen"] = item_comment_results_seen
                persistence_items[-1]["comments_persisted_count"] = item_comments_persisted
                persistence_items[-1]["comment_replies_persisted_count"] = item_comment_replies_persisted

        return {
            "enabled": True,
            "backend": "db-module",
            "homepage_persisted_count": homepage_persisted_count,
            "detail_snapshots_inserted": detail_snapshots_inserted,
            "comment_results_seen": comment_results_seen,
            "comments_persisted_count": comments_persisted_count,
            "comment_replies_persisted_count": comment_replies_persisted_count,
            "failed_count": failed_count,
            "items": persistence_items,
            "failures": failures,
        }

    def _persist_video_comments_result(
        self,
        helper: Any,
        *,
        session: Any,
        video: Any,
        comments_result: dict[str, Any],
        raw_json_path: str | None,
    ) -> Any:
        attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = [
            (
                (),
                {
                    "session": session,
                    "video_id_fk": getattr(video, "id", None),
                    "comments_result": comments_result,
                    "raw_json_path": raw_json_path,
                },
            ),
            (
                (),
                {
                    "session": session,
                    "video_id": getattr(video, "id", None),
                    "comments_result": comments_result,
                    "raw_json_path": raw_json_path,
                },
            ),
            (
                (),
                {
                    "session": session,
                    "video": video,
                    "comments_result": comments_result,
                    "raw_json_path": raw_json_path,
                },
            ),
            (
                (),
                {
                    "session": session,
                    "video_id_fk": getattr(video, "id", None),
                    "comments": comments_result.get("comments"),
                    "replies": comments_result.get("replies"),
                    "scan_meta": comments_result.get("scan_meta"),
                    "raw_json_path": raw_json_path,
                },
            ),
            (
                (),
                {
                    "session": session,
                    "video_id": getattr(video, "id", None),
                    "comments": comments_result.get("comments"),
                    "replies": comments_result.get("replies"),
                    "scan_meta": comments_result.get("scan_meta"),
                    "raw_json_path": raw_json_path,
                },
            ),
            (
                (session, getattr(video, "id", None), comments_result, raw_json_path),
                {},
            ),
            (
                (session, video, comments_result, raw_json_path),
                {},
            ),
        ]

        last_error: Exception | None = None
        for args, kwargs in attempts:
            try:
                return helper(*args, **{key: value for key, value in kwargs.items() if value is not None})
            except TypeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        return helper(session=session, video_id_fk=getattr(video, "id", None), comments_result=comments_result, raw_json_path=raw_json_path)

    def _extract_comment_persistence_counts(
        self,
        persistence_result: Any,
        comments_result: dict[str, Any],
    ) -> tuple[int, int]:
        payload = self._as_mapping(persistence_result)
        comment_count_keys = (
            "comments_persisted_count",
            "comment_persisted_count",
            "comments_count",
            "persisted_comments_count",
            "inserted_comments_count",
        )
        reply_count_keys = (
            "comment_replies_persisted_count",
            "replies_persisted_count",
            "reply_count",
            "replies_count",
            "persisted_replies_count",
            "inserted_replies_count",
        )

        comment_count_present = self._has_any_key(payload, comment_count_keys)
        reply_count_present = self._has_any_key(payload, reply_count_keys)

        comments_count = self._first_int_from_mapping(payload, comment_count_keys)
        replies_count = self._first_int_from_mapping(payload, reply_count_keys)

        if not comment_count_present:
            comments_count = len(list((comments_result or {}).get("comments") or []))
        if not reply_count_present:
            replies_count = len(list((comments_result or {}).get("replies") or []))
        return comments_count, replies_count

    def _as_mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump()  # type: ignore[call-arg]
            except TypeError:
                dumped = value.model_dump(exclude_none=True)  # type: ignore[call-arg]
            if isinstance(dumped, dict):
                return dumped
        if hasattr(value, "__dict__"):
            return {
                key: item
                for key, item in dict(value.__dict__).items()
                if not str(key).startswith("_sa_")
            }
        return {}

    def _first_int_from_mapping(self, mapping: dict[str, Any], keys: tuple[str, ...]) -> int:
        for key in keys:
            if key not in mapping:
                continue
            value = mapping.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    continue
                try:
                    return int(float(text))
                except ValueError:
                    continue
            if isinstance(value, (list, tuple, set)):
                return len(value)
        summary = mapping.get("summary")
        if isinstance(summary, dict):
            return self._first_int_from_mapping(summary, keys)
        return 0

    def _has_any_key(self, mapping: dict[str, Any], keys: tuple[str, ...]) -> bool:
        for key in keys:
            if key in mapping:
                return True
        summary = mapping.get("summary")
        if isinstance(summary, dict):
            return self._has_any_key(summary, keys)
        return False

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

    def _to_float(self, value: Any) -> float:
        try:
            if value is None:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _to_ratio(self, numerator: Any, denominator: Any) -> float:
        numerator_value = self._to_float(numerator)
        denominator_value = self._to_float(denominator)
        if denominator_value <= 0:
            return 0.0
        return round(numerator_value / denominator_value, 6)

    def _collect_target_names(self, targets: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            label = str(target.get("source_name") or target.get("homepage_url") or "").strip()
            if label and label not in names:
                names.append(label)
        return names

    def _attach_chunk_context_to_accounts(
        self,
        accounts: list[dict[str, Any]],
        *,
        chunk_index: int,
        chunk_status: str,
        chunk_artifact_path: Path,
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for account in accounts:
            if not isinstance(account, dict):
                continue
            enriched.append(
                {
                    **account,
                    "chunk_index": chunk_index,
                    "chunk_status": chunk_status,
                    "chunk_artifact_path": str(chunk_artifact_path),
                    "rerun_priority_hint": self._build_account_rerun_priority_hint(account, chunk_status),
                }
            )
        return enriched

    def _build_account_rerun_priority_hint(self, account: dict[str, Any], chunk_status: str) -> str:
        if chunk_status == "failed":
            return "high"
        detail_meaningful = int(account.get("detail_meaningful") or 0)
        comment_meaningful = int(account.get("comment_meaningful") or 0)
        videos_seen = int(account.get("videos_seen") or 0)
        if videos_seen <= 0:
            return "high"
        if detail_meaningful == 0 and comment_meaningful == 0:
            return "high"
        if comment_meaningful == 0:
            return "medium"
        return "low"

    def _build_rerun_priority_chunks(self, chunk_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        priority_items: list[dict[str, Any]] = []
        for item in chunk_reports:
            chunk = self._as_mapping(item)
            failed_count = int(chunk.get("failed_count") or 0)
            detail_meaningful_rate = self._to_float(chunk.get("detail_meaningful_rate"))
            comment_meaningful_rate = self._to_float(chunk.get("comment_meaningful_rate"))
            duration_sec = self._to_float(chunk.get("duration_sec"))
            priority_score = round(
                failed_count * 100
                + max(0.0, 1.0 - detail_meaningful_rate) * 20
                + max(0.0, 1.0 - comment_meaningful_rate) * 25
                + min(duration_sec / 60.0, 20.0),
                6,
            )
            priority_items.append(
                {
                    "chunk_index": int(chunk.get("chunk_index") or 0),
                    "status": str(chunk.get("status") or ""),
                    "priority_score": priority_score,
                    "reason": self._build_chunk_priority_reason(
                        failed_count=failed_count,
                        detail_meaningful_rate=detail_meaningful_rate,
                        comment_meaningful_rate=comment_meaningful_rate,
                    ),
                    "failed_count": failed_count,
                    "target_names": list(chunk.get("target_names") or []),
                    "artifact_path": chunk.get("artifact_path"),
                }
            )
        priority_items.sort(
            key=lambda row: (
                -self._to_float(row.get("priority_score")),
                int(row.get("chunk_index") or 0),
            )
        )
        return priority_items[:10]

    def _build_chunk_priority_reason(
        self,
        *,
        failed_count: int,
        detail_meaningful_rate: float,
        comment_meaningful_rate: float,
    ) -> str:
        if failed_count > 0:
            return "存在失败目标，建议优先补跑"
        if detail_meaningful_rate <= 0 and comment_meaningful_rate <= 0:
            return "详情与评论都未形成有效样本"
        if comment_meaningful_rate <= 0:
            return "评论有效率偏低，建议先补评论链路"
        if detail_meaningful_rate < 0.5:
            return "详情有效率偏低，建议复查详情页提取"
        return "当前块较稳定，可低优先级补跑"

    def _build_phase1_rerun_command_example(
        self,
        rerun_manifest_path: Path,
        *,
        comment_pages: int,
        max_workers: int,
        video_limit_per_target: int | None,
        comment_video_limit_per_target: int | None,
    ) -> str:
        command_parts = [
            "py -3.11 -X utf8 -m short_video_intel.cli",
            f"--config {self.config.config_path}",
            "run-phase1-batch",
            f"--source-file {rerun_manifest_path}",
            "--format json",
            "--no-from-db",
            f"--workers {max_workers}",
            f"--comment-pages {comment_pages}",
        ]
        if video_limit_per_target is not None:
            command_parts.append(f"--video-limit-per-target {video_limit_per_target}")
        if comment_video_limit_per_target is not None:
            command_parts.append(f"--comment-video-limit-per-target {comment_video_limit_per_target}")
        return " ".join(str(part) for part in command_parts)
