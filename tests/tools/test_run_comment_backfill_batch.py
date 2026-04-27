import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "run_comment_backfill_batch.py"
    spec = importlib.util.spec_from_file_location("run_comment_backfill_batch", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_targets(workspace: Path, targets: list[dict[str, object]]) -> Path:
    target = workspace / "artifacts" / "collector" / "comment_backfill_targets.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"targets": targets}, ensure_ascii=False), encoding="utf-8")
    return target


def _make_target(video_id: str, **metadata: object) -> dict[str, object]:
    """生成测试目标，集中维护 video_url 规则。"""
    return {
        "video_id": video_id,
        "video_url": f"https://www.douyin.com/video/{video_id}",
        **metadata,
    }


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunCommentBackfillBatchTestCase(unittest.TestCase):
    def test_dry_run_outputs_planned_targets_without_subprocess(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(
                workspace,
                [
                    {"video_id": "1111111111", "video_url": "https://www.douyin.com/video/1111111111"},
                    {"video_id": "2222222222", "video_url": "https://www.douyin.com/video/2222222222"},
                ],
            )

            stdout = io.StringIO()
            with patch.object(module.subprocess, "run") as fake_run:
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        ["--workspace", str(workspace), "--session-name", "douyin", "--limit", "1", "--dry-run"]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["planned_count"], 1)
            self.assertEqual(payload["targets"][0]["video_id"], "1111111111")
            fake_run.assert_not_called()
            self.assertFalse((workspace / "artifacts" / "run-logs").exists())

    def test_dry_run_filters_missing_artifact_before_limit(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(
                workspace,
                [
                    _make_target("1111111111", has_comment_artifact=True, comment_count=0, priority=10),
                    _make_target(
                        "2222222222",
                        has_comment_artifact=False,
                        has_non_empty_comments=False,
                        comment_count=0,
                        priority=20,
                    ),
                    _make_target("3333333333", has_comment_artifact=False, comment_count=3, priority=30),
                ],
            )

            stdout = io.StringIO()
            with patch.object(module.subprocess, "run") as fake_run:
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        [
                            "--workspace",
                            str(workspace),
                            "--session-name",
                            "douyin",
                            "--only-missing-artifact",
                            "--limit",
                            "1",
                            "--dry-run",
                        ]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["planned_count"], 1)
            self.assertEqual(payload["targets"][0]["video_id"], "2222222222")
            self.assertFalse(payload["targets"][0]["has_comment_artifact"])
            self.assertFalse(payload["targets"][0]["has_non_empty_comments"])
            self.assertEqual(payload["targets"][0]["comment_count"], 0)
            self.assertEqual(payload["targets"][0]["priority"], 20)
            fake_run.assert_not_called()

    def test_main_runs_each_target_and_writes_summary_log(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            log_output = workspace / "artifacts" / "run-logs" / "batch.log"
            _write_targets(
                workspace,
                [
                    {"video_id": "1111111111", "video_url": "https://www.douyin.com/video/1111111111"},
                    {"video_id": "2222222222", "video_url": "https://www.douyin.com/video/2222222222"},
                ],
            )
            fake_results = [
                FakeCompletedProcess(0, "ok\nline", ""),
                FakeCompletedProcess(0, "done", "warn"),
            ]

            stdout = io.StringIO()
            with patch.object(module.subprocess, "run", side_effect=fake_results) as fake_run:
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        [
                            "--workspace",
                            str(workspace),
                            "--config",
                            "config.local.yaml",
                            "--session-name",
                            "douyin",
                            "--max-pages",
                            "5",
                            "--log-output",
                            str(log_output),
                        ]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["planned_count"], 2)
            self.assertEqual(payload["results"][0]["stdout_tail"], "ok\nline")
            self.assertEqual(payload["results"][1]["stderr_tail"], "warn")
            self.assertEqual(fake_run.call_count, 2)
            first_command = fake_run.call_args_list[0].args[0]
            self.assertEqual(first_command[:4], ["py", "-3.11", "-m", "short_video_intel.cli"])
            self.assertIn("crawl-video-comments", first_command)
            self.assertIn("--max-pages", first_command)
            self.assertEqual(first_command[-1], "5")
            self.assertEqual(fake_run.call_args_list[0].kwargs["cwd"], workspace.resolve())
            self.assertTrue(fake_run.call_args_list[0].kwargs["capture_output"])
            self.assertTrue(fake_run.call_args_list[0].kwargs["text"])
            self.assertEqual(fake_run.call_args_list[0].kwargs["encoding"], "utf-8")
            self.assertEqual(fake_run.call_args_list[0].kwargs["errors"], "replace")
            self.assertIn("video_id=1111111111", log_output.read_text(encoding="utf-8"))

    def test_skip_existing_artifact_does_not_run_skipped_targets(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(
                workspace,
                [
                    _make_target("1111111111", has_comment_artifact=True),
                    _make_target("2222222222", has_comment_artifact=False, comment_count=5),
                ],
            )

            stdout = io.StringIO()
            with patch.object(module.subprocess, "run", return_value=FakeCompletedProcess(0, "ok", "")) as fake_run:
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        [
                            "--workspace",
                            str(workspace),
                            "--session-name",
                            "douyin",
                            "--skip-existing-artifact",
                        ]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["planned_count"], 1)
            self.assertEqual(payload["results"][0]["video_id"], "2222222222")
            self.assertFalse(payload["results"][0]["has_comment_artifact"])
            self.assertEqual(payload["results"][0]["comment_count"], 5)
            self.assertEqual(fake_run.call_count, 1)
            command = fake_run.call_args.args[0]
            self.assertIn("https://www.douyin.com/video/2222222222", command)
            self.assertNotIn("https://www.douyin.com/video/1111111111", command)

    def test_main_retries_failed_target_and_reports_failure(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(workspace, [{"video_url": "https://www.douyin.com/video/3333333333"}])
            fake_results = [
                FakeCompletedProcess(2, "first", "err1"),
                FakeCompletedProcess(3, "second", "err2"),
            ]

            stdout = io.StringIO()
            with patch.object(module.subprocess, "run", side_effect=fake_results):
                with redirect_stdout(stdout):
                    exit_code = module.main(
                        ["--workspace", str(workspace), "--session-name", "douyin", "--retry-limit", "1"]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["failed_count"], 1)
            self.assertEqual(payload["results"][0]["attempts"], 2)
            self.assertEqual(payload["results"][0]["return_code"], 3)
            self.assertEqual(payload["results"][0]["video_id"], "3333333333")

    def test_main_handles_none_subprocess_streams(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(workspace, [{"video_url": "https://www.douyin.com/video/4444444444"}])

            stdout = io.StringIO()
            with patch.object(module.subprocess, "run", return_value=FakeCompletedProcess(0, None, None)):
                with redirect_stdout(stdout):
                    exit_code = module.main(["--workspace", str(workspace), "--session-name", "douyin"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["results"][0]["stdout_tail"], "")
            self.assertEqual(payload["results"][0]["stderr_tail"], "")


if __name__ == "__main__":
    unittest.main()
