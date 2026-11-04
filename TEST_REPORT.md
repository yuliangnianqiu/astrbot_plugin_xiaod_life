# V0.4 测试报告

执行日期：2026-11-04。基线 `da03067`（V0.3），本地 Python 3.13.9 / Windows。外部模型、搜索与 QQ 在自动化测试中使用离线替身。

## V0.4 执行结果

- `python -m unittest discover -s tests -q`：137 项通过，含原有 112 项和新增 25 项。
- `python -m ruff check .`：通过。
- `python -m ruff format --check .`：18 个 Python 文件通过。
- `python -m compileall -q .`、配置 JSON 解析及 `git diff --check`：通过。
- 另从官方 v4.29.4 tag 读取 `astrbot/core/agent/message.py`，在本地加载真实 `TextPart` / `Message`，核验 `mark_as_temp()` 的 `_no_save` 标记及 `dump_messages_with_checkpoints()` 对临时资料的过滤：通过。该检查使用官方源文件与本地 Pydantic，独立于适配器替身测试。

## 新增覆盖

1. 今天、昨天按北京时间筛选，覆盖 UTC 16:00 跨日边界；七天窗口、未来时间和失败/跳过/取消/进行中记录过滤。
2. 常用生活问句、主题片段及来源追问召回；追问使用当前请求上下文匹配相关主题；无关聊天保持请求原状。
3. 原始 URL、逐来源阅读深度与主观评价保留；正文和候选池留在日志中；V0.1/V0.2 来源按摘要处理。
4. 最近三条按时间排序、6000 字符硬预算、长链接完整、JSON 记录完整、发现引用与附带来源一致，读取前后日志一致。
5. QQ 管理员私聊默认范围、白名单精确会话匹配、平台实例及用户隔离、群聊和其他平台隔离；生活指令与普通聊天采用同一记录访问范围。
6. 关闭开关、卸载中和 life 指令跳过召回；原有请求字段及其他插件资料保留；相同请求重复钩子调用只追加一段临时内容。
7. JSON 损坏及无时区/非法时间戳直接报告错误、文件保留；查询时间与重载后旧历史读取；查询消息分段在发送完成后终止事件。

## 接口与真实环境验收

接口按 AstrBot v4.29.4 核验：`ProviderRequest.extra_user_content_parts`、`TextPart.mark_as_temp()`、`on_llm_request`、`AstrMessageEvent.is_private_chat()`、`get_platform_name()` 与精确 `unified_msg_origin`。钩子默认优先级为 0，框架按优先级从高到低执行；本插件只追加一段临时资料。调用钩子时的异常交由 AstrBot 官方错误日志报告。

- [ProviderRequest](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/provider/entities.py)
- [临时内容与序列化](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/agent/message.py)
- [事件注册](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/register/star_handler.py)
- [钩子排序](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_handler.py)
- [事件调用与错误日志](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/pipeline/context_utils.py)

本轮执行范围为本地逻辑、接口契约及官方序列化验证。服务器实际版本、DeepSeek 回答、QQ 链接显示与 LivingMemory / Proactive Chat 共存需按 README 的 V0.4 五步清单在用户部署环境验收。GitHub Actions 使用仓库既有的 Python 3.12 / 3.13 矩阵执行上述测试与静态检查，结果可在提交对应的 Actions 中查看。

---

# V0.3 历史测试报告

执行日期：2026-11-03。基线 V0.2 commit `ecfcf61`。Python 3.12，测试依赖隔离安装到临时目录。未使用用户 API Key，未调用真实 Tavily / DeepSeek / QQ 服务，未宣称完成真实部署验收。

## 执行结果

- `python -m unittest discover -s tests -q`：112 tests 全部通过。
- `python -m pytest -q`：112 passed，24 subtests passed。宿主 anyio pytest 插件产生 1 条 pytest CallSpec 弃用警告，非本插件运行错误。
- `python -m ruff check .`：通过。
- `python -m ruff format --check .`：18 Python 文件通过。
- `python -m compileall -q .`：通过。
- `_conf_schema.json` JSON 解析与 `git diff --check`：通过。
- 原 78 项 V0.1/V0.2 测试全部保留，本轮新增 34 项方法测试与 5 个预算上限 subtests。

测试命令使用 `PYTHONPATH=/tmp/life-review-deps:.` 加载临时测试依赖；生产依赖仍为 requirements.txt，不引入数据库、浏览器或本地模型。

## V0.3 新增覆盖

1. 模型自主 discovery / active_search 切换；发现候选交错混合 RSS 与两组不同查询、去重，RSS 不要求旧兴趣命中。
2. 模型只选候选 ID，拒绝超范围、重复、布尔 ID；零选择保存 skipped，不伪造阅读；最多两条选中材料。
3. 平台识别与小红书、B站、知乎域名过滤；过滤恶意相似后缀与不安全 URL；原链接保留。
4. Tavily Extract 固定 endpoint、basic/text、有界 timeout、同一 Bearer Key、无重定向；批量最多两页；正文长度及512KiB响应限制。
5. 正文提取成功与部分失败、空正文、异常回退；结果 URL 必须与请求完全匹配，非选中页面不能成为正文；mixed/summary/full_text 日志区分。
6. 私网字面量 URL 不上传 Extract；模型与网络错误不保存秘密异常；取消向上传播；无隐式重试。
7. 脱敏必须在完整文本清理/HTML实体归一化之后、最终截断之前，避免密钥前缀边界泄漏。
8. 2次搜索、2网页、3次模型默认预算与配置硬顶；失败计入尝试；低于完整模型预算会停止，不先做无法阅读的额外提取；关闭Extract仍可摘要阅读。
9. 每日V0.3记录持久化、重建Explorer/Store后日期去重；原定时、同日失败不重试、卸载、并发、冷却、原子写和旧记录测试继续通过。
10. AstrBot适配器生产路径确实启用V0.3；非法预算或域名配置在HTTP session启动前拒绝。与LivingMemory/Proactive Chat的隔离、空contexts、tools=None、后台无QQ事件及无主动消息测试继续通过。

## 独立审查与修复

独立审查新增 `tests/test_v03_security.py` 15项测试，发现并修复两处问题：

- 原“截断后脱敏”可能留下密钥前缀，改为有界响应内完整归一化、脱敏再截断，含HTML实体测试。
- 初始化预算校验原先晚于session创建，改为先校验/构造引擎再连接，连接初始化失败或取消时关闭资源。

最终独立审查没有剩余阻断项。保留旧Explorer构造接口供旧调用方使用；生产适配器始终显式注入 discovery/extract 进入V0.3三阶段流程。

## 官方接口依据与边界

- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [Tavily credits](https://docs.tavily.com/documentation/api-credits)
- AstrBot沿用V0.2已核对的v4.29.4公开 `Context.llm_generate`、`StarTools.get_data_dir`、initialize/terminate生命周期，不引入新的私有接口调用。

离线测试验证请求契约和逻辑，不证明第三方当前可访问某一页面。Extract抓取由Tavily执行，本地DNS防护不覆盖其远端DNS和跳转链。仅精确返回URL被认定为所选正文；canonical URL变化会保守降级。full_text指实际提取且可能截断的正文，不代表整页、图片、视频或原文事实独立核验。

`usage`是本插件预留尝试次数，不是供应商实际计费。LLM预算约束本插件调用官方方法次数，供应商内部重试不在本插件控制内。

## 真实部署仍需验收

用户2 CPU / 2 GB服务器、AstrBot/QQ/DeepSeek实例未在本轮运行，无性能压测或真实端到端通过声明。按README末尾清单完成：真实Key与provider配置、发现模式候选多样性、至少一篇公开网页实际full_text、内容概括有据、API配额、平台摘要降级、QQ命令与权限、重载持久化、无QQ消息自动触发及同日去重、卸载清理，以及LivingMemory/Proactive Chat共存。
