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
                "planned_title": "PET考试资料怎么领取？三步备考方法",
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
        noisy_row = {**rows[0], "like_count": 999999, "comment_count": 999999, "share_count": 999999}
        noisy_result = score_video(noisy_row, [{"topics": ["家长咨询"], "sentiment": "咨询", "is_author_reply": True}], model)

        self.assertEqual(model["version"], "video-effect-evaluator.prepublish.v1")
        self.assertGreater(result["effect_score"], 60)
        self.assertIn("资料领取", result["planned_topics"])
        self.assertEqual(result["effect_score"], noisy_result["effect_score"])
        self.assertIn("comments", noisy_result["ignored_runtime_fields"])


if __name__ == "__main__":
    unittest.main()

