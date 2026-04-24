from short_video_intel.analysis.local_video_inputs import _build_sample_timestamps
from short_video_intel.analysis.local_video_inputs import _build_content_feature_summary
from short_video_intel.analysis.local_video_inputs import _looks_like_video_download
from short_video_intel.analysis.local_video_inputs import _classify_subtitle_readability
from short_video_intel.analysis.local_video_inputs import _probe_looks_like_real_video


def test_build_sample_timestamps_spreads_across_duration() -> None:
    timestamps = _build_sample_timestamps(duration_sec=10.0, count=3)
    assert timestamps == [1.5, 5.0, 8.5]


def test_build_sample_timestamps_handles_zero_duration() -> None:
    assert _build_sample_timestamps(duration_sec=0.0, count=2) == [0.0, 0.0]


def test_looks_like_video_download_prefers_video_content_type() -> None:
    class DummyPath:
        suffix = ".json"

    assert _looks_like_video_download({"status": "success", "content_type": "video/mp4"}, DummyPath())
    assert not _looks_like_video_download({"status": "success", "content_type": "image/png"}, DummyPath())


def test_probe_looks_like_real_video_filters_png_pipe() -> None:
    assert not _probe_looks_like_real_video(
        {
            "ok": True,
            "duration_sec": 0.0,
            "format_name": "png_pipe",
            "video": {"codec": "png", "width": 100, "height": 100},
        }
    )
    assert not _probe_looks_like_real_video(
        {
            "ok": True,
            "duration_sec": 12.3,
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "video": {"codec": "", "width": 0, "height": 0},
        }
    )
    assert _probe_looks_like_real_video(
        {
            "ok": True,
            "duration_sec": 12.3,
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "video": {"codec": "hevc", "width": 1080, "height": 1920},
        }
    )


def test_build_content_feature_summary_maps_probe_to_tags() -> None:
    summary = _build_content_feature_summary(
        probe={
            "duration_sec": 22.0,
            "bit_rate": 1800000,
            "video": {"width": 1080, "height": 1920},
        },
        frame_stats={
            "summary": {
                "brightness_level": "balanced",
                "saturation_level": "medium",
                "contrast_level": "high",
            }
        },
    )
    assert summary["duration_bucket"] == "medium"
    assert summary["orientation"] == "portrait"
    assert summary["resolution_tier"] == "high"
    assert summary["bitrate_tier"] == "medium"
    assert "clean_educational_style" in summary["visual_tags"]


def test_classify_subtitle_readability() -> None:
    assert _classify_subtitle_readability(avg_brightness=120, avg_contrast=180) == "high"
    assert _classify_subtitle_readability(avg_brightness=120, avg_contrast=100) == "medium"
    assert _classify_subtitle_readability(avg_brightness=120, avg_contrast=40) == "low"
