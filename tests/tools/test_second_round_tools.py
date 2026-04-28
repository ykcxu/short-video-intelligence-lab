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


if __name__ == "__main__":
    unittest.main()
