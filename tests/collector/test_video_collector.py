from __future__ import annotations

from short_video_intel.collector.video_collector import _extract_body_metric_window, _extract_metrics_from_body_text


def test_extract_body_metric_window_includes_publish_marker() -> None:
    text = "前文内容 举报 发布时间：2024-04-04 08:21 后文内容"

    section = _extract_body_metric_window(text)

    assert "发布时间" in section
    assert "举报" in section


def test_extract_metrics_from_body_text_prefers_body_sequence() -> None:
    text = (
        "前文内容 举报 "
        "粉丝说叫我买个挂脖子的支架！体验了下就是鱼看得到我我看不到鱼#杀鱼技术 #片鱼片教程 #刀工 "
        "1234 249 211 586 举报 发布时间：2024-04-04 08:21"
    )

    metrics, diagnostics = _extract_metrics_from_body_text(text)

    assert diagnostics["matched"] is True
    assert metrics["like_count"] == 1234
    assert metrics["comment_count"] == 249
    assert metrics["share_count"] == 586
