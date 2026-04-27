import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.short_video_intel.analysis.ocr_features import analyze_ocr_features_file
from src.short_video_intel.analysis.ocr_features import analyze_ocr_features_item


class FakeReader:
    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping

    def read_text(self, image_path: Path) -> list[str]:
        return self.mapping.get(image_path.name, [])


class OcrFeaturesTest(unittest.TestCase):
    def test_item_reads_existing_jpg_frame_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            frame_one = workspace / "frame_01.jpg"
            frame_two = workspace / "frame_02.jpg"
            frame_one.write_bytes(b"fake jpg")
            frame_two.write_bytes(b"fake jpg")
            item = {
                "video_id": "v1",
                "frame_samples": [
                    {"ok": True, "output_path": str(frame_one)},
                    {"ok": True, "output_path": str(frame_two)},
                    {"ok": False, "output_path": str(workspace / "skip.jpg")},
                ],
            }

            result = analyze_ocr_features_item(workspace=workspace, item=item, reader=FakeReader({"frame_01.jpg": ["知识方法"], "frame_02.jpg": []}))

            feature = result["ocr_subtitle"]
            self.assertEqual(result["video_id"], "v1")
            self.assertEqual(feature["text"], "知识方法")
            self.assertEqual(feature["frames_count"], 2)
            self.assertEqual(feature["coverage_ratio"], 0.5)
            self.assertGreater(feature["keyword_density"], 0)

    def test_file_merges_ocr_subtitle_into_feature_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            frame = workspace / "frames" / "frame_01.jpg"
            frame.parent.mkdir()
            frame.write_bytes(b"fake jpg")
            artifact = workspace / "local_video_inputs.json"
            output = workspace / "out" / "ocr.json"
            features_dir = workspace / "features"
            artifact.write_text(
                json.dumps({"items": [{"video_id": "v2", "frame_samples": [{"ok": True, "output_path": str(frame)}]}]}),
                encoding="utf-8",
            )
            reader_result = {"ok": True, "backend": "easyocr", "reader": FakeReader({"frame_01.jpg": ["视频复盘重点"]})}

            with patch("src.short_video_intel.analysis.ocr_features._create_reader", return_value=reader_result):
                result = analyze_ocr_features_file(workspace=workspace, artifact=artifact, output=output, features_dir=features_dir)

            saved = json.loads((features_dir / "v2.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(output.exists())
            self.assertEqual(saved["ocr_subtitle"]["text"], "视频复盘重点")
            self.assertEqual(result["result"]["results"][0]["ocr_subtitle"]["frames_count"], 1)

    def test_missing_dependency_returns_clear_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "input.json"
            artifact.write_text(json.dumps({"items": []}), encoding="utf-8")
            missing = {"ok": False, "error_code": "missing_dependency", "backend": "auto", "errors": ["easyocr", "paddleocr"]}

            with patch("src.short_video_intel.analysis.ocr_features._create_reader", return_value=missing):
                result = analyze_ocr_features_file(workspace=workspace, artifact=artifact, backend="auto")

            self.assertFalse(result["ok"])
            self.assertEqual(result["error_code"], "missing_dependency")
            self.assertIn("missing_dependency", Path(result["output_path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
