# V0.3 测试报告

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
