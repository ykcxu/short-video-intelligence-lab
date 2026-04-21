from .adapter_ytdlp import detect_ytdlp, run_ytdlp_download
from .downloader_stub import run_download_job as run_download_job_stub
from .downloader_stub import run_download_jobs as run_download_jobs_stub
from .job_builder import build_download_jobs
from .manager import (
    run_download_job,
    run_download_job_with_fallback,
    run_download_jobs,
    run_download_jobs_with_fallback,
)

__all__ = [
    "build_download_jobs",
    "detect_ytdlp",
    "run_ytdlp_download",
    "run_download_job_stub",
    "run_download_jobs_stub",
    "run_download_job",
    "run_download_job_with_fallback",
    "run_download_jobs",
    "run_download_jobs_with_fallback",
]
