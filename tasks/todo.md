# 短视频情报项目 TODO

> 更新时间：2026-04-28  
> 当前阶段：一期数据采集与严格有效池修复。  
> 当前原则：先保证数据可信，再扩大评论与低覆盖账号补采；能自动推进的任务尽量拆成互不冲突的小批次。

## 当前状态快照

- Git 最新提交：`587f6c4 增加批量多模态流水线`（本轮推进：评论补采预期分类与批处理过滤）
- 严格有效池：输入视频 `628`，严格有效 `362`，保留率 `57.64%`
- 过滤原因：`quality_report_filtered=26`，`missing_required_assets=5`，`not_homepage_observed=133`，`detail_account_not_mentioned=89`
- 评论补采：detail `623`，已有评论产物视频 `389`，仍需补采目标 `277`；其中详情评论数为 0 的 `131`，页面运行时明确无评论 `5`，仍应继续补采 `141`
- 下载补采目标：`5` 个账号，其中 `2` 个账号需要刷新数据集；本地 MP4 文件约 `630`
- 高优先级异常账号：`希望学小学` 严格有效 `0/62`，`紫一老师讲剑桥` 严格有效 `18/57`，`unknown` 严格有效 `0/8` 且本地 MP4 为 0


### 账号级缺口快照

| 账号 | processed | MP4 | detail | 评论非空 | 严格有效 | 严格缺口 | 评论补采缺口 | 优先级 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| unknown | 8 | 0 | 3 | 2 | 0 | 50 | 3 | 高 |
| 希望学小学 | 62 | 64 | 62 | 33 | 0 | 50 | 46 | 高 |
| 紫一老师讲剑桥 | 57 | 58 | 57 | 22 | 18 | 32 | 39 | 高 |
| 紫一剑桥英语 | 59 | 59 | 59 | 6 | 22 | 28 | 54 | 中 |
| 希望学小学英语@航航老师 | 59 | 59 | 59 | 5 | 38 | 12 | 54 | 中 |
| 希望学英语@哈佛小哈老师 | 58 | 58 | 58 | 5 | 38 | 12 | 53 | 中 |
| 子琦老师讲语文 | 78 | 81 | 78 | 16 | 51 | 0 | 65 | 低 |
| 关关讲小学语文 | 72 | 74 | 72 | 13 | 56 | 0 | 61 | 低 |
| 老尼讲语文 | 87 | 87 | 87 | 10 | 66 | 0 | 78 | 低 |
| 原学而思小学规划（希望学） | 88 | 90 | 88 | 12 | 73 | 0 | 79 | 低 |

## P0：数据可信度与采集闭环

- [ ] P0-1 复核并重采 `希望学小学` 主页
  - 当前问题：62 条输入视频严格有效为 0，疑似采到了推荐流/无关内容。
  - 验收：主页采集 artifact 中视频标题/详情能命中账号，严格有效视频数 > 0。
- [ ] P0-2 定向重采 `紫一老师讲剑桥`
  - 当前问题：57 条输入仅 18 条严格有效，仍有大量详情账号不匹配。
  - 验收：严格有效数提升到 50 条附近，或明确剩余过滤原因不可修复。
- [ ] P0-3 完成下载/detail 覆盖对齐
  - 当前问题：补采下载状态显示 `希望学小学`、`紫一老师讲剑桥` 仍需刷新数据集。
  - 验收：`backfill_download_status.needs_dataset_refresh_count = 0`。
- [x] P0-4 评论补采继续小批次推进
  - 当前问题：待补评论目标 `532`，其中 `493` 条没有任何评论产物；当前缺少正式批量补采入口。
  - 验收：已新增 `tools/run_comment_backfill_batch.py`，支持 dry-run、小批次、重试、JSON 摘要和日志；当前待补目标已下降到 `277`。
- [x] P0-5 评论补采命中率诊断
  - 当前问题：修复后新跑 20 条，多数产物真实评论为 0，需要区分无评论、页面未展开、登录/风控、接口无响应。
  - 验收：已新增 `tools/build_comment_failure_diagnostics.py`，输出 `artifacts/status/comment_failure_diagnostics.{json,md}`；当前待补目标 `277` 条中详情评论数为 0 的 `131`，页面运行时明确无评论 `5`，仍应继续补采 `141`。
- [x] P0-6 评论补采目标区分“无评论/未取到”
  - 当前问题：此前补采队列会混入详情评论数为 0 的视频，容易误判为“抓取失败”。
  - 验收：`comment_backfill_targets.json` 已写入 `comment_expected_status` 和 `should_backfill_comment`；已识别 `no_comment_observed` 并从后续补采队列剔除；`run_comment_backfill_batch.py` 已支持 `--only-comment-expected`。

## P1：自动化与运维面板

- [x] P1-1 增加评论补采状态报告
  - 输出 `artifacts/status/comment_backfill_status.{json,md}`，展示真实评论覆盖、噪声产物、空产物、待补数量。已实现 `tools/build_comment_backfill_status.py`。
- [x] P1-2 将评论补采状态接入 `run_phase1_analysis_pipeline.py`
  - 验收：一键流水线能刷新数据集、严格池、下载状态、评论状态、运行摘要。已通过 `phase1_pipeline_with_comment_status_20260427.out.log` 验证。
- [x] P1-3 更新运行总览 `run_summary`
  - 加入评论待补目标、真实评论命中、最新评论批次日志。已输出到 `artifacts/status/run_summary.{json,md}`。
- [x] P1-4 增加 artifact 索引/最近运行历史命令
  - 验收：已新增 `tools/build_artifact_index.py`，输出 `artifacts/artifact_index.{json,md}`，能查看最近产物、运行日志和非空错误日志。
- [x] P1-5 批量评论补采工具
  - 当前问题：`crawl-video-comments` 只支持单视频，批量、limit、retry、log 依赖外层 PowerShell。
  - 验收：已新增 `tools/run_comment_backfill_batch.py`，支持 dry-run、小批次、重试、JSON 摘要和日志。

## P2：采集能力增强

- [ ] P2-1 主页采集异常分类
  - 分类：空主页、推荐流误入、加载慢、风控、结构异常。
- [ ] P2-2 详情质量分层
  - 区分“内容可信但指标异常”和“内容归因错误”。
- [ ] P2-3 评论分页与面板激活策略增强
  - 当前评论采集最多抽样，且部分视频抓到 0 条，需要优化打开评论面板与接口监听。
- [ ] P2-4 下载任务与 processed 对齐检查自动化
  - 当前已有状态报告，但需把“本地有 MP4、processed 缺记录”的修复流程自动串起来。

## P3：分析层推进

- [x] P3-1 严格有效池上的账号正向因素报告复核
  - 当前已有 `positive_factors_strict_valid_report`，已生成第一版分析 `docs/analysis/first_pass_account_video_analysis_20260427.md`；低覆盖账号仍需后续修复。
- [ ] P3-2 视频适配分析接入真实账号画像
  - 输入一个视频后，按账号历史高表现内容给出适配与改进建议。
- [x] P3-3 视觉/话术特征 MVP
  - 已新增多模态融合评分骨架，先支持人脸/姿态/人物主体/OCR/ASR/话术结构等外部特征接入与可解释融合评分。
  - 已接入 ASR/OCR 抽取命令；当前依赖为可选安装，未安装模型库时显式返回 `missing_dependency`，不伪造转写或字幕。
  - 已接入人脸、人物主体与姿态关键点检测；新增 `tools/run_multimodal_batch.py` 支持小批量多模态流水线。

## 可并行子任务拆分

### 子任务 A：数据状态与缺口报告
- 负责人建议：只读 explorer 或轻量 worker。
- 输出：评论状态报告、严格池缺口解释、账号覆盖表。
- 写入范围：`tools/build_*status*.py`、`tests/tools/*status*`。

### 子任务 B：采集流程增强
- 负责人建议：worker。
- 输出：评论失败分类、主页异常分类、补采命令链。
- 写入范围：`src/short_video_intel/collector/*`、`src/short_video_intel/pipelines/*`、对应测试。

### 子任务 C：低覆盖账号补采
- 负责人建议：主进程执行，避免多个浏览器任务互相抢登录态。
- 输出：`希望学小学`、`紫一老师讲剑桥` 重新主页采集、下载、detail、pipeline。
- 写入范围：`artifacts/`、`downloads/`、`data/`，不提交大数据。

### 子任务 D：分析层复核
- 负责人建议：explorer。
- 输出：严格有效池正向因素是否可解释、哪些账号数据不足不能解释。
- 写入范围：分析报告脚本或只读结论。

## 当前最推荐执行顺序

1. `P1-1/P1-2/P1-3` 已完成，评论补采状态已进入一键流水线与运行总览。
2. `P0-5/P0-6 评论补采诊断` 已完成，当前主要瓶颈是 `141` 条仍应继续补采；下一批应优先带 `--only-comment-expected` 跑。
3. 同时准备 `P0-1/P0-2` 高优先级账号定向重采。
4. 每轮采集后跑 `tools/run_phase1_analysis_pipeline.py` 刷新严格池。

## Review 小结

- 已有采集、下载、detail、严格池、正向因素分析的主链路；现在瓶颈不是“有没有代码”，而是“采集归因可信度”和“评论真实命中率”。
- 下一步应该少做盲目大批量采集，多做状态报告与失败分类，让每轮补采都能证明覆盖率实际改善。
- 评论失败诊断已经拆开“详情无评论”“运行时无评论”和“有评论但未取到”；下一批应优先补 `comment_expected_missing_artifact=107`，再处理 `comment_expected_noise_only=28` 和 `comment_expected_empty_response=6`。


