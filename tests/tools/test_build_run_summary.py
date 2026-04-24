import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_run_summary.py"
    spec = importlib.util.spec_from_file_location("build_run_summary", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_log(path: Path, content: str, unix_ts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.touch()
    path.chmod(0o666)
    import os

    os.utime(path, (unix_ts, unix_ts))


class BuildRunSummaryTestCase(unittest.TestCase):
    def test_main_outputs_summary_and_warnings(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_json(
                workspace / "artifacts" / "status" / "project_progress.json",
                {
                    "generated_at": "2026-04-24T10:00:00+00:00",
                    "progress": {
                        "download_goal_progress": 1.0,
                        "detail_coverage_progress": 0.8,
                        "comment_quality_progress": 0.5,
                        "download_goal_downloaded_videos": 80,
                        "download_goal_target_videos": 100,
                    },
                    "accounts": [{"name": "A"}, {"name": "B"}],
                },
            )
            _write_json(
                workspace / "artifacts" / "status" / "data_quality_report.json",
                {
                    "generated_at": "2026-04-24T11:00:00+00:00",
                    "invalid_video_id": {"count": 3},
                    "coverage": {"download_without_detail_count": 2, "detail_without_download_count": 1},
                    "detail_metric_anomalies": {"count": 4},
                },
            )

            _write_log(workspace / "artifacts" / "run-logs" / "task_batch3_20260424.out.log", "ok", 1700000000)
            _write_log(workspace / "artifacts" / "run-logs" / "task_batch3_20260424.err.log", "stacktrace", 1700000010)
            _write_log(workspace / "artifacts" / "run-logs" / "task_misc_20260424.out.log", "done", 1700000020)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace), "--log-limit", "2"])

            self.assertEqual(exit_code, 0)
            command_output = json.loads(stdout.getvalue())
            json_output = Path(command_output["json_output"])
            md_output = Path(command_output["md_output"])
            self.assertTrue(json_output.exists())
            self.assertTrue(md_output.exists())

            payload = json.loads(json_output.read_text(encoding="utf-8"))
            self.assertIn("状态文件缺失：artifacts/analysis/positive_factors_report.json", payload["warnings"])
            self.assertIn("状态文件缺失：artifacts/analysis/positive_factors_strict_valid_report.json", payload["warnings"])
            self.assertIn("状态文件缺失：artifacts/status/strict_pool_gap_report.json", payload["warnings"])
            self.assertIn("状态文件缺失：artifacts/analysis/strict_pool_backfill_targets.json", payload["warnings"])
            self.assertEqual(payload["run_logs"]["included_file_count"], 2)
            self.assertTrue(payload["run_logs"]["has_non_empty_error_log"])
            self.assertEqual(payload["run_logs"]["non_empty_error_log_count"], 1)
            self.assertEqual(payload["status_reports"]["project_progress"]["highlights"]["account_count"], 2)

            markdown = md_output.read_text(encoding="utf-8")
            self.assertIn("运行状态总览", markdown)
            self.assertIn("错误日志数", markdown)
            self.assertIn("状态文件缺失：artifacts/analysis/positive_factors_report.json", markdown)

    def test_main_supports_custom_output_and_missing_log_dir(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_json(workspace / "artifacts" / "analysis" / "positive_factors_report.json", {"account_count": 9, "top_n": 5})
            _write_json(
                workspace / "artifacts" / "analysis" / "positive_factors_strict_valid_report.json",
                {"account_count": 8, "top_n": 10},
            )
            _write_json(
                workspace / "artifacts" / "status" / "strict_pool_gap_report.json",
                {"input_video_count": 10, "valid_video_count": 6, "overall_retention_rate": 0.6, "accounts": [{"priority": "高"}]},
            )
            _write_json(
                workspace / "artifacts" / "analysis" / "strict_pool_backfill_targets.json",
                [{"source_name": "账号A", "backfill_priority": "高"}, {"source_name": "账号B", "backfill_priority": "中"}],
            )

            custom_json = workspace / "tmp" / "run_summary.json"
            custom_md = workspace / "tmp" / "run_summary.md"
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
                        "--log-limit",
                        "3",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(custom_json.exists())
            self.assertTrue(custom_md.exists())

            payload = json.loads(custom_json.read_text(encoding="utf-8"))
            self.assertIn("日志目录缺失：artifacts/run-logs", payload["warnings"])
            self.assertIn("状态文件缺失：artifacts/status/project_progress.json", payload["warnings"])
            self.assertEqual(payload["run_logs"]["included_file_count"], 0)
            self.assertEqual(payload["status_reports"]["positive_factors_report"]["highlights"]["account_count"], 9)
            self.assertEqual(payload["status_reports"]["positive_factors_strict_valid_report"]["highlights"]["account_count"], 8)
            gap = payload["status_reports"]["strict_pool_gap_report"]["highlights"]
            self.assertEqual(gap["high_priority_account_count"], 1)
            targets = payload["status_reports"]["strict_pool_backfill_targets"]["highlights"]
            self.assertEqual(targets["target_count"], 2)


if __name__ == "__main__":
    unittest.main()
