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

## 配置与运行

- 复制 `config.yaml.example` 为 `config.yaml` 后再按需调整
- 如果根目录下没有 `config.yaml`，程序会回退到示例默认值
- 配置里的相对路径都会按 workspace 目录统一解析
- 当前 CLI 入口仍是 `short-video-intel`

### 当前可用 CLI（当前实现）

- `short-video-intel bootstrap`
- `short-video-intel init-db`
- `short-video-intel import-targets --input targets.csv`
- `short-video-intel session-init --session-name main`
- `short-video-intel session-capture --session-name douyin-main`
- `short-video-intel open-debug-homepage --session-name douyin-main --homepage-url <url>`
- `short-video-intel crawl-homepage --homepage-url <url>`
- `short-video-intel crawl-homepage-cdp --homepage-url <url> --cdp-url http://127.0.0.1:9222`
- `short-video-intel crawl-video-detail --video-url <url>`
- `short-video-intel crawl-video-comments --video-url <url>`
- `short-video-intel build-download-jobs --videos-file videos.json --run`
- `short-video-intel build-download-jobs-from-artifact --artifact <homepage-or-full-batch.json> --max-videos 20 --run`
- `short-video-intel run-download-jobs --jobs-file <download_jobs.json> --workers 2`
- `short-video-intel crawl-targets-batch --source-file inputs/douyin_homepages_seed.tsv --workers 2`
- `short-video-intel crawl-targets-batch --from-db --limit 20 --persist-db`
- `short-video-intel crawl-targets-full-batch --source-file inputs/douyin_homepages_seed.tsv --with-video-detail --with-comments --comment-pages 3 --workers 2`
- `short-video-intel run-phase1-batch --from-db --limit 20 --session-name douyin-main`
- `short-video-intel generate-weekly-report --artifact <full-batch.json> --json-output <path> --md-output <path>`
- `short-video-intel generate-phase1-chunked-report --artifact <phase1_chunked_master.json> --json-output <path> --md-output <path>`
- `short-video-intel export-phase1-rerun-manifest --artifact <phase1_chunked_master.json> --output <path>`
- `short-video-intel phase1-status-overview --json-output <path> --md-output <path>`
- `short-video-intel phase1-recent-runs --limit 20 --json-output <path> --md-output <path>`
- `short-video-intel summarize-homepage-batch --artifact <batch.json> --json-output <path> --md-output <path>`
- `short-video-intel analyze-positive-factors --artifact <full-batch.json> --output <path>`
- `short-video-intel analyze-video-fit --input <detail-or-batch.json> --output <path>`
- `short-video-intel analyze-video-fit-full-batch --artifact <full-batch.json> --output <path>`

`import-targets` 现支持 `csv/json/tsv`，并支持中文表头映射（如“主页链接/账号名/分类/部门”）。

### 下载任务主链（新增）

现在可以直接把采集产物转成下载任务，不必手工整理视频 URL：

```powershell
# 从主页采集结果生成下载任务
short-video-intel build-download-jobs-from-artifact `
  --artifact .\artifacts\collector\homepage\homepage_xxx.json `
  --max-videos 10

# 从 full-batch / phase1_chunked master 直接抽视频并执行
short-video-intel build-download-jobs-from-artifact `
  --artifact .\artifacts\collector\full-batch\phase1_chunked_master_xxx.json `
  --max-videos 20 `
  --run `
  --workers 2

# 对已生成的 jobs 文件重复执行
short-video-intel run-download-jobs `
  --jobs-file .\artifacts\downloader\jobs\download_jobs_from_phase1_chunked_master_xxx.json `
  --workers 2
```

当前实现说明：

- 支持输入 `homepage / batch / full-batch / full-batch-chunks / phase1_chunked_master` artifact
- 下载任务会尽量保留：
  - `source_name`
  - `homepage_url`
  - `video_url`
  - `video_id`
  - `origin_artifact_path`
  - `origin_kind`
- 当前机器若未安装 `yt-dlp`，会自动回退到 stub，并在 `downloads\artifact\<账号名>\*.json` 留下可追踪结果
- 已安装 `yt-dlp` 时，会优先尝试真实下载
- 若 `yt-dlp` 因 `fresh cookies` 等问题失败，会继续尝试：
  - 浏览器辅助提取媒体直链
  - 再用 HTTP 直接落地视频文件
- 若两条真实下载链都失败，最后才回退到 stub

## 最短实操：扫码登录 + 一期批跑 + 周报生成

下面给一条最短链路，优先保证一期抓取稳定可复现。

### 1) session-capture（带 `--session-name`）典型流程

```powershell
# 第一步：打开浏览器扫码登录并落盘会话
short-video-intel session-capture `
  --session-name douyin-main `
  --homepage-url https://www.douyin.com/ `
  --wait-seconds 180

# 第二步：后续命令复用同一会话
short-video-intel --session-name douyin-main crawl-targets-batch --from-db --limit 5
```

### 2) 一期批跑（20 账号）示例

```powershell
short-video-intel run-phase1-batch `
  --session-name douyin-main `
  --from-db `
  --limit 20 `
  --workers 1 `
  --comment-pages 1 `
  --video-limit-per-target 8 `
  --comment-video-limit-per-target 4 `
  --browser-timeout-ms 120000
```

### 3) 慢网推荐：分块批跑

```powershell
short-video-intel run-phase1-batch `
  --session-name douyin-main `
  --from-db `
  --limit 20 `
  --workers 1 `
  --comment-pages 1 `
  --video-limit-per-target 8 `
  --comment-video-limit-per-target 4 `
  --browser-timeout-ms 120000 `
  --chunk-size 2 `
  --pause-seconds 3
```

这会产出：

- 每个 chunk 一个 artifact
- 一个 `phase1_chunked_master` 总汇总
- 如果有失败 target，还会有 rerun manifest

### 4) 周报生成示例（同时输出 JSON + Markdown）

```powershell
short-video-intel generate-weekly-report `
  --artifact .\artifacts\collector\full-batch\batch_full_collect_xxx.json `
  --json-output .\artifacts\reports\weekly-2026w17.json `
  --md-output .\artifacts\reports\weekly-2026w17.md
```

### 5) chunked 运行报告与补跑清单

```powershell
short-video-intel generate-phase1-chunked-report `
  --artifact .\artifacts\collector\full-batch\phase1_chunked_master_xxx.json `
  --json-output .\artifacts\reports\phase1-chunked-report.json `
  --md-output .\artifacts\reports\phase1-chunked-report.md

short-video-intel export-phase1-rerun-manifest `
  --artifact .\artifacts\collector\full-batch\phase1_chunked_master_xxx.json `
  --output .\artifacts\collector\full-batch-chunks\phase1-rerun.json
```

### 5.1) 从批跑到补跑的闭环示例

```powershell
# 1. 分块批跑
short-video-intel run-phase1-batch `
  --session-name douyin-main `
  --from-db `
  --limit 20 `
  --workers 1 `
  --comment-pages 1 `
  --video-limit-per-target 8 `
  --comment-video-limit-per-target 4 `
  --browser-timeout-ms 120000 `
  --chunk-size 2 `
  --pause-seconds 3

# 2. 为最新 chunked master 生成运维报告
short-video-intel generate-phase1-chunked-report `
  --json-output .\artifacts\analysis\phase1_chunked_report.json `
  --md-output .\artifacts\analysis\phase1_chunked_report.md

# 2.1 先快速看最新状态
short-video-intel phase1-status-overview `
  --json-output .\artifacts\analysis\phase1_status_overview.json `
  --md-output .\artifacts\analysis\phase1_status_overview.md

# 2.2 看最近运行历史
short-video-intel phase1-recent-runs `
  --limit 20 `
  --json-output .\artifacts\analysis\phase1_recent_runs.json `
  --md-output .\artifacts\analysis\phase1_recent_runs.md

# 2.3 汇总主页采集结果
short-video-intel summarize-homepage-batch `
  --artifact .\artifacts\collector\batch\batch_homepage_crawl_xxx.json `
  --json-output .\artifacts\analysis\homepage_batch_summary.json `
  --md-output .\artifacts\analysis\homepage_batch_summary.md

# 3. 导出失败 target，作为下一轮补跑清单
short-video-intel export-phase1-rerun-manifest `
  --output .\artifacts\collector\full-batch-chunks\phase1_rerun_manifest.json

# 4. 用补跑清单单独重跑
short-video-intel run-phase1-batch `
  --source-file .\artifacts\collector\full-batch-chunks\phase1_rerun_manifest.json `
  --no-from-db `
  --workers 1 `
  --comment-pages 1 `
  --video-limit-per-target 8 `
  --comment-video-limit-per-target 4 `
  --browser-timeout-ms 120000
```

如果你想先看一个已经生成好的离线样例，当前仓库里已有：

- `C:\Users\Administrator\Desktop\codex\short-video-intelligence-lab\artifacts\analysis\phase1_chunked_report_sample_20260422.json`
- `C:\Users\Administrator\Desktop\codex\short-video-intelligence-lab\artifacts\analysis\phase1_chunked_report_sample_20260422.md`
- `C:\Users\Administrator\Desktop\codex\short-video-intelligence-lab\artifacts\collector\full-batch-chunks\phase1_rerun_export_sample_20260422.json`

### 6) 一期 / 二期边界

- 一期优先：抓取链路、落库、批跑稳定性。
- 二期辅助：积极因素评分与视频适配分析，建立在一期产物之上。
- 决策顺序：先修抓取与覆盖率，再扩展评分和适配规则。

## 一期数据收集 + 二期积极因素评分雏形操作手册

这份手册按**一期优先收集数据、二期基于 `summary_block` 做积极因素评分**来组织。  
当前建议先把采集链路跑稳，再做评分和推荐。

### 1. 一期推荐执行顺序

1. `bootstrap`：初始化 workspace 目录
2. `init-db`：初始化数据库底座
3. `import-targets`：导入主页清单
4. `session-init`：生成或刷新会话状态
5. `crawl-targets-full-batch`：一次性跑主页 + 视频详情 + 评论
6. `persist-db`：把批次结果写回数据库

### 2. full-batch 的推荐用法

如果你的目标是“一次跑完一期可观测数据”，建议直接使用：

```powershell
short-video-intel crawl-targets-full-batch `
  --source-file inputs/douyin_homepages_seed.tsv `
  --with-video-detail `
  --with-comments `
  --comment-pages 3 `
  --workers 2 `
  --persist-db
```

如果你已经把目标导入数据库，也可以用：

```powershell
short-video-intel crawl-targets-full-batch `
  --from-db `
  --limit 20 `
  --with-video-detail `
  --with-comments `
  --comment-pages 3 `
  --persist-db
```

### 3. 如何查看 `summary_block`

`crawl-targets-full-batch` 的输出会包含一个 `summary` 字段，其中的核心汇总就是 `summary_block`。  
推荐先把结果保存下来，再单独查看：

```powershell
short-video-intel crawl-targets-full-batch `
  --source-file inputs/douyin_homepages_seed.tsv `
  --with-video-detail `
  --with-comments `
  --comment-pages 3 `
  --workers 2 `
  --persist-db |
  Tee-Object -FilePath .\artifacts\full-batch.json
```

然后读取 `summary_block`：

```powershell
(Get-Content .\artifacts\full-batch.json -Raw | ConvertFrom-Json).summary.summary_block |
  ConvertTo-Json -Depth 10
```

`summary_block` 里最值得先看的字段是：

- `account_summary`：每个主页的汇总条目
- `global_summary`：整批总体统计
- `account_summary[].videos_seen`：每个主页抓到的视频数
- `account_summary[].detail_success`：视频详情可成功提取的数量
- `account_summary[].comments_success`：评论侧可成功提取的数量
- `account_summary[].warnings_count`：该主页在批次里的 warning 数

### 4. 二期评分命令

二期雏形现在已经支持**直接消费 full-batch artifact** 的积极因素评分：

```powershell
short-video-intel analyze-positive-factors `
  --artifact .\artifacts\collector\full-batch\batch_full_collect_xxx.json `
  --output .\artifacts\positive-factors.json
```

这个命令的定位是：

- 输入：一期 full-batch 结果里的 `summary_block`
- 输出：账号积极因素分数、排序、建议
- 不读取原始视频内容，不抢一期采集资源

### 5. 一期与二期边界

#### 一期优先做的事

- 主页目标导入
- 主页视频发现
- 视频详情与评论采集
- 下载任务与媒体资产归档
- 批次结果落库

#### 二期再做的事

- 基于 `summary_block` 的积极因素评分
- 账号排序与对比
- 规则版推荐
- 后续再逐步接入更细的视觉 / 话术 / 模型特征

#### 边界原则

- 一期先保证“数据收集可重复、可落库、可复盘”
- 二期只消费一期的汇总结果，不倒逼一期增加复杂分析
- 如果一期数据不稳，优先修采集，再谈评分

## 目标校准

已在 `docs/goal-calibration.md` 固化当前目标与一期范围。

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

## 当前运行建议

建议当前按下面顺序推进：

1. 先用 `session-capture` 固化登录态
2. 用 `open-debug-homepage + crawl-homepage-cdp` 做主页活窗口验证
3. 用 `run-phase1-batch` 小范围跑通
4. 网络差时切 `--chunk-size`
5. 跑完后优先看：
   - `generate-phase1-chunked-report`
   - `export-phase1-rerun-manifest`
   - `generate-weekly-report`
6. 再进入二期评分与视频适配

## 说明

本仓库当前是“方案 + 骨架”版本，下一轮可以继续补：

- 真实采集适配器
- SQLite / Postgres 表结构
- 视频分析 pipeline
- ASR 批处理 worker
- CLI 命令与配置文件
## 当前本地默认浏览器模式

- `config.local.yaml` 当前默认配置为 `browser.headless: true`
- 也就是批量采集、批量下载、批量分析会尽量后台无窗口运行
- 只有 `session-capture` 这类人工登录步骤，或明确使用调试命令时，才需要弹出可见浏览器
