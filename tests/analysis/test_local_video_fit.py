from short_video_intel.analysis.local_video_fit import analyze_local_video_item


def test_analyze_local_video_item_prefers_portrait_balanced_medium_duration() -> None:
    result = analyze_local_video_item(
        {
            "content_features": {
                "duration_sec": 28.0,
                "duration_bucket": "medium",
                "orientation": "portrait",
                "resolution_tier": "high",
                "bitrate_tier": "medium",
                "visual_tone": {
                    "brightness_level": "balanced",
                    "saturation_level": "medium",
                    "contrast_level": "high",
                    "visual_rhythm_hint": "stable",
                },
                "visual_tags": ["possible_talking_head", "clean_educational_style"],
            },
            "subtitle_hints": {"readability_hint": "high"},
            "frame_feature_summary": {
                "summary": {
                    "avg_brightness": 110.0,
                    "avg_saturation": 24.0,
                    "avg_contrast_span": 160.0,
                }
            },
        }
    )
    assert result["fit_level"] == "high"
    assert result["fit_score"] >= 75


def test_analyze_local_video_item_penalizes_dark_landscape_short_video() -> None:
    result = analyze_local_video_item(
        {
            "content_features": {
                "duration_sec": 6.0,
                "duration_bucket": "short",
                "orientation": "landscape",
                "resolution_tier": "low",
                "bitrate_tier": "low",
                "visual_tone": {
                    "brightness_level": "dark",
                    "saturation_level": "low",
                    "contrast_level": "low",
                    "visual_rhythm_hint": "mixed",
                },
                "visual_tags": ["low_exposure_risk"],
            },
            "subtitle_hints": {"readability_hint": "low"},
        }
    )
    assert result["fit_level"] == "low"
    assert result["fit_score"] < 50
