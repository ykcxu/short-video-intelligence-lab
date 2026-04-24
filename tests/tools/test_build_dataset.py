import csv
import importlib.util
import io
import json
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_tool_module():
    tool_path = Path(__file__).resolve().parents[2] / "tools" / "build_dataset.py"
    spec = importlib.util.spec_from_file_location("build_dataset", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class BuildDatasetToolTestCase(unittest.TestCase):
    def test_main_outputs_csv_and_sqlite_and_warning(self) -> None:
        module = _load_tool_module()
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_json(
                workspace / "artifacts" / "collector" / "video" / "video_detail_1111111111.json",
                {
                    "video_url": "https://www.douyin.com/video/1111111111",
                    "metrics": {"view_count": 88, "like_count": 7, "comment_count": 1, "share_count": 2},
                    "raw": {"title": "测试视频A"},
                    "collected_at": "2026-04-24T00:00:00+00:00",
                },
            )
            _write_json(
                workspace / "artifacts" / "collector" / "video" / "video_detail_2222222222.json",
                {
                    "video_url": "https://www.douyin.com/video/2222222222",
                    "metrics": {"view_count": 99, "like_count": 8, "comment_count": 0, "share_count": 3},
                    "raw": {"title": "测试视频B"},
                },
            )
            broken = workspace / "artifacts" / "collector" / "video" / "video_detail_bad.json"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_text("{not-json}", encoding="utf-8")
            _write_json(
                workspace / "artifacts" / "collector" / "comments" / "video_comments_1111111111.json",
                {
                    "video_url": "https://www.douyin.com/video/1111111111",
                    "comments": [
                        {
                            "cid": "c-1",
                            "text": "第一条评论",
                            "create_time": "1713900000",
                            "digg_count": 5,
                            "user": {"uid": "u-1", "nickname": "小明"},
                        }
                    ],
                    "collected_at": "2026-04-24T00:01:00+00:00",
                },
            )
            video_file = workspace / "downloads" / "artifact" / "测试账号" / "测试账号_1111111111.mp4"
            video_file.parent.mkdir(parents=True, exist_ok=True)
            video_file.write_bytes(b"fake")
            output_dir = workspace / "data" / "processed"

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = module.main(
                    ["--workspace", str(workspace), "--output-dir", str(output_dir), "--sqlite", "--limit", "1"]
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["videos_count"], 1)
            self.assertEqual(summary["video_metrics_count"], 1)
            self.assertEqual(summary["comments_count"], 1)
            self.assertEqual(summary["warning_count"], 1)
            self.assertIn("video_detail_bad.json", stderr.getvalue())
            self.assertIn("[WARNING]", stderr.getvalue())

            for name in ("accounts.csv", "videos.csv", "video_metrics.csv", "comments.csv"):
                self.assertTrue((output_dir / name).exists(), name)
            accounts = _read_csv(output_dir / "accounts.csv")
            videos = _read_csv(output_dir / "videos.csv")
            metrics = _read_csv(output_dir / "video_metrics.csv")
            comments = _read_csv(output_dir / "comments.csv")

            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["account_name"], "测试账号")
            self.assertEqual(videos[0]["video_id"], "1111111111")
            self.assertEqual(videos[0]["title"], "测试视频A")
            self.assertEqual(metrics[0]["view_count"], "88")
            self.assertEqual(comments[0]["comment_id"], "c-1")
            self.assertEqual(comments[0]["user_name"], "小明")

            sqlite_path = output_dir / "analysis_dataset.sqlite"
            self.assertTrue(sqlite_path.exists())
            conn = sqlite3.connect(sqlite_path)
            try:
                account_count = conn.execute("SELECT COUNT(1) FROM accounts").fetchone()[0]
                video_count = conn.execute("SELECT COUNT(1) FROM videos").fetchone()[0]
                metric_count = conn.execute("SELECT COUNT(1) FROM video_metrics").fetchone()[0]
                comment_count = conn.execute("SELECT COUNT(1) FROM comments").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(account_count, 1)
            self.assertEqual(video_count, 1)
            self.assertEqual(metric_count, 1)
            self.assertEqual(comment_count, 1)


if __name__ == "__main__":
    unittest.main()
