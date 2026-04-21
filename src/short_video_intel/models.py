from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class VideoJob:
    job_id: str
    source_uri: str
    local_video_path: str | None = None
    status: str = "pending"
    duration_sec: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptSegment:
    job_id: str
    segment_id: int
    start_ms: int
    end_ms: int
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisResult:
    job_id: str
    keywords: list[str] = field(default_factory=list)
    topics: list[dict[str, Any]] = field(default_factory=list)
    structure: list[dict[str, Any]] = field(default_factory=list)
