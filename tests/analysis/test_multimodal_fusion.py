import json
import tempfile
import unittest
from pathlib import Path

from src.short_video_intel.analysis.multimodal_fusion import (
    analyze_multimodal_inputs_file,
    analyze_multimodal_item,
)


class MultimodalFusionTest(unittest.TestCase):
    def test_complete_item_gets_high_score(self) -> None:
        item = {
            "video_id": "v1",
            "local_video_fit": {"fit_score": 82, "strengths": ["画面基础好。"], "risks": [], "actions": []},
            "face_quality": {
                "face_detected": True,
                "center_score": 0.9,
                "sharpness_score": 0.88,
                "expression_positive_score": 0.75,
                "occlusion_risk": 0.0,
            },
            "pose_quality": {
                "upper_body_visible": True,
                "facing_camera_score": 0.86,
                "stability_score": 0.82,
                "gesture_activity_score": 0.55,
            },
            "person_subject": {
                "person_count": 1,
                "subject_ratio": 0.45,
                "center_score": 0.88,
                "background_clutter_score": 0.1,
            },
            "ocr_subtitle": {
                "readability_score": 0.82,
                "keyword_density": 0.7,
                "subtitle_consistency_score": 0.8,
                "coverage_ratio": 0.75,
            },
            "asr_speech": {"speech_rate_cpm": 270, "pause_ratio": 0.08, "opening_hook_score": 0.82},
            "script_structure": {
                "has_hook": True,
                "has_pain_point": True,
                "has_method": True,
                "has_example": True,
                "has_cta": True,
                "knowledge_density_score": 0.8,
            },
        }

        result = analyze_multimodal_item(item)

        self.assertEqual(result["fit_level"], "high")
        self.assertGreaterEqual(result["fit_score"], 75)
        self.assertIn("face_quality", result["diagnostics"])

    def test_missing_modalities_report_risks(self) -> None:
        result = analyze_multimodal_item({"video_id": "v2"})

        self.assertEqual(result["fit_level"], "low")
        self.assertLess(result["fit_score"], 55)
        self.assertTrue(any("缺少ASR" in risk for risk in result["risks"]))
        self.assertTrue(any("补充人脸检测" in action for action in result["actions"]))

    def test_analyze_file_writes_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "input.json"
            output = workspace / "out" / "fusion.json"
            artifact.write_text(json.dumps({"items": [{"video_id": "v3"}]}, ensure_ascii=False), encoding="utf-8")

            result = analyze_multimodal_inputs_file(workspace=workspace, artifact=artifact, output=output)

            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["total"], 1)
            self.assertTrue(output.exists())

    def test_analyze_file_accepts_multimodal_inputs_result_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "input.json"
            artifact.write_text(
                json.dumps({"result": {"items": [{"video_id": "v4", "local_video_fit": {"fit_score": 80}}]}}),
                encoding="utf-8",
            )

            result = analyze_multimodal_inputs_file(workspace=workspace, artifact=artifact)

            self.assertEqual(result["result"]["total"], 1)
            self.assertEqual(result["result"]["results"][0]["video_id"], "v4")


if __name__ == "__main__":
    unittest.main()
