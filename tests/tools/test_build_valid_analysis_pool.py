from __future__ import annotations

import csv
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_valid_analysis_pool.py"
    spec = importlib.util.spec_from_file_location("build_valid_analysis_pool", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_video_ids(path: Path) -> list[str]:
    """读取 CSV 中的视频 ID，避免测试重复展开行结构。"""
    return [row["video_id"] for row in _read_csv(path)]


class BuildValidAnalysisPoolToolTestCase(unittest.TestCase):
    def test_main_filters_invalid_and_anomaly_by_default(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            input_dir = workspace / "data" / "processed"
            output_dir = workspace / "data" / "processed"
            self._prepare_fixture(input_dir, workspace)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["valid_video_count"], 1)
            self.assertEqual(summary["valid_video_metric_count"], 1)
            self.assertEqual(summary["valid_comment_count"], 1)

            videos = _read_csv(output_dir / "valid_videos.csv")
            metrics = _read_csv(output_dir / "valid_video_metrics.csv")
            comments = _read_csv(output_dir / "valid_comments.csv")

            self.assertEqual([row["video_id"] for row in videos], ["1111111111"])
            self.assertEqual([row["video_id"] for row in metrics], ["1111111111"])
            self.assertEqual([row["video_id"] for row in comments], ["1111111111"])
            self.assertEqual(_read_video_ids(output_dir / "videos.csv"), ["1111111111"])
            self.assertEqual(_read_video_ids(output_dir / "video_metrics.csv"), ["1111111111"])
            self.assertEqual(_read_video_ids(output_dir / "comments.csv"), ["1111111111"])

    def test_main_supports_keep_suspicious(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            input_dir = workspace / "custom_input"
            output_dir = workspace / "custom_output"
            report_path = workspace / "custom_report" / "dq.json"
            self._prepare_fixture(input_dir, workspace, report_path)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--input-dir",
                        str(input_dir),
                        "--output-dir",
                        str(output_dir),
                        "--quality-report",
                        str(report_path),
                        "--keep-suspicious",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertTrue(summary["keep_suspicious"])
            self.assertEqual(summary["valid_video_count"], 2)

            videos = _read_csv(output_dir / "valid_videos.csv")
            metrics = _read_csv(output_dir / "valid_video_metrics.csv")
            comments = _read_csv(output_dir / "valid_comments.csv")

            self.assertEqual([row["video_id"] for row in videos], ["1111111111", "3333333333"])
            self.assertEqual([row["video_id"] for row in metrics], ["1111111111", "3333333333"])
            self.assertEqual([row["video_id"] for row in comments], ["1111111111", "3333333333"])
            self.assertEqual(_read_video_ids(output_dir / "videos.csv"), ["1111111111", "3333333333"])

    def test_main_requires_homepage_observed_when_enabled(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            input_dir = workspace / "data" / "processed"
            output_dir = workspace / "clean"
            self._prepare_fixture(input_dir, workspace)
            self._prepare_homepage_fixture(workspace)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--output-dir",
                        str(output_dir),
                        "--require-homepage-observed",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertTrue(summary["require_homepage_observed"])
            self.assertEqual(summary["homepage_observed_video_count"], 1)
            self.assertEqual(_read_video_ids(output_dir / "videos.csv"), ["1111111111"])

    def test_main_requires_detail_account_mention_when_enabled(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            input_dir = workspace / "data" / "processed"
            output_dir = workspace / "clean"
            self._prepare_fixture(input_dir, workspace)
            self._prepare_detail_fixture(workspace)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(
                    [
                        "--workspace",
                        str(workspace),
                        "--output-dir",
                        str(output_dir),
                        "--keep-suspicious",
                        "--require-detail-account-mention",
                    ]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertTrue(summary["require_detail_account_mention"])
            self.assertEqual(_read_video_ids(output_dir / "videos.csv"), ["1111111111"])

    def _prepare_fixture(self, input_dir: Path, workspace: Path, report_path: Path | None = None) -> None:
        report_file = report_path or workspace / "artifacts" / "status" / "data_quality_report.json"
        video_headers = ["video_id", "detail_artifact_path", "mp4_path", "title", "account_id"]
        metric_headers = ["video_id", "view_count"]
        comment_headers = ["comment_id", "video_id", "text"]

        _write_csv(
            input_dir / "videos.csv",
            video_headers,
            [
                {
                    "video_id": "1111111111",
                    "detail_artifact_path": "detail/1.json",
                    "mp4_path": "mp4/1.mp4",
                    "title": "有效",
                    "account_id": "测试账?号",
                },
                {
                    "video_id": "3333333333",
                    "detail_artifact_path": "detail/3.json",
                    "mp4_path": "mp4/3.mp4",
                    "title": "可疑指标",
                    "account_id": "测试账号",
                },
                {
                    "video_id": "2222222222",
                    "detail_artifact_path": "detail/2.json",
                    "mp4_path": "",
                    "title": "无下载",
                    "account_id": "测试账号",
                },
                {
                    "video_id": "4444444444",
                    "detail_artifact_path": "",
                    "mp4_path": "mp4/4.mp4",
                    "title": "无详情",
                    "account_id": "测试账号",
                },
                {
                    "video_id": "5555555555",
                    "detail_artifact_path": "detail/5.json",
                    "mp4_path": "mp4/5.mp4",
                    "title": "无效ID名单",
                    "account_id": "测试账号",
                },
                {
                    "video_id": "bad-id",
                    "detail_artifact_path": "detail/bad.json",
                    "mp4_path": "mp4/bad.mp4",
                    "title": "格式非法",
                    "account_id": "测试账号",
                },
            ],
        )
        _write_csv(
            input_dir / "video_metrics.csv",
            metric_headers,
            [
                {"video_id": "1111111111", "view_count": "10"},
                {"video_id": "3333333333", "view_count": "20"},
                {"video_id": "2222222222", "view_count": "30"},
                {"video_id": "5555555555", "view_count": "40"},
            ],
        )
        _write_csv(
            input_dir / "comments.csv",
            comment_headers,
            [
                {"comment_id": "c1", "video_id": "1111111111", "text": "ok"},
                {"comment_id": "c2", "video_id": "3333333333", "text": "sus"},
                {"comment_id": "c3", "video_id": "2222222222", "text": "missing"},
                {"comment_id": "c4", "video_id": "5555555555", "text": "invalid"},
            ],
        )
        _write_json(
            report_file,
            {
                "invalid_video_id": {
                    "records": [
                        {"detected_video_id": "5555555555"},
                        {"detected_video_id": "undefined"},
                    ]
                },
                "coverage": {"detail_without_download_video_ids": ["2222222222"]},
                "detail_metric_anomalies": {"records": [{"video_id": "3333333333"}]},
            },
        )

    def _prepare_homepage_fixture(self, workspace: Path) -> None:
        _write_json(
            workspace / "artifacts" / "collector" / "batch" / "batch_homepage_crawl_test.json",
            {
                "batch": {
                    "results": [
                        {
                            "target": {"source_name": "测试账号", "homepage_url": "https://example.test/user/1"},
                            "crawl_result": {"videos": [{"video_id": "1111111111"}]},
                        }
                    ]
                }
            },
        )

    def _prepare_detail_fixture(self, workspace: Path) -> None:
        _write_json(
            workspace / "detail" / "1.json",
            {
                "raw": {
                    "body_text_preview": "这是测试账号发布的视频",
                }
            },
        )
        _write_json(
            workspace / "detail" / "3.json",
            {
                "raw": {
                    "body_text_preview": "这里没有作者信息",
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
