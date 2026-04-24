from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def extract_videos_from_artifact(artifact_path: str | Path) -> dict[str, Any]:
    path = Path(artifact_path)
    if not path.exists():
        raise FileNotFoundError(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    collected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    visited_artifacts: set[str] = set()

    def add_video(video: Any, *, source_name: str = "", homepage_url: str = "") -> None:
        if not isinstance(video, dict):
            return
        video_url = str(video.get("video_url") or "").strip()
        if not video_url or video_url in seen_urls:
            return
        seen_urls.add(video_url)
        item = {
            "video_url": video_url,
            "video_id": str(video.get("video_id") or "").strip(),
            "title": video.get("title"),
            "publish_at": video.get("publish_at"),
            "source_name": source_name,
            "homepage_url": homepage_url,
        }
        collected.append(item)

    def collect_from_payload(node: Any, *, inherited_source_name: str = "", inherited_homepage_url: str = "") -> None:
        if isinstance(node, dict):
            videos = node.get("videos")
            if isinstance(videos, list):
                for video in videos:
                    add_video(
                        video,
                        source_name=str(node.get("source_name") or inherited_source_name or "").strip(),
                        homepage_url=str(node.get("homepage_url") or inherited_homepage_url or "").strip(),
                    )

            target = node.get("target")
            target_source_name = inherited_source_name
            target_homepage_url = inherited_homepage_url
            if isinstance(target, dict):
                target_source_name = str(target.get("source_name") or inherited_source_name or "").strip()
                target_homepage_url = str(target.get("homepage_url") or inherited_homepage_url or "").strip()

            crawl_result = node.get("crawl_result")
            if isinstance(crawl_result, dict):
                collect_from_payload(
                    crawl_result,
                    inherited_source_name=target_source_name,
                    inherited_homepage_url=target_homepage_url,
                )

            homepage_result = node.get("homepage_result")
            if isinstance(homepage_result, dict):
                collect_from_payload(
                    homepage_result,
                    inherited_source_name=target_source_name,
                    inherited_homepage_url=target_homepage_url,
                )

            candidate = node.get("candidate")
            if isinstance(candidate, dict):
                add_video(candidate, source_name=inherited_source_name, homepage_url=inherited_homepage_url)

            video_items = node.get("video_items")
            if isinstance(video_items, list):
                for item in video_items:
                    collect_from_payload(
                        item,
                        inherited_source_name=target_source_name or inherited_source_name,
                        inherited_homepage_url=target_homepage_url or inherited_homepage_url,
                    )

            batch = node.get("batch")
            if isinstance(batch, dict):
                collect_from_payload(
                    batch,
                    inherited_source_name=inherited_source_name,
                    inherited_homepage_url=inherited_homepage_url,
                )

            results = node.get("results")
            if isinstance(results, list):
                for item in results:
                    collect_from_payload(
                        item,
                        inherited_source_name=inherited_source_name,
                        inherited_homepage_url=inherited_homepage_url,
                    )

            chunks = node.get("chunks")
            if isinstance(chunks, list):
                for chunk in chunks:
                    if isinstance(chunk, dict):
                        chunk_artifact_path = str(chunk.get("artifact_path") or "").strip()
                        if chunk_artifact_path:
                            resolved = str(Path(chunk_artifact_path).resolve())
                            if resolved not in visited_artifacts and Path(chunk_artifact_path).exists():
                                visited_artifacts.add(resolved)
                                chunk_payload = json.loads(Path(chunk_artifact_path).read_text(encoding="utf-8"))
                                collect_from_payload(
                                    chunk_payload,
                                    inherited_source_name=inherited_source_name,
                                    inherited_homepage_url=inherited_homepage_url,
                                )
                        collect_from_payload(
                            chunk,
                            inherited_source_name=inherited_source_name,
                            inherited_homepage_url=inherited_homepage_url,
                        )

        elif isinstance(node, list):
            for item in node:
                collect_from_payload(
                    item,
                    inherited_source_name=inherited_source_name,
                    inherited_homepage_url=inherited_homepage_url,
                )

    visited_artifacts.add(str(path.resolve()))
    collect_from_payload(payload)

    return {
        "artifact_path": str(path),
        "videos": collected,
        "videos_count": len(collected),
    }

