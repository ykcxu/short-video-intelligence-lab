import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_comment_backfill_targets.py"
    spec = importlib.util.spec_from_file_location("build_comment_backfill_targets", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_detail(workspace: Path, name: str, payload: dict[str, object]) -> None:
    target = workspace / "artifacts" / "collector" / "video" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_comments(workspace: Path, name: str, payload: dict[str, object]) -> None:
    target = workspace / "artifacts" / "collector" / "comments" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class BuildCommentBackfillTargetsTestCase(unittest.TestCase):
    def test_main_prioritizes_positive_comment_count_without_non_empty_comments(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_detail(
                workspace,
                "video_detail_111.json",
                {
                    "video_id": "1111111111",
                    "video_url": "https://www.douyin.com/video/1111111111",
                    "comment_count": 10,
                    "like_count": 99,
                    "share_count": 11,
                    "view_count": 1000,
                },
            )
            _write_detail(
                workspace,
                "video_detail_222.json",
                {
                    "video_id": "2222222222",
                    "video_url": "https://www.douyin.com/video/2222222222",
                    "statistics": {"comment_count": 5, "digg_count": 3, "share_count": 2, "play_count": 1},
                },
            )
            _write_detail(
                workspace,
                "video_detail_333.json",
                {
                    "video_id": "3333333333",
                    "video_url": "https://www.douyin.com/video/3333333333",
                    "metrics": {"comment_count": 4, "like_count": 8, "share_count": 9, "view_count": 10},
                },
            )
            _write_comments(
                workspace,
                "video_comments_111.json",
                {
                    "video_id": "1111111111",
                    "comments": [
                        {
                            "text": "已有",
                            "raw": {"response_url": "https://www-hj.douyin.com/aweme/v1/web/comment/list/"},
                        }
                    ],
                },
            )
            _write_comments(
                workspace,
                "video_comments_222.json",
                {"video_id": "2222222222", "comments": []},
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace), "--limit", "1"])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["detail_count"], 3)
            self.assertEqual(payload["comment_video_count"], 2)
            self.assertEqual(payload["target_count"], 2)
            self.assertEqual(payload["planned_count"], 1)
            self.assertEqual(payload["targets"][0]["video_id"], "2222222222")
            self.assertEqual(payload["targets"][0]["comment_count"], 5)
            self.assertEqual(payload["targets"][0]["like_count"], 3)
            self.assertEqual(payload["targets"][0]["share_count"], 2)
            self.assertEqual(payload["targets"][0]["view_count"], 1)
            self.assertEqual(payload["targets"][0]["comment_expected_status"], "comment_expected")
            self.assertTrue(payload["targets"][0]["should_backfill_comment"])
            self.assertTrue(payload["targets"][0]["has_comment_artifact"])

            output_json = workspace / "artifacts" / "collector" / "comment_backfill_targets.json"
            output_txt = workspace / "artifacts" / "collector" / "comment_backfill_targets.txt"
            self.assertTrue(output_json.exists())
            self.assertTrue(output_txt.exists())
            saved_payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(saved_payload["targets"][0]["video_id"], "2222222222")
            self.assertEqual(
                output_txt.read_text(encoding="utf-8"),
                "https://www.douyin.com/video/2222222222\n",
            )

    def test_main_uses_fallback_id_and_orders_by_priority_then_comment_count(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_detail(
                workspace,
                "video_detail_444.json",
                {
                    "video_url": "https://www.douyin.com/video/4444444444",
                    "commentCount": "8",
                },
            )
            _write_detail(
                workspace,
                "video_detail_555.json",
                {
                    "video_id": "5555555555",
                    "comment_count": 0,
                },
            )
            _write_comments(
                workspace,
                "video_comments_555.json",
                {"video_id": "5555555555", "data": {"comments": []}},
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual([item["video_id"] for item in payload["targets"]], ["4444444444", "5555555555"])
            self.assertEqual(payload["targets"][0]["priority"], 1)
            self.assertEqual(payload["targets"][1]["priority"], 0)
            self.assertEqual(payload["targets"][0]["comment_expected_status"], "comment_expected")
            self.assertEqual(payload["targets"][1]["comment_expected_status"], "no_comment_expected")
            self.assertTrue(payload["targets"][0]["should_backfill_comment"])
            self.assertFalse(payload["targets"][1]["should_backfill_comment"])
            self.assertEqual(payload["targets"][0]["video_url"], "https://www.douyin.com/video/4444444444")
            output_txt = workspace / "artifacts" / "collector" / "comment_backfill_targets.txt"
            self.assertEqual(
                output_txt.read_text(encoding="utf-8"),
                "https://www.douyin.com/video/4444444444\nhttps://www.douyin.com/video/5555555555\n",
            )

    def test_main_does_not_treat_platform_setting_text_as_completed_comments(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_detail(
                workspace,
                "video_detail_666.json",
                {
                    "video_id": "6666666666",
                    "video_url": "https://www.douyin.com/video/6666666666",
                    "comment_count": 3,
                },
            )
            _write_comments(
                workspace,
                "video_comments_666.json",
                {
                    "video_id": "6666666666",
                    "comments": [
                        {
                            "content": "直播间带货榜说明",
                            "raw": {"response_url": "https://live.douyin.com/webcast/setting/"},
                        }
                    ],
                },
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["target_count"], 1)
            self.assertTrue(payload["targets"][0]["has_comment_artifact"])
            self.assertFalse(payload["targets"][0]["has_non_empty_comments"])


if __name__ == "__main__":
    unittest.main()
