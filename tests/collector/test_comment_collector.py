import unittest

from short_video_intel.collector.comment_collector import (
    _build_comment_backend,
    _extract_comment_section,
    _extract_comments_from_body_text,
    _extract_comments_from_response,
    _extract_comments_from_response_payload,
    _filter_real_comment_items,
    _has_empty_comment_state,
)


class CommentCollectorTestCase(unittest.TestCase):
    def test_extract_comments_from_body_text_supports_stub_entries_when_content_not_rendered(self) -> None:
        body_text = """
        全部评论
        留下你的精彩评论吧
        柯尔嫚·美容
        ...
        1周前·四川

        0

        分享
        回复
        三生
        ...
        1月前·湖南

        0

        分享
        回复
        暂时没有更多评论
        推荐视频
        """.strip()

        comments, warnings, diagnostics = _extract_comments_from_body_text(
            body_text,
            "https://www.douyin.com/video/7609547134546791690",
        )

        self.assertEqual(len(comments), 2)
        self.assertIn("body_text_stub_extraction_used", warnings)
        self.assertEqual(diagnostics["source_hits"]["body_text_stub_blocks"], 2)
        self.assertEqual(comments[0]["author_name"], "柯尔嫚·美容")
        self.assertTrue(comments[0]["raw"]["stub"])
        self.assertTrue(comments[0]["content"].startswith("[评论正文未渲染]"))
        self.assertEqual(comments[1]["author_name"], "三生")

    def test_extract_comments_from_body_text_supports_no_recommendation_tail_marker(self) -> None:
        body_text = """
        全部评论
        小王同学
        第一次看你的视频
        2天前·浙江
        12
        分享
        回复
        暂时没有更多评论
        """.strip()

        comments, warnings, diagnostics = _extract_comments_from_body_text(
            body_text,
            "https://www.douyin.com/video/7609547134546791691",
        )

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author_name"], "小王同学")
        self.assertEqual(comments[0]["content"], "第一次看你的视频")
        self.assertEqual(comments[0]["like_count"], 12)
        self.assertEqual(comments[0]["raw"]["source"], "body_text_lines")
        self.assertEqual(diagnostics["source_hits"]["body_text_blocks"], 1)
        self.assertIn("body_text_line_extraction_used", warnings)

    def test_extract_comments_from_body_text_supports_absolute_date_meta_line(self) -> None:
        body_text = """
        全部评论
        小月亮
        我来学习了
        03-21·上海
        0
        分享
        回复
        推荐视频
        """.strip()

        comments, warnings, diagnostics = _extract_comments_from_body_text(
            body_text,
            "https://www.douyin.com/video/7609547134546791692",
        )

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author_name"], "小月亮")
        self.assertEqual(comments[0]["content"], "我来学习了")
        self.assertEqual(comments[0]["raw"]["meta_line"], "03-21·上海")
        self.assertEqual(diagnostics["source_hits"]["body_text_blocks"], 1)
        self.assertIn("body_text_line_extraction_used", warnings)

    def test_empty_comment_state_is_preserved_after_search_marker(self) -> None:
        body_text = """
        全部评论
        留下你的精彩评论吧
        大家都在搜：
        暂无评论
        抢首评
        推荐视频
        """.strip()

        section = _extract_comment_section(body_text)

        self.assertTrue(_has_empty_comment_state(section))
        self.assertIn("暂无评论", section)
        self.assertEqual(_build_comment_backend(comments=[], stop_reason="empty_comment_state"), "playwright:empty-state")

    def test_extract_comments_from_response_payload_maps_common_comment_fields(self) -> None:
        payload = {
            "data": {
                "comments": [
                    {
                        "cid": "comment-1",
                        "text": "第一条评论",
                        "digg_count": 23,
                        "reply_count": 4,
                        "create_time": 1710000000,
                        "user": {"uid": "user-1", "nickname": "测试作者"},
                    }
                ]
            }
        }

        comments, diagnostics = _extract_comments_from_response_payload(
            payload,
            "https://www.douyin.com/video/7609547134546791693",
            "https://api.example.com/aweme/v1/web/comment/list/",
        )

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["comment_id"], "comment-1")
        self.assertEqual(comments[0]["author_id"], "user-1")
        self.assertEqual(comments[0]["author_name"], "测试作者")
        self.assertEqual(comments[0]["content"], "第一条评论")
        self.assertEqual(comments[0]["like_count"], 23)
        self.assertEqual(comments[0]["reply_count"], 4)
        self.assertEqual(comments[0]["created_at"], "1710000000")
        self.assertEqual(comments[0]["raw"]["source"], "network_response_json")
        self.assertGreaterEqual(diagnostics["source_hits"]["response_payloads"], 2)
        self.assertEqual(diagnostics["source_hits"]["response_objects"], 1)

    def test_extract_comments_from_response_keeps_failures_in_warning_without_interrupting(self) -> None:
        class FakeResponse:
            url = "https://api.example.com/aweme/v1/web/comment/list/"

            @staticmethod
            def header_value(name: str) -> str:
                return "application/json" if name == "content-type" else ""

            @staticmethod
            def json() -> dict[str, str]:
                raise ValueError("broken json")

        comments, warnings, diagnostics = _extract_comments_from_response(
            FakeResponse(),
            "https://www.douyin.com/video/7609547134546791694",
        )

        self.assertEqual(comments, [])
        self.assertTrue(any("comment response parse failed" in item for item in warnings))
        self.assertEqual(diagnostics["parse_failures"], 1)
        self.assertEqual(diagnostics["response_listener_failures"], 1)

    def test_extract_comments_from_response_ignores_platform_setting_json(self) -> None:
        class FakeResponse:
            url = "https://live.douyin.com/webcast/setting/"

            @staticmethod
            def header_value(name: str) -> str:
                return "application/json" if name == "content-type" else ""

            @staticmethod
            def json() -> dict[str, object]:
                return {"data": {"items": [{"content": "直播间带货榜说明"}]}}

        comments, warnings, diagnostics = _extract_comments_from_response(
            FakeResponse(),
            "https://www.douyin.com/video/7609547134546791695",
        )

        self.assertEqual(comments, [])
        self.assertEqual(warnings, [])
        self.assertEqual(diagnostics["parse_failures"], 0)

    def test_build_comment_backend_marks_network_response_source(self) -> None:
        comments = [{"content": "来自接口的评论", "raw": {"source": "network_response_json"}}]
        self.assertEqual(
            _build_comment_backend(comments=comments, stop_reason="placeholder_only"),
            "playwright:network-response-v1",
        )

    def test_filter_real_comment_items_drops_platform_setting_noise(self) -> None:
        items = [
            {
                "content": "直播间带货榜说明",
                "raw": {"response_url": "https://live.douyin.com/webcast/setting/"},
            },
            {
                "content": "真实评论",
                "author_id": "user-1",
                "raw": {"response_url": "https://www-hj.douyin.com/aweme/v1/web/comment/list/"},
            },
        ]

        filtered = _filter_real_comment_items(items)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["content"], "真实评论")


if __name__ == "__main__":
    unittest.main()
