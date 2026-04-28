from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from short_video_intel.analysis.video_effect_evaluator import score_video
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def main() -> int:
    """读取单条视频特征并输出效果评分。"""
    parser = argparse.ArgumentParser(description="视频效果评价器")
    parser.add_argument("--model", required=True, help="模型 JSON 路径")
    parser.add_argument("--input", required=True, help="输入 JSON，包含 row 和 comments")
    parser.add_argument("--output", help="可选输出 JSON 路径")
    args = parser.parse_args()
    model = _load_json(Path(args.model))
    payload = _load_json(Path(args.input))
    result = score_video(payload.get("row", {}), payload.get("comments", []), model)
    text = json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0
def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
if __name__ == "__main__":
    raise SystemExit(main())
