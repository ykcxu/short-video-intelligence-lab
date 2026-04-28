import json
import tempfile
import unittest
from pathlib import Path

from src.short_video_intel.analysis.script_structure import (
    analyze_script_structure_file,
    analyze_script_structure_item,
)


class ScriptStructureTest(unittest.TestCase):
    def test_detects_complete_talking_script(self) -> None:
        item = {
            "video_id": "v1",
            "asr_speech": {
                "transcript": "你知道孩子阅读为什么总丢分吗？很多家长都忽略了方法。先看题型，再找关键词，最后用公式作答。比如这道题，我们看原文。记得收藏关注。"
            },
        }

        result = analyze_script_structure_item(item)
        structure = result["script_structure"]

        self.assertTrue(structure["has_hook"])
        self.assertTrue(structure["has_pain_point"])
        self.assertTrue(structure["has_method"])
        self.assertTrue(structure["has_example"])
        self.assertTrue(structure["has_cta"])
        self.assertGreaterEqual(structure["structure_completeness_score"], 1.0)

    def test_file_analysis_writes_feature_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "items.json"
            features_dir = workspace / "features"
            artifact.write_text(
                json.dumps({"items": [{"video_id": "v2", "transcript": "一个方法解决作文没思路，比如先列提纲，最后关注我。"}]}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = analyze_script_structure_file(workspace=workspace, artifact=artifact, features_dir=features_dir)

            self.assertTrue(result["ok"])
            self.assertTrue((features_dir / "v2.json").exists())
            feature = json.loads((features_dir / "v2.json").read_text(encoding="utf-8"))
            self.assertIn("script_structure", feature)

    def test_reads_real_detail_raw_title_and_video_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "detail.json"
            artifact.write_text(
                json.dumps(
                    [
                        {
                            "video_url": "https://www.douyin.com/video/1234567890",
                            "raw": {"title": "一个方法解决阅读丢分，比如先看题型，最后收藏关注。"},
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = analyze_script_structure_file(workspace=workspace, artifact=artifact)

            item = result["result"]["results"][0]
            self.assertEqual(item["video_id"], "1234567890")
            self.assertGreater(item["text_length"], 0)
            self.assertTrue(item["script_structure"]["has_method"])

    def test_reads_multimodal_inputs_result_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "multimodal_inputs.json"
            artifact.write_text(
                json.dumps({"result": {"items": [{"video_id": "v3", "asr_speech": {"transcript": "一个方法解决阅读丢分，比如先看题型，最后收藏关注。"}}]}}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = analyze_script_structure_file(workspace=workspace, artifact=artifact)

            self.assertEqual(result["result"]["total"], 1)
            self.assertEqual(result["result"]["results"][0]["video_id"], "v3")


if __name__ == "__main__":
    unittest.main()
