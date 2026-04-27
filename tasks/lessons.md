# Lessons

- Windows 下运行项目测试优先使用 `py -3.11`；`py -3` 可能指向 Python 3.9，无法支持 `dataclass(slots=True)`。
- 采集评论时不能把任意 JSON 响应里的 `text/content` 当评论；必须要求真实评论接口或作者标识。
- 严格有效池不能轻易放宽 `detail_account_not_mentioned`，它目前有效拦截了大量推荐流/无关视频。
