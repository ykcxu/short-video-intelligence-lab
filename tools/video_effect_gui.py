from __future__ import annotations

import html
import json
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, Button, Entry, Frame, Label, StringVar, Text, Tk, filedialog, messagebox

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from short_video_intel.analysis.prepublish_video_features import build_pre_publish_row
from short_video_intel.analysis.video_effect_evaluator import score_video

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
REPORT_DIR = APP_DIR / "output" / "gui_reports"
WORK_DIR = APP_DIR / "output" / "gui_work"


def main() -> int:
    """启动图形界面；测试模式用于打包后快速自检。"""
    _prepare_runtime_path()
    if "--self-test" in sys.argv:
        return _self_test()
    app = VideoEffectGui()
    app.mainloop()
    return 0


class VideoEffectGui(Tk):
    """上架前视频评价器 GUI。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("短视频上架前效果评分器")
        self.geometry("920x720")
        self.video_path = StringVar()
        self.report_path = ""
        self._build_layout()

    def _build_layout(self) -> None:
        """搭建上传、文本输入、评分结果三块界面。"""
        file_frame = Frame(self)
        file_frame.pack(fill="x", padx=12, pady=10)
        Button(file_frame, text="选择视频", command=self._choose_video, width=14).pack(side=LEFT)
        Entry(file_frame, textvariable=self.video_path).pack(side=LEFT, fill="x", expand=True, padx=8)
        Button(file_frame, text="开始评分", command=self._run_score, width=14).pack(side=RIGHT)

        self.title_input = _labeled_text(self, "拟发布标题（可选，但建议填写）：", height=2)
        self.caption_input = _labeled_text(self, "拟发布文案 / 评论引导（可选）：", height=4)
        self.script_input = _labeled_text(self, "口播脚本 / 视频话术（可选，越完整评分越准）：", height=8)

        result_frame = Frame(self)
        result_frame.pack(fill=BOTH, expand=True, padx=12, pady=8)
        Label(result_frame, text="评分输出：").pack(anchor="w")
        self.result_output = Text(result_frame, height=16, wrap="word")
        self.result_output.pack(fill=BOTH, expand=True)

        action_frame = Frame(self)
        action_frame.pack(fill="x", padx=12, pady=8)
        Button(action_frame, text="打开 HTML 报告", command=self._open_report, width=18).pack(side=LEFT)
        Button(action_frame, text="清空结果", command=self._clear_result, width=12).pack(side=LEFT, padx=8)

    def _choose_video(self) -> None:
        """选择本地视频文件。"""
        path = filedialog.askopenfilename(
            title="选择要评分的视频",
            filetypes=[
                ("视频文件", "*.mp4 *.mov *.m4v *.webm *.mkv *.avi"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.video_path.set(path)

    def _run_score(self) -> None:
        """执行轻量特征抽取、上架前评分和 HTML 报告生成。"""
        try:
            row = build_pre_publish_row(
                {
                    "video_path": self.video_path.get(),
                    "planned_title": _text_value(self.title_input),
                    "planned_caption": _text_value(self.caption_input),
                    "script_text": _text_value(self.script_input),
                    "work_dir": str(WORK_DIR),
                    "model_workspace": str(_model_workspace()),
                    "model_root": str(_model_workspace() / "artifacts" / "models"),
                    "full_mode": True,
                }
            )
            model = _load_json(_model_path())
            result = score_video(row, [], model)
            self.report_path = str(_write_html_report(row=row, result=result))
            self._show_result(row=row, result=result)
        except Exception as exc:
            messagebox.showerror("评分失败", str(exc))

    def _show_result(self, *, row: dict, result: dict) -> None:
        """把关键评分、风险和建议展示在界面上。"""
        self.result_output.delete("1.0", END)
        lines = [
            f"总分：{result.get('effect_score')} / 100",
            f"等级：{_level_name(result.get('effect_level'))}",
            f"画面/出镜/字幕分：{result.get('production_quality_score')}",
            f"内容结构分：{result.get('content_structure_score')}",
            f"评论潜力分：{result.get('comment_potential_score')}",
            "",
            "风险：",
            *[f"- {item}" for item in result.get("risks", [])],
            "",
            "改进建议：",
            *[f"- {item}" for item in result.get("actions", [])],
            "",
            "特征告警：",
            *[f"- {item}" for item in row.get("feature_warnings", [])],
            "",
            f"HTML 报告：{self.report_path}",
        ]
        self.result_output.insert(END, "\n".join(lines))

    def _open_report(self) -> None:
        """用默认浏览器打开最近一次评分报告。"""
        if not self.report_path:
            messagebox.showinfo("暂无报告", "请先选择视频并开始评分。")
            return
        webbrowser.open(Path(self.report_path).resolve().as_uri())

    def _clear_result(self) -> None:
        """清空界面中的评分结果。"""
        self.result_output.delete("1.0", END)


def _labeled_text(root: Tk, label: str, *, height: int) -> Text:
    frame = Frame(root)
    frame.pack(fill="x", padx=12, pady=5)
    Label(frame, text=label).pack(anchor="w")
    widget = Text(frame, height=height, wrap="word")
    widget.pack(fill="x")
    return widget


def _text_value(widget: Text) -> str:
    return widget.get("1.0", END).strip()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _model_path() -> Path:
    """优先读取 PyInstaller 内置模型，其次读取仓库本地模型。"""
    bundle_dir = Path(getattr(sys, "_MEIPASS", ROOT))
    bundled = bundle_dir / "artifacts" / "analysis" / "video_effect_model.json"
    if bundled.exists():
        return bundled
    return ROOT / "artifacts" / "analysis" / "video_effect_model.json"


def _model_workspace() -> Path:
    """返回模型所在工作区；打包后指向 PyInstaller 解包目录。"""
    return Path(getattr(sys, "_MEIPASS", ROOT))


def _prepare_runtime_path() -> None:
    """把 PyInstaller 解包目录加入 PATH，保证内置 ffmpeg/ffprobe 可被 subprocess 找到。"""
    bundle_dir = Path(getattr(sys, "_MEIPASS", ""))
    if not bundle_dir.exists():
        return
    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(bundle_dir) + os.pathsep + current_path


def _write_html_report(*, row: dict, result: dict) -> Path:
    """生成单视频上架前评价 HTML，便于直接发给团队核验。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    token = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"{row.get('video_id')}_{token}_上架前评分.html"
    report_path.write_text(_render_html(row=row, result=result), encoding="utf-8")
    return report_path


def _render_html(*, row: dict, result: dict) -> str:
    risks = "".join(f"<li>{_esc(item)}</li>" for item in result.get("risks", []))
    actions = "".join(f"<li>{_esc(item)}</li>" for item in result.get("actions", []))
    warnings = "".join(f"<li>{_esc(item)}</li>" for item in row.get("feature_warnings", []))
    fit = row.get("local_video_fit") if isinstance(row.get("local_video_fit"), dict) else {}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>上架前视频评分报告</title>
  <style>
    body {{ font-family: "Microsoft YaHei", Arial, sans-serif; margin: 32px; color: #222; }}
    .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 18px; margin-bottom: 16px; }}
    .score {{ font-size: 42px; font-weight: 700; color: #1f6feb; }}
    table {{ border-collapse: collapse; width: 100%; }}
    td, th {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    code {{ background: #f6f8fa; padding: 2px 4px; }}
  </style>
</head>
<body>
  <h1>上架前视频评分报告</h1>
  <div class="card">
    <div class="score">{_esc(result.get("effect_score"))}</div>
    <p>等级：{_esc(_level_name(result.get("effect_level")))}</p>
    <p>视频文件：<code>{_esc(row.get("video_url"))}</code></p>
    <p>说明：本报告只使用视频内容特征、ASR/OCR/人物姿态特征与拟发布文本，不使用播放、点赞、评论、转发等上架后数据。</p>
  </div>
  <div class="card">
    <h2>分项评分</h2>
    <table>
      <tr><th>维度</th><th>分数</th></tr>
      <tr><td>画面 / 出镜 / 字幕</td><td>{_esc(result.get("production_quality_score"))}</td></tr>
      <tr><td>口播结构</td><td>{_esc(result.get("content_structure_score"))}</td></tr>
      <tr><td>评论潜力</td><td>{_esc(result.get("comment_potential_score"))}</td></tr>
      <tr><td>本地画面适配</td><td>{_esc(fit.get("fit_score"))}</td></tr>
    </table>
  </div>
  <div class="card"><h2>主要风险</h2><ul>{risks}</ul></div>
  <div class="card"><h2>改进建议</h2><ul>{actions}</ul></div>
  <div class="card"><h2>特征告警</h2><ul>{warnings}</ul></div>
  <div class="card">
    <h2>拟发布文本</h2>
    <p><b>标题：</b>{_esc(row.get("planned_title"))}</p>
    <p><b>文案：</b>{_esc(row.get("planned_caption"))}</p>
    <p><b>脚本：</b>{_esc(row.get("script_text"))}</p>
  </div>
</body>
</html>"""


def _level_name(value: object) -> str:
    mapping = {"excellent": "优秀", "good": "较好", "medium": "一般", "weak": "偏弱"}
    return mapping.get(str(value), str(value or "未知"))


def _esc(value: object) -> str:
    return html.escape(str(value or ""))


def _self_test() -> int:
    model_path = _model_path()
    model_workspace = _model_workspace()
    payload = {
        "ok": model_path.exists(),
        "model_path": str(model_path),
        "model_workspace": str(model_workspace),
        "asr_model_exists": (model_workspace / "artifacts" / "models" / "faster-whisper-tiny" / "model.bin").exists(),
        "easyocr_model_exists": (model_workspace / "artifacts" / "models" / "easyocr" / "model" / "zh_sim_g2.pth").exists(),
        "pose_model_exists": (model_workspace / "artifacts" / "models" / "pose_landmarker_lite.task").exists(),
    }
    (APP_DIR / "self_test_result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if all(value for key, value in payload.items() if key.endswith("_exists") or key == "ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
