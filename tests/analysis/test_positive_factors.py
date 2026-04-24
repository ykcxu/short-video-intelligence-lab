from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from short_video_intel.analysis.positive_factors import build_positive_factors_report
from tools.build_positive_factors_report import main as build_positive_factors_main


class PositiveFactorsAnalysisTestCase(unittest.TestCase):
    def test_build_positive_factors_report_sorts_and_extracts_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            self._write_sample_csvs(input_dir)
            report = build_positive_factors_report(input_dir, top_n=2)
            account = self._find_account(report, "a1")
            self.assertEqual(account["top_videos"][0]["video_id"], "v1")
            self.assertEqual(account["bottom_videos"][0]["video_id"], "v3")
            self.assertEqual(account["high_performance_title_keywords"][0]["keyword"], "教程")
            comment_keywords = [item["keyword"] for item in account["comment_keywords"]]
            self.assertIn("有用", comment_keywords)

    def test_build_positive_factors_report_requires_processed_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            self._write_csv(input_dir / "videos.csv", ["video_id", "account_id", "title"], [{"video_id": "x", "account_id": "a", "title": "t"}])
            with self.assertRaises(FileNotFoundError) as context:
                build_positive_factors_report(input_dir, top_n=3)
            self.assertIn("build_dataset", str(context.exception))

    def test_cli_main_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            input_dir = workspace / "data" / "processed"
            input_dir.mkdir(parents=True, exist_ok=True)
            self._write_sample_csvs(input_dir)
            exit_code = build_positive_factors_main(["--workspace", str(workspace), "--top-n", "1"])
            self.assertEqual(exit_code, 0)
            json_path = workspace / "artifacts" / "analysis" / "positive_factors_report.json"
            md_path = workspace / "artifacts" / "analysis" / "positive_factors_report.md"
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["top_n"], 1)
            self.assertGreater(payload["account_count"], 0)

    def _write_sample_csvs(self, input_dir: Path) -> None:
        input_dir.mkdir(parents=True, exist_ok=True)
        self._write_csv(
            input_dir / "videos.csv",
            ["video_id", "account_id", "title"],
            [
                {"video_id": "v1", "account_id": "a1", "title": "教程 爆款"},
                {"video_id": "v2", "account_id": "a1", "title": "教程 进阶"},
                {"video_id": "v3", "account_id": "a1", "title": "日常 记录"},
                {"video_id": "v4", "account_id": "a2", "title": "舞蹈 挑战"},
            ],
        )
        self._write_csv(
            input_dir / "video_metrics.csv",
            ["video_id", "view_count", "like_count", "comment_count", "share_count"],
            [
                {"video_id": "v1", "view_count": "1000", "like_count": "200", "comment_count": "80", "share_count": "40"},
                {"video_id": "v2", "view_count": "800", "like_count": "120", "comment_count": "30", "share_count": "10"},
                {"video_id": "v3", "view_count": "700", "like_count": "20", "comment_count": "2", "share_count": "1"},
                {"video_id": "v4", "view_count": "900", "like_count": "150", "comment_count": "20", "share_count": "5"},
            ],
        )
        self._write_csv(
            input_dir / "comments.csv",
            ["video_id", "content"],
            [
                {"video_id": "v1", "content": "有用 有用 干货"},
                {"video_id": "v2", "content": "教程 清晰"},
                {"video_id": "v3", "content": "一般"},
                {"video_id": "v4", "content": "不错"},
            ],
        )

    def _find_account(self, report: dict, account_id: str) -> dict:
        for item in report.get("accounts", []):
            if item.get("account_id") == account_id:
                return item
        self.fail(f"未找到账号：{account_id}")
        return {}

    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
