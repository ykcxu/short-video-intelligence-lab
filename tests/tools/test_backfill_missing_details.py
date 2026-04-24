import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "backfill_missing_details.py"
    spec = importlib.util.spec_from_file_location("backfill_missing_details", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_download(workspace: Path, relative_path: str) -> None:
    target = workspace / "downloads" / "artifact" / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"mp4")


def _write_detail(workspace: Path, name: str, payload: dict[str, object]) -> None:
    target = workspace / "artifacts" / "collector" / "video" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class BackfillMissingDetailsTestCase(unittest.TestCase):
    def test_main_dry_run_outputs_missing_items(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_download(workspace, "账号A/账号A_111.mp4")
            _write_download(workspace, "账号A/账号A_222.mp4")
            _write_detail(
                workspace,
                "video_detail_existing.json",
                {"video_url": "https://www.douyin.com/video/111"},
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace), "--dry-run"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["downloaded_count"], 2)
            self.assertEqual(payload["existing_detail_count"], 1)
            self.assertEqual(payload["missing_count"], 1)
            self.assertEqual(len(payload["planned_items"]), 1)
            self.assertEqual(payload["planned_items"][0]["video_id"], "222")
            self.assertEqual(payload["planned_items"][0]["video_url"], "https://www.douyin.com/video/222")
            self.assertTrue(payload["planned_items"][0]["file_path"].endswith("downloads\\artifact\\账号A\\账号A_222.mp4"))

    def test_main_backfills_only_limited_missing_items(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_download(workspace, "账号A/账号A_111.mp4")
            _write_download(workspace, "账号A/账号A_222.mp4")
            _write_download(workspace, "账号A/账号A_333.mp4")
            _write_detail(
                workspace,
                "video_detail_existing.json",
                {"video_id": "111", "video_url": "https://www.douyin.com/video/111"},
            )
            calls: list[str] = []

            class FakeOrchestrator:
                def __init__(self, config) -> None:
                    self.config = config

                def bootstrap(self) -> None:
                    return None

                def crawl_video_detail(self, video_url: str) -> dict[str, object]:
                    calls.append(video_url)
                    return {"artifact_path": f"artifact:{video_url}", "warnings": []}

            stdout = io.StringIO()
            with patch.object(module, "load_config", lambda path, workspace: {"path": str(path), "workspace": str(workspace)}):
                with patch.object(module, "Orchestrator", FakeOrchestrator):
                    with redirect_stdout(stdout):
                        exit_code = module.main(["--workspace", str(workspace), "--limit", "1"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(calls, ["https://www.douyin.com/video/222"])
            self.assertEqual(payload["missing_count"], 2)
            self.assertEqual(payload["planned_count"], 1)
            self.assertEqual(payload["processed_count"], 1)
            self.assertEqual(
                payload["results"][0]["artifact_path"],
                "artifact:https://www.douyin.com/video/222",
            )


if __name__ == "__main__":
    unittest.main()
