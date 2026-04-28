import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_comment_failure_diagnostics.py"
    spec = importlib.util.spec_from_file_location("build_comment_failure_diagnostics", tool_path)
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


def _write_targets(workspace: Path) -> None:
    _write_json(
        workspace / "artifacts" / "collector" / "comment_backfill_targets.json",
        {
            "targets": [
                {"video_id": "real", "video_url": "https://www.douyin.com/video/real", "priority": 1},
                {"video_id": "empty", "comment_count": 10, "has_comment_artifact": True},
                {"video_id": "noise", "comment_count": 5, "has_non_empty_comments": True},
                {"video_id": "missing", "comment_count": 3, "has_comment_artifact": False},
                {"video_id": "none", "comment_count": 0, "has_comment_artifact": False},
            ]
        },
    )


class BuildCommentFailureDiagnosticsTestCase(unittest.TestCase):
    def test_main_classifies_target_videos_and_writes_outputs(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(workspace)
            _write_comments(
                workspace,
                "video_comments_real.json",
                {
                    "video_id": "real",
                    "comments": [{"author_id": "u1", "text": "真实评论"}],
                    "scan_meta": {"backend": "playwright:ok"},
                },
            )
            _write_comments(
                workspace,
                "video_comments_empty.json",
                {
                    "video_id": "empty",
                    "comments": [],
                    "scan_meta": {
                        "stop_reason": "placeholder_only",
                        "backend": "playwright:placeholder",
                        "warnings": ["networkidle timeout", "networkidle timeout"],
                    },
                },
            )
            _write_comments(
                workspace,
                "video_comments_noise.json",
                {
                    "video_id": "noise",
                    "comments": [
                        {"text": "占位", "raw": {"stub": True, "error": "blocked", "response_url": "https://x/a?b=1"}},
                        {"text": "占位2", "raw": {"stub": True, "response_url": "https://x/a?b=2"}},
                    ],
                },
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            payload = json.loads((workspace / "artifacts" / "status" / "comment_failure_diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status_counts"], {"real_comment": 1, "empty_response": 1, "noise_only": 1, "missing_artifact": 2})
            self.assertEqual(
                payload["expected_status_counts"],
                {
                    "real_comment": 1,
                    "no_comment_expected": 1,
                    "comment_expected_empty_response": 1,
                    "comment_expected_noise_only": 1,
                    "comment_expected_missing_artifact": 1,
                },
            )
            self.assertEqual(payload["summary"]["hit_rate"], 0.2)
            self.assertEqual(payload["summary"]["failure_video_count"], 4)
            self.assertEqual(payload["summary"]["no_comment_expected_video_count"], 1)
            self.assertEqual(payload["summary"]["comment_expected_but_uncollected_video_count"], 3)
            self.assertEqual(payload["failure_reasons"]["empty_response"]["scan_meta.warning:networkidle timeout"], 2)
            self.assertEqual(payload["failure_reasons"]["noise_only"]["raw.stub:true"], 2)
            self.assertEqual(payload["failure_reasons"]["noise_only"]["raw.response_url:x/a"], 2)
            self.assertIn("json_output", json.loads(stdout.getvalue()))

            markdown = (workspace / "artifacts" / "status" / "comment_failure_diagnostics.md").read_text(encoding="utf-8")
            self.assertIn("# 评论补采命中率诊断", markdown)
            self.assertIn("| missing_artifact | 2 |", markdown)
            self.assertIn("| no_comment_expected | 1 |", markdown)
            self.assertIn("| noise | noise_only | comment_expected_noise_only | 5 | 1 | 2 | 0 | `raw.response_url:x/a` |", markdown)

    def test_uses_artifacts_when_targets_are_absent(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_comments(
                workspace,
                "video_comments_hash.json",
                {
                    "video_url": "https://www.douyin.com/video/from-url",
                    "data": {"comments": [{"raw": {"response_url": "https://x/aweme/v1/web/comment/list/?cursor=0"}}]},
                },
            )

            report = module.build_comment_failure_diagnostics(workspace)

            self.assertEqual(report["summary"]["video_count"], 1)
            self.assertEqual(report["status_counts"]["real_comment"], 1)
            self.assertEqual(report["videos"][0]["video_id"], "from-url")
            self.assertEqual(report["videos"][0]["real_comment_count"], 1)

    def test_custom_output_paths_are_workspace_relative(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_targets(workspace)

            exit_code = module.main(
                [
                    "--workspace",
                    str(workspace),
                    "--output",
                    "tmp/report.json",
                    "--md-output",
                    "tmp/report.md",
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((workspace / "tmp" / "report.json").exists())
            self.assertTrue((workspace / "tmp" / "report.md").exists())


if __name__ == "__main__":
    unittest.main()

