# V0.1 验证记录

执行日期：2026-11-03（UTC）。执行环境：云端 Python 3.12，未操作用户电脑。

## 已执行

- `python -m unittest discover -s tests -v`：**41 项测试通过**（另外含 URL 安全 subtests）。
- `python -m ruff check .`：通过。
- `python -m ruff format --check .`：通过。
- `python -m compileall -q .`：通过。
- `_conf_schema.json` 可解析为 JSON，配置字段与实现一致。
- 对照 AstrBot v4.29.4 官方源码核对 Context、Star、StarTools、命令组与配置 API。

离线测试使用模型/HTTP/AstrBot API 测试替身，并非真实 QQ、DeepSeek 或完整 AstrBot 启动测试。测试数据为临时文件，不会当成小D的真实经历发版。

覆盖成功闭环、错误日志保留、无匹配、非法 JSON、伪造来源编号、来源上限、损坏日志保护、并发拒绝、冷却、取消保存、敏感错误信息不泄漏、RSS/Atom、HTML清理、XML实体防护、私网URL与DNS、请求重定向/响应字节限制、默认权限、provider调用参数、卸载取消、today不调用模型、归档保留、持久化线程取消安全、消息分段与生成URL/CQ拒绝。

## 实际外网冒烟

通过插件真实 `RSSClient.fetch` 尝试下列三个默认源：

- `https://www.sciencedaily.com/rss/all.xml`
- `https://www.nasa.gov/feed/`
- `https://www.technologyreview.com/feed/`

三者在当前云执行环境均报 `ClientConnectorDNSError`，底层为 `Temporary failure in name resolution`。**本轮没有成功取得真实 RSS 响应**。这不能用于判断部署环境中这些 RSS 可用或不可用，也不能算真实网络探索闭环已验收。没有通过关闭TLS、放开内网或代理绕过该限制。

## 尚未验收

1. 真正安装到 AstrBot v4.29.4 后的模块加载、配置页与命令匹配。
2. 用户已配置 DeepSeek 的真实调用、结构化输出稳定性、费用及响应时间。
3. 部署网络中的 RSS 连通性与真实内容匹配。
4. QQ 私聊/群聊的管理员身份、命令唤醒、平台消息分段/URL风控。
5. 同时启用 LivingMemory 与 Proactive Chat 时的端到端共存行为。
6. AstrBot 重载/卸载/进程重启情况下的真实持久化验收。

GitHub Actions 工作流已提供；远端运行结果以仓库实际 Checks 为准，本文件不预先宣称 CI 成功。

## 指令回复顺序回归

对照 v4.29.4 的 `pipeline/scheduler.py`、`process_stage/stage.py` 和 `astr_message_event.py`，修复了在 yield 之前 stop_event 导致后续分段中断、清理延迟的风险。当前所有回应先完成 yield，最后再停止事件。新增七项逐次 yield 状态测试，覆盖拒绝、停用、未就绪、探索多段/异常、摘要多段/异常。它们验证适配器边界，不代替完整 QQ 流水线验收。

另执行独立探针，从 AstrBot v4.29.4 官方原文件提取未改写的 `_process_stages` 与 `call_handler` 函数 AST，运行当前插件真实 explore 生成器（外围模型/事件使用替身）。旧顺序得到 delivered_parts=1、running_tasks=1；修复顺序得到 delivered_parts=3、running_tasks=0，三段全文、立即清理和停止状态断言全部通过。该探针是上游调度函数契约验证，仍不是完整 AstrBot/QQ 实机验收。
