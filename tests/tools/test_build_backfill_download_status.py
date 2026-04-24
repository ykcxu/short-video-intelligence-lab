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
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_backfill_download_status.py"
    spec = importlib.util.spec_from_file_location("build_backfill_download_status", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_videos(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["video_id", "account_id"])
        writer.writeheader()
        writer.writerow({"video_id": "1", "account_id": "账号A"})


class BuildBackfillDownloadStatusTestCase(unittest.TestCase):
    def test_main_builds_download_status(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_json(workspace / "artifacts" / "analysis" / "strict_pool_backfill_targets.json", [{"source_name": "账号A????", "backfill_priority": "高", "valid_video_count": 0}])
            _write_videos(workspace / "data" / "processed" / "videos.csv")
            account_dir = workspace / "downloads" / "artifact" / "账号A"
            account_dir.mkdir(parents=True)
            (account_dir / "a.mp4").write_bytes(b"a")
            (account_dir / "b.mp4").write_bytes(b"b")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = module.main(["--workspace", str(workspace)])

            self.assertEqual(exit_code, 0)
            output = json.loads(stdout.getvalue())
            payload = json.loads(Path(output["json_output"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["needs_dataset_refresh_count"], 1)
            self.assertEqual(payload["accounts"][0]["local_mp4_count"], 2)
            self.assertTrue(payload["accounts"][0]["needs_dataset_refresh"])


if __name__ == "__main__":
    unittest.main()
