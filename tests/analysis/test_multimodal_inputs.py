import json
import tempfile
import unittest
from pathlib import Path

from src.short_video_intel.analysis.multimodal_inputs import prepare_multimodal_inputs


class MultimodalInputsTest(unittest.TestCase):
    def test_prepare_inputs_merges_feature_file_by_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            features_dir = workspace / "features"
            features_dir.mkdir()
            local_fit = workspace / "local_fit.json"
            output = workspace / "out" / "inputs.json"
            local_fit.write_text(
                json.dumps(
                    {
                        "result": {
                            "results": [
                                {
                                    "video_id": "v1",
                                    "video_url": "https://example.test/v1",
                                    "source_name": "样例账号",
                                    "fit": {"fit_score": 76},
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (features_dir / "v1.json").write_text(
                json.dumps({"face_quality": {"face_detected": True}, "debug": {"raw": True}}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = prepare_multimodal_inputs(
                workspace=workspace,
                local_fit_artifact=local_fit,
                features_dir=features_dir,
                output=output,
            )

            item = result["result"]["items"][0]
            self.assertTrue(result["ok"])
            self.assertEqual(item["local_video_fit"]["fit_score"], 76)
            self.assertEqual(item["face_quality"]["face_detected"], True)
            self.assertNotIn("debug", item)
            self.assertTrue(output.exists())

    def test_missing_feature_dir_keeps_local_fit_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            local_fit = workspace / "local_fit.json"
            local_fit.write_text(json.dumps({"result": {"results": [{"video_id": "v2", "fit": {}}]}}), encoding="utf-8")

            result = prepare_multimodal_inputs(workspace=workspace, local_fit_artifact=local_fit)

            self.assertEqual(result["result"]["total"], 1)
            self.assertEqual(result["result"]["summary"]["modality_coverage"]["face_quality"], 0)


if __name__ == "__main__":
    unittest.main()
