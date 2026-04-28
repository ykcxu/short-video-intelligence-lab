import json
import tempfile
import unittest
from pathlib import Path

from tools import build_artifact_index


class ArtifactIndexTest(unittest.TestCase):
    def test_main_writes_json_and_markdown_with_directory_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            comments = artifacts / "collector" / "comments"
            analysis = artifacts / "analysis"
            comments.mkdir(parents=True)
            analysis.mkdir(parents=True)
            (comments / "a.json").write_text("{}", encoding="utf-8")
            (analysis / "report.md").write_text("报告", encoding="utf-8")

            code = build_artifact_index.main(["--artifacts-dir", str(artifacts), "--limit", "5"])

            self.assertEqual(code, 0)
            index = json.loads((artifacts / "artifact_index.json").read_text(encoding="utf-8"))
            markdown = (artifacts / "artifact_index.md").read_text(encoding="utf-8")
            self.assertEqual(index["totals"]["file_count"], 2)
            self.assertEqual(index["directories"][0]["path"], "collector/comments")
            self.assertIn("## 按目录聚合", markdown)
            self.assertIn("collector/comments/a.json", markdown)

    def test_non_empty_error_log_warning_and_recent_run_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            run_logs = artifacts / "run-logs"
            run_logs.mkdir(parents=True)
            (run_logs / "stderr-error.log").write_text("boom", encoding="utf-8")
            (run_logs / "ok.log").write_text("", encoding="utf-8")

            index = build_artifact_index.build_index(artifacts, limit=10)

            self.assertEqual(len(index["recent_run_logs"]), 2)
            self.assertEqual(len(index["error_log_warnings"]), 1)
            self.assertEqual(index["error_log_warnings"][0]["path"], "run-logs/stderr-error.log")


if __name__ == "__main__":
    unittest.main()
