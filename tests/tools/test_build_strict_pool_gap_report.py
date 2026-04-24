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
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_strict_pool_gap_report.py"
    spec = importlib.util.spec_from_file_location("build_strict_pool_gap_report", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["video_id", "account_id"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class BuildStrictPoolGapReportTestCase(unittest.TestCase):
    def test_main_builds_gap_report(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_csv(
                workspace / "data" / "processed" / "videos.csv",
                [
                    {"video_id": "1", "account_id": "账号A"},
                    {"video_id": "2", "account_id": "账号A"},
                    {"video_id": "3", "account_id": "账号B"},
                ],
            )
            _write_csv(workspace / "data" / "processed_strict_valid" / "videos.csv", [{"video_id": "1", "account_id": "账号A"}])
            self._write_pipeline_log(workspace)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            output = json.loads(stdout.getvalue())
            payload = json.loads(Path(output["json_output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["input_video_count"], 3)
            self.assertEqual(payload["valid_video_count"], 1)
            self.assertEqual(payload["filtered_reason_counts"]["not_homepage_observed"], 2)
            self.assertEqual(payload["accounts"][0]["account_id"], "账号B")
            self.assertEqual(payload["accounts"][0]["priority"], "高")
            markdown = Path(output["md_output"]).read_text(encoding="utf-8")
            self.assertIn("严格有效池覆盖缺口报告", markdown)
            self.assertIn("账号B", markdown)

    def _write_pipeline_log(self, workspace: Path) -> None:
        log_path = workspace / "artifacts" / "status" / "phase1_pipeline_last_run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "prefix\n"
            + json.dumps(
                {
                    "filtered_reason_counts": {"not_homepage_observed": 2},
                    "valid_account_video_counts": {"账号A": 1},
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps({"return_code": 0, "steps": []}, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
