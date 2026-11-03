# 小D 的探索生活 · V0.2

AstrBot 通用网页搜索与每日自动探索插件。让现有角色「小D」根据独立兴趣状态和近期经历选题，通过 Tavily 检索公开网页摘要，或选择 RSS/Atom 摘录，写下有来源的发现和主观感想。

## 安装与使用

运行要求：AstrBot v4.29.4 本体要求 Python >=3.12。

1. 在 AstrBot 插件页使用本仓库地址安装。**仓库当前为私有仓库**，未授权的 AstrBot 服务无法直接拉取；可将仓库下载后放入 `data/plugins/astrbot_plugin_xiaod_life`，或自行使用具备仓库读取权限的安装方式。插件不保存 GitHub 凭据。
2. 安装 `requirements.txt` 中依赖，重载插件。
3. 在插件配置中设置 `tavily_api_key`（在 https://app.tavily.com 获取），选择已有模型的 `provider_id`。自动探索必须配置该 ID；只有手动探索可留空使用当前会话模型。插件不创建模型、不读取模型密钥、不更改模型配置。首次配置时可先关闭 `auto_enabled`，配置完成后再开启并重载。
4. 使用 AstrBot 管理员账号发送 `/life explore`。完成后得到发现、主观评价及原始来源链接。
5. 使用 `/life today` 查看最近三次成功探索。该命令不联网、不调用模型。这里的 today 是近期活动入口，**并非只显示当天**，时间统一为 UTC。

默认 `admin_only=true`，防止群成员随意消耗模型额度或读取角色经历。若主动关闭，所有会话共享一份小D活动记录。需要在 AstrBot 本体中正确配置管理员账号。普通 QQ 私聊/群聊是否允许命令触发仍受本体唤醒与平台配置控制。

## 配置

- `enabled`：启用指令和自动探索，默认 true。
- `search_mode`：默认 `tavily`，通用网页搜索；设为 `rss` 使用原 RSS 模式，不自动回退。
- `tavily_api_key`：Tavily 密钥，仅发送到固定的 `https://api.tavily.com/search`。请保护 AstrBot 插件配置及备份，不将密钥提交到 GitHub。
- `auto_enabled`：每日自动探索开关，默认 true。后台只保存结果和错误，不发送 QQ 消息。
- `auto_hour` / `auto_minute`：默认 15 / 0，使用明确的 `Asia/Shanghai` 时区，与服务器本地时区无关。
- `search_sources`：1–3 个公开 HTTPS RSS/Atom 地址。默认 ScienceDaily、NASA、MIT Technology Review。可以替换为自己验证可访问的公开订阅源；不支持 HTTP、带账户密码的 URL、非 443 端口或重定向。
- `provider_id`：已配置模型的提供商 ID；推荐明确选择 DeepSeek。
- `max_sources`：单次最终来源数，1–3，硬上限 3。
- `admin_only`：仅管理员使用指令，默认 true。

修改配置后重载插件。首轮探索会创建 `interests.json`，仅新安装默认兴趣为音乐、电影、文学、美食、艺术、自然、科技；已有兴趣文件原样保留。可在插件停用后编辑其中的 `interests` 字符串数组，再重载；支持 1–20 项，每项最多 100 字符。不会自动读取或推断 LivingMemory 中的兴趣。

## 实际执行流程

1. 读取独立兴趣状态及最近五条活动的主题和下次方向。
2. 通过官方 `context.llm_generate()` 让模型根据自己的兴趣选出主题和检索词，不依赖用户画像；手动未配置 ID 时才使用 `get_current_chat_provider_id()`。
3. Tavily 模式执行一次 basic/general 搜索，关闭 auto_parameters、答案生成和正文提取；从响应中保留最多三个有效来源。RSS 模式读取最多三个订阅源并按关键词匹配。没有有效来源时保存失败，不伪装成功。
4. 将抓到的摘录交给模型归纳发现、表达个人评价和下次方向。模型只能引用已给定的来源编号；来源 URL 永远来自实际搜索或 RSS 响应，不接受模型提供的 URL。
5. 保存成功或失败记录；手动模式回复当前指令，自动模式不发消息。只处理自己的 life 指令，不拦截普通聊天。

**AstrBot 搜索接口核查**：检查官方 v4.29.4（commit `61335414433db721b3f7ecc93f90deafe0a59d78`）发现 `Context` 没有稳定的后台 search API。`web_search_tools.py` 的 `TavilyWebSearchTool.call` 依赖包含 event 的 AgentContext；`_tavily_search` 是私有实现。因此 V0.2 使用独立可配置 Tavily HTTP 适配器，不伪造 QQ 事件、不调用私有函数、不读取或复用本体密钥。不支持的搜索模式不会默默降级。

**费用**：每次探索通常一次 Tavily basic 查询（当前为 1 credit）和最多两次模型调用；自动每日一次，手动调用另计。免费额度及超额收费取决于各服务账户和套餐，见 [Tavily credits](https://docs.tavily.com/documentation/api-credits)。无自动重试、无 advanced 搜索、无正文抽取；缺少密钥或配额不足会失败并记录，不编造资料。不要把私人信息放入兴趣或检索词，检索词会发送至 Tavily，兴趣与摘要会发送至所选模型服务。

## 每日调度与重启语义

- 插件 initialize 启动一个轻量 asyncio 任务，每 60 秒内检查一次，北京时间默认 15:00 后执行，无需 QQ 消息；不是精确到秒的实时调度。
- 启动时已过当天设定时间且尚未尝试，会补当天一次；不会补过去日期。启动早于设定时间则等待。调整时间后需重载，已尝试的同一天不会再跑。
- 每个北京时间日期**最多一次自动尝试**，包括成功、失败、卸载取消和崩溃。模型/网络调用前以 O_EXCL 创建 `scheduler/YYYY-MM-DD.json` 占位，重启读取该日期文件是否存在，残缺占位也不重置。故硬崩溃后可能只有占位、没有完成活动，这是为避免重复付费而采取的保守策略。
- 手动和自动共用 Explorer、同一锁、30 秒冷却及持久化逻辑。手动运行不占自动日期；自动遇到手动并发或冷却会下分钟再检查。管理员主动手动触发仍可在当天自动尝试后再探索。
- 禁用/卸载/重载时取消并等待后台及当前探索任务，再关闭 HTTP 连接；取消中的探索尽力保存 cancelled 记录。重载使用同一数据目录恢复去重。
- 缺 `provider_id` 时后台会留下失败活动并占当天尝试；配置修复后可手动测试，下一日期自动恢复。`enabled=false` 或 `auto_enabled=false` 时不触发。

**证据边界**：`sources` 是真实网络响应中的 网页搜索标题和摘要或 RSS 标题和摘录，`url` 是搜索服务或 feed 提供的原始链接；没有访问文章全文，也没有独立验证文章链接是否可用或发布者的说法是否真实。`evidence_type` 区分 `search_snippet` 与 `rss_excerpt`，不会声称读过正文；`published_at` 是服务或 feed 原始日期字符串，可能缺失；`retrieved_at` 是本次抓取完成后的 UTC 时间。`model_findings` 是模型对摘录的归纳，仍可能有误；`subjective_evaluation` 明确是生成的个人感想，不是网络事实，也不是模型真的拥有线下经历。

## 数据与兼容边界

所有数据通过官方 `StarTools.get_data_dir('astrbot_plugin_xiaod_life')` 保存到：

```text
data/plugin_data/astrbot_plugin_xiaod_life/
  interests.json
  activities.json
  scheduler/YYYY-MM-DD.json  # 每日自动尝试占位，失败/中断也保留
  archive-<id>.json  # 活动超过1000条或8 MiB时保留完整归档快照
```

新活动增加 `trigger` 与 `scheduled_day`，旧记录缺少这些字段仍可读取，不迁移或覆盖旧兴趣。活动包含 `id`、`started_at`、`finished_at`、`status`、`topic`、`sources`、`model_findings`、`subjective_evaluation`、`next_direction`、`errors`。`sources` 保留编号、URL、feed URL、标题、实际摘录和抓取时间。失败保留已抓到的内容、错误类型及时间，不删除旧记录，不将失败伪装为成功经历。

采用临时文件 + fsync + 原子替换。JSON 损坏时拒绝继续写入，绝不静默重建。单文件读取上限 16 MiB。达到 1000 条或 8 MiB 时先保存完整归档快照，再在活动文件保留最后五条并追加新记录；归档之间会重叠五条，可按 `id` 去重。归档不自动删除，应定期备份并由管理员管理磁盘空间。单进程内串行写入，不支持多个 AstrBot 进程同时共用同一个数据目录。

不读写 LivingMemory 数据库、配置或内部存储；不读取 QQ 聊天记录；不改 Proactive Chat，不调用主动消息 API，仅创建本插件每日探索任务，不注入普通聊天提示词。`llm_generate` 不传对话历史或工具，只发送本插件的兴趣、简短近期主题和公开摘录。现有服务提供商仍会收到这些输入；请勿在兴趣文件中写入敏感个人数据。

## 限制与防护

- 全局至多一次探索并发；两次开始时间至少间隔 30 秒。
- 每次最多两次模型调用，各 45 秒；探索主体总上限 130 秒。
- Tavily 每次一个 POST 请求、总限时 15 秒、最多 512 KiB 响应；RSS 模式每次最多三个 RSS 请求；每个请求 12 秒，连接 5 秒，最多 512 KiB 解压后响应体。
- 每个 feed 最多取 40 个可用条目，摘录最多 1800 字符；模型输出最多 12000 字符，并校验 JSON 及引用编号。
- 只读公开 HTTPS，不使用环境代理；仅 Tavily 使用配置的 API Key，不关闭 TLS 校验，不跟随重定向。
- 在连接解析器检查 DNS 结果，拒绝回环、局域网、链路本地、保留等非公网地址，防止 DNS 重绑定进入内网。
- defusedxml 拒绝实体扩展；外部内容当数据处理。提示词防注入不是绝对保证，因此模型无工具、无法写文件或选择联网地址，输出 URL/CQ 码也被拒绝。
- 模型/网络错误只存错误类型，避免异常字符串泄漏供应商密钥。存储失败会明确告知“无法确认日志已保存”。
- 输出按行分段，避免切断来源 URL；平台自身的消息限额和 URL 风控仍需 QQ 实测。

## 测试

在插件根目录运行：

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q .
python -m pip install ruff
python -m ruff check .
python -m ruff format --check .
```

可执行测试覆盖核心流程、失败保留历史、非法模型JSON、伪造引用、来源上限、损坏文件保护、并发/冷却/取消、RSS/Atom解析、XML实体、URL与DNS限制。具体本轮执行结果见 `TEST_REPORT.md`。

## 必须在真实 AstrBot 中验收

本项目按 **AstrBot v4.29.4** 官方源码 API 核对，未在用户真实 AstrBot/QQ/DeepSeek 环境加载运行，不声称已完成端到端验收。

- 安装、依赖解析、插件配置显示、重载与卸载。
- 绑定 DeepSeek ID 后成功执行两次官方模型调用，验证输出 JSON 格式。
- 使用真实 Tavily Key 验证检索及账户额度（本轮未调用付费 API）；RSS 模式在部署网络检查三个 RSS 地址是否允许访问；网络策略、地区限制、源重定向或限流都可能导致失败。必要时换成可访问的 HTTPS RSS。
- QQ 私聊及群内管理员/非管理员权限、指令前缀、消息分段和 URL 风控。
- 执行 explore → today → 重载 → today，确认真实历史持久化。
- 设置临近的北京时间，在无 QQ 消息情况下观察后台活动、同日重载不重复、关闭开关后无调用；实际模型响应和 API 额度仍需服务器验收。
- 并发指令、模型超时、断网、不可写目录和取消卸载，不丢旧日志。
- 与 LivingMemory、Proactive Chat 同时启用，确认原有聊天/记忆/主动消息行为保持不变。代码不存在直接耦合，但实际插件事件优先级仍需共存实测。

## 官方依据

- [插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [v4.29.4 Context API](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/context.py)
- [v4.29.4 网页搜索工具源码](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/tools/web_search_tools.py)
- [Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [v4.29.4 插件生命周期](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_manager.py)
- [v4.29.4 StarTools](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_tools.py)
- [v4.29.4 命令过滤器](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/filter/command.py)

指定的 `https://github.com/AstrBotDevs/helloworld` 返回 404。通过目标仓库的模板来源确认实际模板为 [Soulter/helloworld](https://github.com/Soulter/helloworld)，已读取其入口与元数据，并保留原有 LICENSE、.gitignore。入口与生命周期最终以 v4.29.4 官方源码为准，未沿用模板已废弃的 register 装饰器。

本次 V0.2 修改日期为 2026-11-03。沿用仓库现有 AGPL-3.0 许可证，见 LICENSE。

V0.2 不包含兴趣自动演化、视频理解、普通聊天记忆注入或主动分享。针对 2 CPU / 2 GB 使用单任务、无浏览器/本地模型、有限响应体设计；未在该规格真实服务器做性能压测。
