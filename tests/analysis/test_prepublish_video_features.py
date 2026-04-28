from __future__ import annotations

import unittest

from short_video_intel.analysis.prepublish_video_features import _analyze_text_features


class PrepublishVideoFeaturesTest(unittest.TestCase):
    def test_text_features_detect_hook_structure_and_cta(self) -> None:
        """拟发布文本应能提取钩子、方法、例子和评论引导。"""
        result = _analyze_text_features(
            planned_title="孩子阅读总丢分？三步训练方法",
            planned_caption="评论年级，领取资料。",
            script_text="很多孩子不是不会，而是方法错了。比如这道题，先找关键词，再定位原文。",
        )
        self.assertGreaterEqual(result["structure_completeness"], 0.8)
        self.assertGreater(result["opening_hook_score"], 0.5)
        self.assertGreater(result["knowledge_density"], 0.2)

    def test_empty_text_keeps_low_structure(self) -> None:
        """没有拟发布文本时，结构特征必须保持低分。"""
        result = _analyze_text_features(planned_title="", planned_caption="", script_text="")
        self.assertEqual(result["structure_completeness"], 0)
        self.assertEqual(result["opening_hook_score"], 0)
        self.assertEqual(result["knowledge_density"], 0)


if __name__ == "__main__":
    unittest.main()
