from short_video_intel.downloader.browser_media import _candidate_sort_key
from short_video_intel.downloader.browser_media import _is_excluded_media_url


def test_is_excluded_media_url_blocks_known_client_download() -> None:
    assert _is_excluded_media_url(
        "https://lf3-static.bytednsdoc.com/obj/eden-cn/download/douyin_pc_client.mp4"
    )


def test_candidate_sort_prefers_expected_video_id_and_douyinvod() -> None:
    expected_video_id = "7582049083490143488"
    real_candidate = {
        "url": (
            "https://v26-dy-o.zjcdn.com/xxx~tplv-dy-resize:video_mp4?"
            "__vid=7582049083490143488&mime_type=video_mp4"
        ),
        "source": "network_response",
        "mime_type": "video/mp4",
        "note": "",
    }
    client_candidate = {
        "url": "https://lf3-static.bytednsdoc.com/obj/eden-cn/download/douyin_pc_client.mp4",
        "source": "network_response",
        "mime_type": "video/mp4",
        "note": "",
    }

    assert _candidate_sort_key(real_candidate, expected_video_id=expected_video_id) < _candidate_sort_key(
        client_candidate,
        expected_video_id=expected_video_id,
    )
