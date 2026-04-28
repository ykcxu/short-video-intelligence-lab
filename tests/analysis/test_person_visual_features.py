import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.short_video_intel.analysis import person_visual_features as mod
from src.short_video_intel.analysis.person_visual_features import analyze_person_visual_features_file


class PersonVisualFeaturesTest(unittest.TestCase):
    def test_file_writes_three_feature_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            frame = workspace / "frame.jpg"
            frame.write_bytes(b"fake")
            artifact = workspace / "inputs.json"
            artifact.write_text(json.dumps({"items": [{"video_id": "v1", "frame_samples": [{"ok": True, "output_path": str(frame)}]}]}), encoding="utf-8")
            fake_frame = {"ok": True, "faces": [{"x": 0.25, "y": 0.2, "w": 0.3, "h": 0.3, "area": 0.09}], "people": [{"x": 0.2, "y": 0.1, "w": 0.5, "h": 0.7, "area": 0.35}], "pose": {"detected": True, "x": 0.2, "y": 0.1, "w": 0.5, "h": 0.7, "landmark_count": 20}}

            with patch.object(mod, "_load_dependencies", return_value={"ok": True}):
                with patch.object(mod, "_analyze_frame", return_value=fake_frame):
                    result = analyze_person_visual_features_file(workspace=workspace, artifact=artifact, features_dir=workspace / "features")

            saved = json.loads((workspace / "features" / "v1.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(saved["face_quality"]["face_detected"])
            self.assertTrue(saved["pose_quality"]["pose_detected"])
            self.assertTrue(saved["person_subject"]["person_detected"])

    def test_missing_dependency_returns_clear_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "inputs.json"
            artifact.write_text(json.dumps({"items": []}), encoding="utf-8")

            with patch.object(mod, "_load_dependencies", return_value={"ok": False, "errors": ["mediapipe"]}):
                result = analyze_person_visual_features_file(workspace=workspace, artifact=artifact)

            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "missing_dependency")


if __name__ == "__main__":
    unittest.main()
