from __future__ import annotations

import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "run_phase1_analysis_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_phase1_analysis_pipeline", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_fake_tool(path: Path, name: str, return_code: int = 0) -> None:
    """写入可记录调用顺序的假工具脚本。"""
    script = f"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    workspace = None
    for index, item in enumerate(args):
        if item == "--workspace" and index + 1 < len(args):
            workspace = Path(args[index + 1])
            break
    if workspace is None:
        raise RuntimeError("缺少 --workspace 参数")

    record_path = workspace / "call_order.jsonl"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {{"tool": "{name}", "args": args}}
    with record_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\\n")
    return {return_code}


if __name__ == "__main__":
    raise SystemExit(main())
""".strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")


def _read_call_order(workspace: Path) -> list[dict[str, object]]:
    """读取假工具记录的调用顺序。"""
    log_path = workspace / "call_order.jsonl"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class RunPhase1AnalysisPipelineToolTestCase(unittest.TestCase):
    def test_main_runs_all_steps_in_order(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            self._prepare_fake_workspace(workspace)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace), "--top-n", "12", "--log-limit", "30"])

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["return_code"], 0)
            self.assertEqual([step["name"] for step in summary["steps"]], self._expected_names())

            order = _read_call_order(workspace)
            self.assertEqual([row["tool"] for row in order], self._expected_names())

            fourth_args = order[3]["args"]
            comment_args = order[7]["args"]
            summary_args = order[8]["args"]
            self.assertIn("--top-n", fourth_args)
            self.assertEqual(fourth_args[fourth_args.index("--top-n") + 1], "12")
            self.assertIn("artifacts/status/comment_backfill_status.json", comment_args)
            self.assertIn("artifacts/status/comment_backfill_status.md", comment_args)
            self.assertIn("--log-limit", summary_args)
            self.assertEqual(summary_args[summary_args.index("--log-limit") + 1], "30")

    def test_main_stops_when_step_fails(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            self._prepare_fake_workspace(workspace, failing_tool="build_data_quality_report", failing_code=7)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 7)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["return_code"], 7)
            self.assertEqual(len(summary["steps"]), 2)
            self.assertEqual(summary["steps"][1]["name"], "build_data_quality_report")
            self.assertEqual(summary["steps"][1]["return_code"], 7)

            order = _read_call_order(workspace)
            self.assertEqual([row["tool"] for row in order], ["build_dataset", "build_data_quality_report"])

    def test_main_supports_skip_sqlite(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            self._prepare_fake_workspace(workspace)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace), "--skip-sqlite"])

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["return_code"], 0)
            first_args = _read_call_order(workspace)[0]["args"]
            self.assertNotIn("--sqlite", first_args)

    def _prepare_fake_workspace(self, workspace: Path, failing_tool: str | None = None, failing_code: int = 1) -> None:
        """按流水线所需名称创建假工具脚本。"""
        for name in self._expected_names():
            code = failing_code if name == failing_tool else 0
            _write_fake_tool(workspace / "tools" / f"{name}.py", name=name, return_code=code)

    def _expected_names(self) -> list[str]:
        """返回流水线固定步骤名。"""
        return [
            "build_dataset",
            "build_data_quality_report",
            "build_valid_analysis_pool",
            "build_positive_factors_report",
            "build_strict_pool_gap_report",
            "build_strict_pool_backfill_targets",
            "build_backfill_download_status",
            "build_comment_backfill_status",
            "build_run_summary",
        ]


if __name__ == "__main__":
    unittest.main()
