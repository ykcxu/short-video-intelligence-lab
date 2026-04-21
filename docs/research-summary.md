# 调研总结

本文档汇总了三个核心方向：

1. 给定主页后的视频发现、下载、互动与评论采集
2. 画面 / 人脸 / 姿态数值化与特征提取
3. Python 并行处理视频话术的方法

---

## 一、主页视频发现、下载、互动与评论采集

### 结论

- **最稳**：官方授权接口
- **次稳**：Playwright + 页面抓取 + 第三方下载器
- **不建议单押**：仅依赖 `yt-dlp`

### 推荐策略

#### 1. 优先官方授权

如果账号可授权，优先获取：

- 视频列表
- 视频基础数据
- 评论列表
- 评论回复列表

官方资料表明，部分能力支持：

- 视频详细数据
- 评论管理
- 用户授权与数据能力

#### 2. 无授权场景

无授权时建议：

- 用 `playwright` 采主页与视频页
- 抓公开可见互动数
- 抓评论分页
- 调用专用 downloader 批量下载公开视频

#### 3. 下载方案

- 主链路：专用 downloader
- 兜底：`yt-dlp`
- 下载失败：落 `video_url + metadata + retry_status`

### 结构化存储建议

建议拆表：

- `accounts`
- `videos`
- `video_snapshots`
- `comments`
- `comment_replies`
- `download_tasks`

重点：

- `video_snapshots` 用来保存播放、点赞、评论、转发的时间快照
- 评论和回复保留原始 JSON 字段，方便回放与补数

---

## 二、画面 / 人脸 / 姿态特征提取

### 推荐组合

- 画面质量：`pyiqa`、`MUSIQ`、`NIMA`
- 人脸：`InsightFace`、`DeepFace`
- 面部行为：`OpenFace 3.0`
- 姿态：`MMPose`、`MediaPipe`
- 视频工程特征：`OpenCV`、`PySceneDetect`、`ffmpeg`

### 画面质量

建议输出：

- `aesthetic_score`
- `technical_quality_score`
- `blur_score`
- `brightness_mean`
- `brightness_clip_ratio`
- `stability_score`

### 人脸

不建议只输出“颜值总分”，更推荐：

- `face_count`
- `face_quality_score`
- `frontalness`
- `head_pose`
- `smile_score`
- `emotion_probs`
- `face_embedding`
- `face_aesthetic_proxy`

### 姿态

推荐从关键点派生：

- `shoulder_slope`
- `body_center`
- `body_bbox_ratio`
- `motion_velocity`
- `motion_acceleration`
- `motion_jitter`
- `camera_facing_score`

### 镜头与视频工程特征

建议加入：

- 镜头切分
- 光流
- 全局运动估计
- 模糊度
- 构图偏移
- 曝光分布

---

## 三、Python 并行处理视频话术

### 推荐架构

- 外层：`asyncio` 调度
- CPU：`ProcessPoolExecutor`
- GPU：常驻 ASR worker

### 流程

1. 视频登记
2. `ffprobe` 元数据探测
3. `ffmpeg` 抽音频与规范化
4. VAD / 切块
5. `faster-whisper` 转写
6. 文本后处理
7. 关键词 / 主题 / 结构分析
8. 持久化

### 推荐输出

- `video_jobs`
- `transcript_segments`
- `analysis_result`

### 推荐模型与工具

- ASR：`faster-whisper`
- 媒体：`ffmpeg`
- 调度：`asyncio`
- 多进程：`concurrent.futures.ProcessPoolExecutor`

---

## 四、MVP 建议

先做一个可跑通版本：

1. 先实现单主页采集
2. 落视频元数据 + 评论快照
3. 做公开视频下载
4. 补视觉分析
5. 最后再做多进程和多 GPU 优化
