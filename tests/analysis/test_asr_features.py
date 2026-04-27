import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.short_video_intel.analysis import asr_features
from src.short_video_intel.analysis.asr_features import analyze_asr_features_file
from src.short_video_intel.analysis.asr_features import analyze_asr_features_items


class FakeWhisperModel:
    def __init__(self, model_size: str) -> None:
        self.model_size = model_size

    def transcribe(self, wav_path: str, language: str) -> tuple[list[SimpleNamespace], SimpleNamespace]:
        # 伪模型只验证调用边界，不伪造依赖安装行为。
        segments = [
            SimpleNamespace(start=0.0, end=2.0, text="你知道孩子阅读为什么丢分吗？"),
            SimpleNamespace(start=3.0, end=5.0, text="一个方法先看题型。"),
        ]
        return segments, SimpleNamespace(duration=6.0, language=language)


class AsrFeaturesTest(unittest.TestCase):
    def test_missing_dependency_returns_clear_failure(self) -> None:
        items = [{"video_id": "v1", "download_output_path": "v1.mp4"}]

        with patch.object(asr_features, "_load_whisper_model_factory", return_value=None):
            results = analyze_asr_features_items(items=items)

        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["error"], "missing_dependency")
        self.assertEqual(results[0]["asr_speech"]["transcript"], "")

    def test_file_analysis_reads_local_video_inputs_and_writes_feature_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "inputs.json"
            video = workspace / "video.mp4"
            features_dir = workspace / "features"
            video.write_bytes(b"fake")
            artifact.write_text(
                json.dumps({"local_video_inputs": [{"video_id": "v2", "download_output_path": str(video)}]}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.object(asr_features, "_load_whisper_model_factory", return_value=FakeWhisperModel):
                with patch.object(asr_features, "_extract_wav", return_value=None) as extract_wav:
                    result = analyze_asr_features_file(workspace=workspace, artifact=artifact, features_dir=features_dir)

            feature = json.loads((features_dir / "v2.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["summary"]["ok_count"], 1)
            self.assertIn("你知道孩子阅读", feature["asr_speech"]["transcript"])
            self.assertEqual(feature["asr_speech"]["segments_count"], 2)
            self.assertGreater(feature["asr_speech"]["speech_rate_cpm"], 0)
            self.assertGreater(feature["asr_speech"]["opening_hook_score"], 0)
            extract_wav.assert_called_once()

    def test_file_analysis_reads_items_and_video_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "items.json"
            video = workspace / "video.mp4"
            artifact.write_text(json.dumps({"items": [{"video_id": "v3", "video_path": str(video)}]}), encoding="utf-8")

            with patch.object(asr_features, "_load_whisper_model_factory", return_value=FakeWhisperModel):
                with patch.object(asr_features, "_extract_wav", return_value=None):
                    result = analyze_asr_features_file(workspace=workspace, artifact=artifact)

            self.assertTrue(result["ok"])
            item = result["result"]["results"][0]
            self.assertEqual(item["video_id"], "v3")
            self.assertEqual(item["asr_speech"]["duration_sec"], 6.0)

    def test_missing_dependency_does_not_overwrite_existing_feature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            artifact = workspace / "items.json"
            features_dir = workspace / "features"
            features_dir.mkdir()
            artifact.write_text(json.dumps({"result": {"items": [{"video_id": "v4", "video_path": "missing.mp4"}]}}), encoding="utf-8")
            (features_dir / "v4.json").write_text(json.dumps({"asr_speech": {"transcript": "已有文本"}}), encoding="utf-8")

            with patch.object(asr_features, "_load_whisper_model_factory", return_value=None):
                result = analyze_asr_features_file(workspace=workspace, artifact=artifact, features_dir=features_dir)

            feature = json.loads((features_dir / "v4.json").read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertEqual(feature["asr_speech"]["transcript"], "已有文本")

    def test_extract_wav_error_uses_safe_utf8_decode(self) -> None:
        completed = SimpleNamespace(returncode=1, stdout=b"", stderr="错误\xab".encode("utf-8"))

        with patch.object(asr_features.subprocess, "run", return_value=completed):
            with self.assertRaises(RuntimeError) as raised:
                asr_features._extract_wav(video_path=Path("bad.mp4"), wav_path=Path("out.wav"))

        self.assertIn("错误", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
