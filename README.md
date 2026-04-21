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

### 当前可用 CLI（骨架）

- `short-video-intel bootstrap`
- `short-video-intel init-db`
- `short-video-intel import-targets --input targets.csv`
- `short-video-intel session-init --session-name main`
- `short-video-intel crawl-homepage --homepage-url <url>`
- `short-video-intel crawl-video-detail --video-url <url>`
- `short-video-intel crawl-video-comments --video-url <url>`
- `short-video-intel build-download-jobs --videos-file videos.json --run`
- `short-video-intel crawl-targets-batch --source-file inputs/douyin_homepages_seed.tsv --workers 2`
- `short-video-intel crawl-targets-batch --from-db --limit 20 --persist-db`
- `short-video-intel crawl-targets-full-batch --source-file inputs/douyin_homepages_seed.tsv --with-video-detail --with-comments --comment-pages 3 --workers 2`

`import-targets` 现支持 `csv/json/tsv`，并支持中文表头映射（如“主页链接/账号名/分类/部门”）。

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

### 4. 二期评分命令（预期）

二期雏形会增加一个**只消费 `summary_block`** 的积极因素评分命令。  
下面是预期命令形态，主线实现后即可直接使用：

```powershell
short-video-intel analyze-positive-factors `
  --summary-file .\artifacts\full-batch.json `
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
