# 目标校准（2026-04-21）

## 你确认的最终目标

1. 基于采集与下载的数据，分析在可获取维度内促成账号正向增长的积极因素。  
2. 输入一个视频，评估其与指定账号的适配性，并输出改进建议。  

---

## 一期范围（当前）

当前一期仍聚焦**数据收集基础能力**：

- 主页目标导入
- 主页视频发现
- 视频详情与评论采集（先框架，逐步提取）
- 下载任务生成与下载链路（ytdlp + stub fallback）
- 结构化存储与产物归档

---

## 数据到目标的映射

### 对目标 1（积极因素分析）

依赖先落地的数据层：

- 账号维度：账号类型、部门、分类、UID
- 内容维度：视频 URL、发布时间、标题（后续补）
- 互动维度：播放/点赞/评论/转发快照
- 评论维度：评论规模、回复结构、活跃节奏
- 直播维度：直播链接是否存在（先静态字段）

### 对目标 2（视频适配与改进建议）

依赖后续二期分析层：

- 账号画像（历史题材、表达风格、互动结构）
- 视频画像（话术结构、视觉特征、互动预测）
- 匹配评分（内容主题匹配、形式匹配、受众匹配）

---

## 近期执行建议

1. 先用 `inputs/douyin_homepages_seed.tsv` 跑通导入与采集批处理。  
2. 优先把 `with-video-detail + with-comments + persist-db` 跑稳，再扩大主页数。  
3. 采集输出务必保留原始 JSON 和 `summary_block`，方便后续回放与评分。  
4. 如果一期采集不稳定，先修采集完整性，不要先堆评分逻辑。

### 最短实操链路（扫码登录 + 一期批跑 + 周报）

```powershell
# A. 先做扫码登录并保存会话
short-video-intel session-capture `
  --session-name douyin-main `
  --homepage-url https://www.douyin.com/ `
  --wait-seconds 180

# B. 一期批跑（20 账号）
short-video-intel run-phase1-batch `
  --session-name douyin-main `
  --from-db `
  --limit 20 `
  --with-video-detail `
  --with-comments `
  --comment-pages 3 `
  --persist-db `
  --output .\artifacts\phase1\phase1-batch-20.json

# C. 生成周报（输出 json + md）
short-video-intel generate-weekly-report `
  --input .\artifacts\phase1\phase1-batch-20.json `
  --output-json .\artifacts\reports\weekly-2026w17.json `
  --output-md .\artifacts\reports\weekly-2026w17.md
```

> 核心边界：一期抓取优先（采集/落库/批跑稳定性）；二期评分与适配为辅助能力，只消费一期产物，不反向绑架一期采集节奏。

### 一期推荐命令

```powershell
short-video-intel crawl-targets-full-batch `
  --source-file inputs/douyin_homepages_seed.tsv `
  --with-video-detail `
  --with-comments `
  --comment-pages 3 `
  --workers 2 `
  --persist-db
```

### 查看 `summary_block`

```powershell
(Get-Content .\artifacts\full-batch.json -Raw | ConvertFrom-Json).summary.summary_block |
  ConvertTo-Json -Depth 10
```

---

## 二期分析的优先消费对象

二期开始做账号/视频分析时，建议先直接消费 `crawl-targets-full-batch` 产出的 `summary_block`，其中：

- `account_summary` 适合先做账号级聚类、横向对比和异常定位
- `global_summary` 适合看整批采集覆盖率、详情/评论成功率、失败率

等这层汇总稳定后，再逐步下钻到单视频、单评论、单帧特征。

---

## 二期积极因素评分雏形（规则版）

当前已经预留了一个**离线规则版**的账号积极因素评分入口，输入仍然是 `summary_block`，主要消费：

- `account_summary.videos_seen`
- `account_summary.detail_success`
- `account_summary.comments_success`
- `account_summary.warnings_count`
- `global_summary` 中的整体成功率 / 失败率

### 预期命令形态

主线实现后，建议用一个只消费 `summary_block` 的命令来承接二期雏形，例如：

```powershell
short-video-intel analyze-positive-factors `
  --summary-file .\artifacts\full-batch.json `
  --output .\artifacts\positive-factors.json
```

### 目前的规则思路

这只是一个**雏形版本**，还不是机器学习模型，也不直接看视频内容：

- `activity_score`：看账号在批量里可见的内容量和数据量
- `execution_score`：看视频详情数据的可获得程度
- `interaction_score`：看评论侧数据的可获得程度
- `stability_score`：看 warning 数量，warning 越少分越高
- `total_score`：四项加权得到的综合分

### 当前阶段的定位

- 这是**规则版**，用于先把“可解释的筛选逻辑”跑起来
- 结果适合做账号排序、初筛和二期特征工程的参考
- 后续可以再升级成“规则 + 特征 + 模型”的版本

### 一期与二期边界说明

- **一期优先级最高**：主页导入、视频发现、评论快照、下载、落库、产物归档
- **二期不抢一期输入**：积极因素评分只消费一期产出的 `summary_block`
- **二期不替代采集**：评分只能辅助排序和复盘，不能代替真实数据收集
- **一旦一期数据不稳**：先修采集完整性，再扩展评分规则
