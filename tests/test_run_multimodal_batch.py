import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import run_multimodal_batch as mod


class RunMultimodalBatchTest(unittest.TestCase):
    def test_dry_run_selects_per_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            inputs = workspace / "inputs.json"
            items = [
                {"video_id": "a1", "source_name": "A"},
                {"video_id": "a2", "source_name": "A"},
                {"video_id": "b1", "source_name": "B"},
            ]
            inputs.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")

            code = mod.main(["--workspace", str(workspace), "--inputs", str(inputs), "--max-per-account", "1", "--dry-run", "--run-id", "t"])

            self.assertEqual(code, 0)
            batch = json.loads((workspace / "artifacts" / "analysis" / "local_video_inputs_batch_t.json").read_text(encoding="utf-8"))
            self.assertEqual([item["video_id"] for item in batch["items"]], ["a1", "b1"])

    def test_run_calls_feature_steps_and_writes_fusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            inputs = workspace / "inputs.json"
            inputs.write_text(json.dumps({"items": [{"video_id": "v1", "source_name": "A"}]}, ensure_ascii=False), encoding="utf-8")
            fake_result = {"ok": True, "output_path": str(workspace / "fake.json"), "result": {"summary": {"average_fit_score": 1}}}

            with patch.object(mod, "analyze_asr_features_file", return_value=fake_result):
                with patch.object(mod, "analyze_ocr_features_file", return_value=fake_result):
                    with patch.object(mod, "analyze_person_visual_features_file", return_value=fake_result):
                        with patch.object(mod, "analyze_script_structure_file", return_value=fake_result):
                            with patch.object(mod, "prepare_multimodal_inputs", return_value=fake_result):
                                with patch.object(mod, "analyze_multimodal_inputs_file", return_value=fake_result):
                                    code = mod.main(["--workspace", str(workspace), "--inputs", str(inputs), "--run-id", "t"])

            self.assertEqual(code, 0)
            self.assertTrue((workspace / "artifacts" / "analysis" / "local_video_fit_batch_t.json").exists())


if __name__ == "__main__":
    unittest.main()
