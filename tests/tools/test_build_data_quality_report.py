import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_data_quality_report.py"
    spec = importlib.util.spec_from_file_location("build_data_quality_report", tool_path)
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


def _write_download(workspace: Path, relative_path: str) -> None:
    target = workspace / "downloads" / "artifact" / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"mp4")


class BuildDataQualityReportTestCase(unittest.TestCase):
    def test_main_builds_quality_statistics(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_download(workspace, "账号A/账号A_1111111111.mp4")
            _write_download(workspace, "账号A/账号A_2222222222.mp4")
            _write_download(workspace, "账号A/账号A_bad-id.mp4")

            _write_detail(
                workspace,
                "video_detail_1111111111.json",
                {
                    "video_id": "1111111111",
                    "metrics": {
                        "view_count": 7,
                        "like_count": 7,
                        "comment_count": 7,
                        "share_count": 7,
                    },
                },
            )
            _write_detail(
                workspace,
                "video_detail_3333333333.json",
                {
                    "video_url": "https://www.douyin.com/video/3333333333",
                    "metrics": {
                        "view_count": 100,
                        "like_count": -1,
                        "comment_count": 0,
                        "share_count": 1,
                    },
                },
            )
            _write_detail(
                workspace,
                "video_detail_4444444444.json",
                {
                    "video_url": "https://www.douyin.com/video/4444444444",
                    "metrics": {
                        "view_count": 1000000001,
                        "like_count": 2,
                        "comment_count": 3,
                        "share_count": 4,
                    },
                },
            )
            _write_detail(workspace, "video_detail_bad.json", {"video_id": "undefined"})

            _write_comments(
                workspace,
                "video_comments_1111111111.json",
                {
                    "video_id": "1111111111",
                    "comments": [],
                    "scan_meta": {"stop_reason": "placeholder_only"},
                },
            )
            _write_comments(
                workspace,
                "video_comments_2222222222.json",
                {
                    "video_id": "2222222222",
                    "comments": [{"text": "ok"}],
                    "scan_meta": {},
                },
            )
            _write_comments(
                workspace,
                "video_comments_3333333333.json",
                {
                    "video_id": "3333333333",
                    "comments": [],
                    "scan_meta": {
                        "payload_diagnostics": {
                            "rounds": [{"has_empty_comment_state": True, "has_comment_placeholder": False}]
                        }
                    },
                },
            )
            _write_comments(workspace, "video_comments_bad.json", {"video_id": "abc"})

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            command_output = json.loads(stdout.getvalue())
            json_output = Path(command_output["json_output"])
            md_output = Path(command_output["md_output"])
            self.assertTrue(json_output.exists())
            self.assertTrue(md_output.exists())

            payload = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertEqual(payload["invalid_video_id"]["detail_count"], 1)
            self.assertEqual(payload["invalid_video_id"]["comment_count"], 1)
            self.assertEqual(payload["invalid_video_id"]["download_count"], 1)
            self.assertEqual(payload["coverage"]["download_without_detail_video_ids"], ["2222222222"])
            self.assertEqual(
                payload["coverage"]["detail_without_download_video_ids"],
                ["3333333333", "4444444444"],
            )
            self.assertEqual(payload["detail_metric_anomalies"]["count"], 3)
            self.assertEqual(payload["comment_quality"]["placeholder_count"], 1)
            self.assertEqual(payload["comment_quality"]["empty_comment_state_count"], 1)
            self.assertEqual(payload["comment_quality"]["non_empty_comment_video_count"], 1)

            markdown = md_output.read_text(encoding="utf-8")
            self.assertIn("数据质量报表（v1）", markdown)
            self.assertIn("指标异常：3", markdown)
            self.assertIn("placeholder 评论：1", markdown)

    def test_main_supports_custom_output_paths(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_download(workspace, "账号B/账号B_9999999999.mp4")
            _write_detail(
                workspace,
                "video_detail_9999999999.json",
                {"video_id": "9999999999", "metrics": {"view_count": 1, "like_count": 2, "comment_count": 3, "share_count": 4}},
            )
            _write_comments(
                workspace,
                "video_comments_9999999999.json",
                {"video_id": "9999999999", "comments": []},
            )

            custom_json = workspace / "tmp" / "quality.json"
            custom_md = workspace / "tmp" / "quality.md"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--json-output",
                        str(custom_json),
                        "--md-output",
                        str(custom_md),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(custom_json.exists())
            self.assertTrue(custom_md.exists())
            payload = json.loads(custom_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["detail_metric_anomalies"]["count"], 0)
            self.assertEqual(payload["coverage"]["download_without_detail_count"], 0)
            self.assertEqual(payload["coverage"]["detail_without_download_count"], 0)


if __name__ == "__main__":
    unittest.main()
