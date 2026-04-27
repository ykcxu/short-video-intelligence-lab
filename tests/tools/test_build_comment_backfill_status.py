import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_comment_backfill_status.py"
    spec = importlib.util.spec_from_file_location("build_comment_backfill_status", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_comments(workspace: Path, name: str, payload: dict[str, object]) -> None:
    target = workspace / "artifacts" / "collector" / "comments" / name
    _write_json(target, payload)


class BuildCommentBackfillStatusTestCase(unittest.TestCase):
    def test_main_counts_real_noise_empty_nested_and_targets(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_comments(
                workspace,
                "video_comments_1111111111.json",
                {
                    "video_id": "1111111111",
                    "comments": [
                        {"text": "接口命中", "raw": {"response_url": "https://x/aweme/v1/web/comment/list/?a=1"}},
                        {"text": "作者命中", "author_id": "u1"},
                        {"text": "昵称命中", "author_name": "用户A", "raw": {"stub": False}},
                    ],
                },
            )
            _write_comments(
                workspace,
                "video_comments_2222222222.json",
                {"video_id": "2222222222", "comments": [{"text": "配置噪声", "raw": {"stub": True}}]},
            )
            _write_comments(workspace, "video_comments_3333333333.json", {"video_id": "3333333333", "comments": []})
            _write_comments(
                workspace,
                "video_comments_4444444444.json",
                {
                    "video_id": "4444444444",
                    "data": {
                        "comments": [
                            {
                                "text": "回复接口",
                                "raw": {"response_url": "https://x/aweme/v1/web/comment/list/reply/?cursor=0"},
                            }
                        ]
                    },
                },
            )
            _write_json(
                workspace / "artifacts" / "collector" / "comment_backfill_targets.json",
                {"detail_count": 9, "target_count": 4, "planned_count": 3, "comment_video_count": 2},
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            payload = json.loads((workspace / "artifacts" / "status" / "comment_backfill_status.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["comment_artifact_count"], 4)
            self.assertEqual(payload["comment_video_count"], 4)
            self.assertEqual(payload["real_comment_video_count"], 2)
            self.assertEqual(payload["empty_comment_video_count"], 1)
            self.assertEqual(payload["noise_only_video_count"], 1)
            self.assertEqual(payload["total_comment_count"], 5)
            self.assertEqual(payload["total_real_comment_count"], 4)
            self.assertEqual(
                payload["comment_backfill_targets"],
                {"detail_count": 9, "target_count": 4, "planned_count": 3, "comment_video_count": 2},
            )
            self.assertEqual(len(payload["recent_artifacts"]), 4)
            self.assertIn("json_output", json.loads(stdout.getvalue()))

            markdown = (workspace / "artifacts" / "status" / "comment_backfill_status.md").read_text(encoding="utf-8")
            self.assertIn("# 评论补采状态报告", markdown)
            self.assertIn("- 真实评论总数：4", markdown)
            self.assertIn("## 最近 20 个评论产物", markdown)
            self.assertIn("| video_comments_2222222222.json | 2222222222 | 1 | 0 | 仅噪声 |", markdown)

    def test_real_comment_predicates_cover_publish_and_author_name_stub(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_comments(
                workspace,
                "video_comments_5555555555.json",
                {
                    "video_id": "5555555555",
                    "comments": [
                        {"raw": {"response_url": "https://x/aweme/v1/web/comment/publish/"}},
                        {"author_name": "真实昵称", "raw": {"stub": False}},
                        {"author_name": "占位昵称", "raw": {"stub": True}},
                    ],
                },
            )

            report = module.build_comment_backfill_status(workspace)

            self.assertEqual(report["comment_artifact_count"], 1)
            self.assertEqual(report["real_comment_video_count"], 1)
            self.assertEqual(report["noise_only_video_count"], 0)
            self.assertEqual(report["total_comment_count"], 3)
            self.assertEqual(report["total_real_comment_count"], 2)
            self.assertEqual(report["recent_artifacts"][0]["real_comment_count"], 2)

    def test_extracts_video_id_from_video_url_before_filename_hash(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_comments(
                workspace,
                "video_comments_hash_20260427.json",
                {
                    "video_url": "https://www.douyin.com/video/7777777777",
                    "comments": [{"author_id": "u1", "text": "真实评论"}],
                },
            )

            report = module.build_comment_backfill_status(workspace)

            self.assertEqual(report["recent_artifacts"][0]["video_id"], "7777777777")


if __name__ == "__main__":
    unittest.main()
