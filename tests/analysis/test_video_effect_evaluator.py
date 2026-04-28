import unittest

from short_video_intel.analysis.video_effect_evaluator import score_video, train_effect_model


class VideoEffectEvaluatorTestCase(unittest.TestCase):
    def test_train_and_score_video(self) -> None:
        rows = [
            {
                "video_id": "a",
                "source_name": "账号A",
                "video_url": "https://www.douyin.com/video/a",
                "metric_suspicious": False,
                "trusted_engagement_score": 100,
                "comment_count": 10,
                "fit_score": 75,
                "ocr_readability": 0.8,
                "subtitle_consistency": 0.6,
                "structure_completeness": 0.6,
                "face_center_score": 0.8,
                "pose_facing_score": 0.9,
                "speech_rate_cpm": 260,
                "person_count": 1,
            },
            {
                "video_id": "b",
                "source_name": "账号B",
                "video_url": "https://www.douyin.com/video/b",
                "metric_suspicious": False,
                "trusted_engagement_score": 10,
                "comment_count": 0,
                "fit_score": 55,
                "ocr_readability": 0.4,
                "subtitle_consistency": 0.2,
                "structure_completeness": 0.0,
                "face_center_score": 0.5,
                "pose_facing_score": 0.7,
                "speech_rate_cpm": 420,
                "person_count": 3,
            },
        ]
        comments = {"a": [{"topics": ["资料领取"], "sentiment": "咨询", "is_author_reply": True}]}

        model = train_effect_model(rows, comments)
        result = score_video(rows[0], comments["a"], model)

        self.assertEqual(model["version"], "video-effect-evaluator.v1")
        self.assertGreater(result["effect_score"], 60)
        self.assertIn("资料领取", result["comment_topics"])


if __name__ == "__main__":
    unittest.main()
