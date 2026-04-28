import importlib.util
import unittest
from pathlib import Path


def _load_module(name: str, filename: str):
    tool_path = Path(__file__).resolve().parents[2] / "tools" / filename
    spec = importlib.util.spec_from_file_location(name, tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class SecondRoundToolsTestCase(unittest.TestCase):
    def test_spread_indexes_cover_head_middle_tail(self) -> None:
        module = _load_module("build_strict_multimodal_inputs", "build_strict_multimodal_inputs.py")

        self.assertEqual(module._spread_indexes(10, 5), [0, 2, 4, 7, 9])
        self.assertEqual(module._spread_indexes(3, 1), [0])

    def test_recommendations_expose_multimodal_shortfalls(self) -> None:
        module = _load_module("build_second_round_analysis", "build_second_round_analysis.py")
        rows = [
            {
                "structure_completeness": 0.1,
                "subtitle_consistency": 0.2,
                "person_count": 2.0,
                "face_center_score": 0.5,
            }
        ]

        tips = module._recommendations(rows)

        self.assertTrue(any("口播结构" in item for item in tips))
        self.assertTrue(any("字幕一致性" in item for item in tips))
        self.assertTrue(any("多主体" in item for item in tips))

    def test_full_batch_status_summarizes_chunks(self) -> None:
        module = _load_module("run_multimodal_full_batch", "run_multimodal_full_batch.py")
        chunks = [{"index": 1, "count": 2}, {"index": 2, "count": 1}]
        results = [
            {"index": 1, "count": 2, "return_code": 0},
            {"index": 2, "count": 1, "return_code": 124},
        ]

        status = module._build_status(Path("w"), [], chunks, results)

        self.assertEqual(status["chunk_count"], 2)
        self.assertEqual(status["completed_chunk_count"], 1)
        self.assertEqual(status["failed_count"], 1)
        self.assertEqual(status["processed_video_count"], 2)

    def test_full_multimodal_risk_counts_and_correlation(self) -> None:
        module = _load_module("build_full_multimodal_analysis", "build_full_multimodal_analysis.py")
        rows = [
            {
                "person_count": 2.0,
                "structure_completeness": 0.1,
                "subtitle_consistency": 0.2,
                "face_ratio": 0.01,
                "face_center_score": 0.6,
                "speech_rate_cpm": 380,
                "fit_score": 60,
                "engagement_score": 10,
            },
            {
                "person_count": 1.0,
                "structure_completeness": 0.8,
                "subtitle_consistency": 0.9,
                "face_ratio": 0.03,
                "face_center_score": 0.9,
                "speech_rate_cpm": 240,
                "fit_score": 80,
                "engagement_score": 30,
            },
        ]

        risks = module._risk_counts(rows)
        corr = module._correlation_hint(rows)

        self.assertEqual(risks["multi_person_or_unstable_subject"], 1)
        self.assertEqual(risks["low_structure"], 1)
        self.assertEqual(risks["speech_too_fast"], 1)
        self.assertGreater(corr["fit_score"], 0)


if __name__ == "__main__":
    unittest.main()
