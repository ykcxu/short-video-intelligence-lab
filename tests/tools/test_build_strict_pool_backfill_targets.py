from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_strict_pool_backfill_targets.py"
    spec = importlib.util.spec_from_file_location("build_strict_pool_backfill_targets", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class BuildStrictPoolBackfillTargetsTestCase(unittest.TestCase):
    def test_main_builds_targets_from_gap_report(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_json(
                workspace / "artifacts" / "status" / "strict_pool_gap_report.json",
                {
                    "accounts": [
                        {"account_id": "账号A", "priority": "高", "input_video_count": 10, "valid_video_count": 0, "filtered_video_count": 10, "retention_rate": 0},
                        {"account_id": "账号B????", "priority": "中", "input_video_count": 60, "valid_video_count": 21, "filtered_video_count": 39, "retention_rate": 0.35},
                        {"account_id": "账号C", "priority": "低", "input_video_count": 80, "valid_video_count": 70, "filtered_video_count": 10, "retention_rate": 0.875},
                    ]
                },
            )
            _write_json(
                workspace / "artifacts" / "analysis" / "homepage_batch_summary_20260422.json",
                {
                    "rows": [
                        {"source_name": "账号A", "homepage_url": "https://a", "category_lv1": "内部", "category_lv2": "小语", "platform": "抖音"},
                        {"source_name": "账号B", "homepage_url": "https://b", "category_lv1": "内部", "category_lv2": "小英", "platform": "抖音"},
                        {"source_name": "账号C", "homepage_url": "https://c", "category_lv1": "内部", "category_lv2": "小英", "platform": "抖音"},
                    ]
                },
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace), "--min-priority", "中"])

            self.assertEqual(exit_code, 0)
            output = json.loads(stdout.getvalue())
            targets = json.loads(Path(output["output"]).read_text(encoding="utf-8"))
            self.assertEqual([item["source_name"] for item in targets], ["账号A", "账号B"])
            self.assertEqual(targets[1]["homepage_url"], "https://b")
            self.assertEqual(targets[0]["backfill_priority"], "高")


if __name__ == "__main__":
    unittest.main()
