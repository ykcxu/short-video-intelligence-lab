# Short Video Intelligence Lab

一个面向短视频平台的研究与工程项目骨架，目标是逐步实现：

- 主页视频发现与批量下载
- 视频互动与评论快照采集
- 画面质量 / 人脸 / 姿态 / 镜头级特征提取
- 视频语音转写与话术结构分析
- 面向后续迭代的数据落库与批处理流程

## 当前状态

当前仓库先落地了：

- 调研结论文档
- 系统架构设计
- 推荐技术栈与模块边界
- Python 项目基础骨架
- 后续迭代路线图

## 目录结构

```text
.
├─ docs/
│  ├─ research-summary.md
│  ├─ architecture.md
│  └─ roadmap.md
├─ src/
│  └─ short_video_intel/
│     ├─ __init__.py
│     ├─ cli.py
│     ├─ config.py
│     ├─ models.py
│     └─ orchestrator.py
├─ .gitignore
├─ pyproject.toml
└─ README.md
```

## 目标能力

### 1. 数据采集

- 优先走官方授权接口
- 无授权时走浏览器自动化 / 页面抓取 / 第三方下载器
- 将视频元数据、评论、回复、互动数按时间快照入库

### 2. 视频下载

- 公开视频下载主链路
- 失败后保留 URL 与元数据，避免阻塞主任务

### 3. 视觉分析

- 画面质量 / 审美分
- 人脸检测、表情、脸部特征
- 姿态关键点与动作派生特征
- 清晰度、亮度、镜头切分、稳定性

### 4. 话术分析

- 音频抽取与规范化
- ASR 转写
- 切句、关键词、主题与脚本结构分析

## 推荐技术栈

- 采集：`httpx`、`playwright`
- 下载：专用 downloader、`yt-dlp` 兜底
- 视觉：`pyiqa`、`InsightFace`、`DeepFace`、`MMPose`、`MediaPipe`、`OpenCV`
- 语音：`ffmpeg`、`faster-whisper`
- 存储：`SQLite` / `Postgres`、`JSONL`、`Parquet` / `DuckDB`

## 下一步

建议先按下面顺序推进：

1. 明确采集目标平台与授权边界
2. 先做单账号 / 单主页采集 MVP
3. 打通视频下载 + 评论快照落库
4. 加入视觉特征抽取
5. 加入话术分析并做批处理
6. 最后做队列化、多进程、多 GPU 优化

## 说明

本仓库当前是“方案 + 骨架”版本，下一轮可以继续补：

- 真实采集适配器
- SQLite / Postgres 表结构
- 视频分析 pipeline
- ASR 批处理 worker
- CLI 命令与配置文件
