# V0.2 测试报告

执行日期：2026-11-03。基线为 V0.1 修正版 commit `7fca296fc8075480f5b6eaf743b0c06bbf77e486`。测试环境 Python 3.12，使用临时隔离目录安装测试依赖；未使用用户 API Key，未访问真实 QQ/模型服务或真实 Tavily 账户。

## 执行结果

- `python -m unittest discover -s tests -v`：78 tests，全部通过。
- `python -m pytest -q`：78 passed，19 subtests passed。环境自带 anyio pytest 插件产生 1 条 pytest CallSpec 弃用警告，不是插件运行警告。
- `python -m ruff check .`：通过。
- `python -m ruff format --check .`：15 Python 文件均已格式化。
- `python -m compileall -q .`：通过。
- `_conf_schema.json` 经过 JSON 解析验证。保留原 41 条 V0.1 测试，新增 37 条独立测试方法；参数化兴趣/URL等场景另以 subtest 执行。

## 新增覆盖

1. Tavily 成功响应解析，真实原始 URL/标题/摘要/检索时间；最多 3 个来源、去重、空摘要与不安全 URL 过滤。
2. 固定 endpoint、basic/general 参数、禁用自动参数/答案/正文、Bearer 认证、禁止重定向；响应大小限制、超时、非法 JSON、HTTP 失败、缺 Key、异常脱敏、取消传播、显式 RSS 模式。
3. 音乐、电影、文学、美食、艺术、自然、科技各自进入检索流程；选题输入包含旧日志下一方向；已有兴趣文件额外字段保留。
4. 北京时间 15:00 边界、UTC 转换、过点启动补当天、不补昨日、禁用开关、配置时分、拒绝无时区时钟。
5. O_EXCL 同日持久占位，20 线程竞争仅一个成功；新 Store/Explorer 重建去重；失败/取消后同日不重试；残缺 claim 不覆盖；手动与自动独立日期额度但共享引擎。
6. 后台显式 provider_id、无 QQ 事件、无需 current-chat resolver；手动仍可回退会话模型；上下文为空、tools=None，无主动消息接口。
7. initialize/terminate 取消与资源关闭、并发 initialize 只启动一个任务、初始化与 terminate 竞争、关闭后不重新启动；busy/cooldown 后继续检查、当日完成后停止重复调用。
8. 旧日志稀疏字段兼容、旧文件字节保护、已有并发/冷却/取消/损坏存储/原子写入/RSS/XML/URL/DNS/模型引用安全及 yield 传播测试全部保留。

## 独立审核

独立审核新增 `test_adapter_review.py` 与 `test_scheduler_review.py`。审核发现并发 initialize 可能遗失后台任务，已增加生命周期锁，使初始化与终止串行化；确定性复现测试修复后通过。未发现遗留阻断问题。

## 官方源码核查

核查 AstrBot v4.29.4，commit `61335414433db721b3f7ecc93f90deafe0a59d78`：

- [Context.llm_generate](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/context.py#L184-L231)：后台使用 keyword `chat_provider_id`，不依赖 event；手动使用 `get_current_chat_provider_id(umo=...)`。
- [网页搜索工具](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/tools/web_search_tools.py)：聊天工具依赖 AgentContext/event，私有 `_tavily_search` 不作为稳定插件接口。实现独立配置的 Tavily HTTP 适配器，无私有调用。
- [生命周期](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_manager.py#L1932-L1966)：直接实现 initialize/terminate，不定义可能抢先触发的自定义 __del__。
- [Tavily API](https://docs.tavily.com/documentation/api-reference/endpoint/search)：请求与响应契约以官方文档核查；离线 HTTP double 验证，不等于真实账户网络验收。

## 未完成的真实部署验收

未在用户的 2 CPU / 2 GB 服务器、AstrBot/QQ/DeepSeek 实例安装运行，也未调用付费搜索。因此不能宣称已完成真实端到端、内存峰值或性能验收。仍需验证插件安装/配置 UI、provider ID、真实 API Key 与额度、部署网络、QQ 指令结果、定时时刻无人发消息触发、同日重载去重、卸载无遗留任务，以及与 LivingMemory/Proactive Chat 共存。代码不读写这些插件，不做普通聊天注入、不主动分享。

硬崩溃时可能仅保留每日占位而无完成活动；这是“每日最多一次尝试”的明确设计，不承诺 exactly-once 成功。多个 AstrBot 进程不应共用活动目录；日占位跨进程互斥不等于活动文件支持多进程写入。
