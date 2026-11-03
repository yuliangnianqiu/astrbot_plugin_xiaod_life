# 小D 的探索生活 · V0.3

AstrBot 通用网页搜索与每日自动探索插件。让现有角色「小D」根据独立兴趣状态和近期经历选题，自主决定主动搜索或发现式浏览，从 Tavily 与公开 RSS/Atom 候选中挑选内容，通过 Tavily Extract 阅读公开正文或明确标注摘要降级，写下有依据的发现和主观感想。

## 安装与使用

运行要求：AstrBot v4.29.4 本体要求 Python >=3.12。

1. 在 AstrBot 插件页使用本仓库地址安装。原仓库地址目前重定向到 `https://github.com/yuliangnianqiu/astrbot_plugin_xiaod_life`，发布时仓库为公开状态；插件不会调整仓库可见性。也可下载后放入 `data/plugins/astrbot_plugin_xiaod_life`。插件不保存 GitHub 凭据。
2. 安装 `requirements.txt` 中依赖，重载插件。
3. 在插件配置中设置 `tavily_api_key`（在 https://app.tavily.com 获取），选择已有模型的 `provider_id`。自动探索必须配置该 ID；只有手动探索可留空使用当前会话模型。插件不创建模型、不读取模型密钥、不更改模型配置。首次配置时可先关闭 `auto_enabled`，配置完成后再开启并重载。
4. 使用 AstrBot 管理员账号发送 `/life explore`。完成后得到发现、主观评价及原始来源链接。
5. 使用 `/life today` 查看最近三次成功探索。该命令不联网、不调用模型。这里的 today 是近期活动入口，**并非只显示当天**，时间统一为 UTC。

默认 `admin_only=true`，防止群成员随意消耗模型额度或读取角色经历。若主动关闭，所有会话共享一份小D活动记录。需要在 AstrBot 本体中正确配置管理员账号。普通 QQ 私聊/群聊是否允许命令触发仍受本体唤醒与平台配置控制。

## 配置

- `enabled`：启用指令和自动探索，默认 true。
- `search_mode`：默认 `tavily`，通用网页搜索；设为 `rss` 使用原 RSS 模式，不自动回退。
- `tavily_api_key`：Tavily 密钥，仅发送到固定的 `https://api.tavily.com/search` 与 `https://api.tavily.com/extract`。请保护 AstrBot 插件配置及备份，不将密钥提交到 GitHub。
- `auto_enabled`：每日自动探索开关，默认 true。后台只保存结果和错误，不发送 QQ 消息。
- `auto_hour` / `auto_minute`：默认 15 / 0，使用明确的 `Asia/Shanghai` 时区，与服务器本地时区无关。
- `search_sources`：1–3 个公开 HTTPS RSS/Atom 地址。默认 ScienceDaily、NASA、MIT Technology Review。可以替换为自己验证可访问的公开订阅源；不支持 HTTP、带账户密码的 URL、非 443 端口或重定向。
- `provider_id`：已配置模型的提供商 ID；推荐明确选择 DeepSeek。
- `max_sources`：保留旧版配置兼容（1–3）；V0.3 候选数由 `max_candidates` 控制，实际阅读始终最多 2 篇。
- `max_search_calls`：1–2，默认 2；失败请求也消耗预算，不重试。
- `max_extract_pages`：0–2，默认 2；0 表示只阅读摘要。若设 1，第二个已选来源只读摘要。
- `max_llm_calls`：1–3，默认 3。完整流程需要 3 次，调低是严格费用熔断，会保存预算不足失败记录，不会超额调用或先做无法阅读的提取。
- `max_candidates`：2–12，默认 8；不同搜索和 RSS 结果交错取样，URL 去重。
- `max_content_chars`：500–12000，默认 6000，每页清理后正文上限。日志保存实际送给模型的有界文本。
- `include_domains`：列表，默认空即全网。支持 `xiaohongshu.com`、`bilibili.com`、`zhihu.com`，可组合；限制只作用于 Tavily Search，不限制独立 RSS 候选。请求和返回结果都会校验域名。
- `admin_only`：仅管理员使用指令，默认 true。

修改配置后重载插件。首轮探索会创建 `interests.json`，仅新安装默认兴趣为音乐、电影、文学、美食、艺术、自然、科技；已有兴趣文件原样保留。可在插件停用后编辑其中的 `interests` 字符串数组，再重载；支持 1–20 项，每项最多 100 字符。不会自动读取或推断 LivingMemory 中的兴趣。

## 实际执行流程

1. 读取独立兴趣与最近五条活动。第一轮模型自主选择 `active_search` 或 `discovery`，保留新鲜感，允许与用户兴趣不同。
2. 主动模式根据模型检索词进行一次 Tavily 搜索。发现模式最多两组不同领域搜索，覆盖音乐、电影、文学、美食、艺术、生活方式、游戏、自然等方向，并混合最多三个 RSS/Atom 的候选。RSS 候选不要求命中旧兴趣，按订阅源轮流取样。默认 RSS 偏科学；生活领域拓展主要来自两组模型检索，可配置更多样的公开订阅源。领域覆盖受真实来源和模型选择影响，不保证单轮涵盖全部领域。
3. 第二轮模型看到候选标题、来源、摘要与最近经历，仅以已有整数 ID 自主选择最多两条，并记录具体理由。也可选零条，保存 `skipped`，不强迫形成兴趣。
4. 对已选来源进行一次 Tavily Extract basic 批量请求，最多两页。清理并截断实际返回正文；无密钥、提取失败、空正文或 URL 不精确匹配时保留摘要，不换第三页、不自动重试。只接受本轮候选 URL，不使用模型生成 URL。
5. 第三轮模型只依据实际正文或明确标注的摘要，形成引用发现、阅读概括、主观评价和下一步方向。`full_text` 表示提取正文（可能截断），不表示完整浏览页面，更不表示观看图片或视频。
6. 每轮保存一条完整成功、跳过、失败或取消记录；自动运行不发 QQ 消息。`/life explore` 手动与自动共用模型决策，不强制固定模式。`search_mode=rss` 时发现候选只来自 RSS，不发送 Tavily Search；配置 Tavily Key 后仍可做 Extract。

**AstrBot 搜索接口核查**：检查官方 v4.29.4（commit `61335414433db721b3f7ecc93f90deafe0a59d78`）发现 `Context` 没有稳定的后台 search API。`web_search_tools.py` 的 `TavilyWebSearchTool.call` 依赖包含 event 的 AgentContext；`_tavily_search` 是私有实现。因此 V0.2 使用独立可配置 Tavily HTTP 适配器，不伪造 QQ 事件、不调用私有函数、不读取或复用本体密钥。不支持的搜索模式不会默默降级。

**费用**：默认每轮最多 2 次 Tavily basic Search、最多提取 2 网页、最多 3 次模型请求。无自动重试，不使用 advanced。基本搜索每次 1 credit；基本正文提取按成功 URL 计费，当前每累计 5 次成功提取为 1 credit，具体免费额度与套餐见 [Tavily credits](https://docs.tavily.com/documentation/api-credits)。模型费用由已配置服务决定；失败的网络请求也占本插件尝试预算。模型预算限制本插件调用 `llm_generate` 的次数，AstrBot/模型提供商内部可能执行的重试不由本插件控制，请同时检查提供商设置。日志 `usage` 是本插件预留/尝试计数，不是供应商账单或精确 credit 消耗。不要在兴趣中放入私人信息：检索词与选中 URL 发送至 Tavily，兴趣、近期主题、候选与实际阅读文本发送至模型服务。

## 每日调度与重启语义

- 插件 initialize 启动一个轻量 asyncio 任务，每 60 秒内检查一次，北京时间默认 15:00 后执行，无需 QQ 消息；不是精确到秒的实时调度。
- 启动时已过当天设定时间且尚未尝试，会补当天一次；不会补过去日期。启动早于设定时间则等待。调整时间后需重载，已尝试的同一天不会再跑。
- 每个北京时间日期**最多一次自动尝试**，包括成功、失败、卸载取消和崩溃。模型/网络调用前以 O_EXCL 创建 `scheduler/YYYY-MM-DD.json` 占位，重启读取该日期文件是否存在，残缺占位也不重置。故硬崩溃后可能只有占位、没有完成活动，这是为避免重复付费而采取的保守策略。
- 手动和自动共用 Explorer、同一锁、30 秒冷却及持久化逻辑。手动运行不占自动日期；自动遇到手动并发或冷却会下分钟再检查。管理员主动手动触发仍可在当天自动尝试后再探索。
- 禁用/卸载/重载时取消并等待后台及当前探索任务，再关闭 HTTP 连接；取消中的探索尽力保存 cancelled 记录。重载使用同一数据目录恢复去重。
- 缺 `provider_id` 时后台会留下失败活动并占当天尝试；配置修复后可手动测试，下一日期自动恢复。`enabled=false` 或 `auto_enabled=false` 时不触发。

**证据边界**：`candidates` 是发现到的候选，不代表文章已读；`sources` 是实际交给第三轮阅读模型的选中材料。逐来源记录 `source_platform`、`selection_reason`、`read_level`（`summary` 或 `full_text`）、`read_content` 和来源 URL。未提取成功不能标为正文，混合阅读的记录级 `read_level` 为 `mixed`。`read_content_summary` 和 `model_findings` 是模型生成的归纳，仍可能有误，不独立证明发布者说法真实。原始搜索证据类型保留为 `search_snippet` / `rss_excerpt`，正文另有 `extracted_at`。模型生成个人感想不代表真实线下经历。

小红书、B站、知乎只支持公开已索引内容。不登录、不收集个人登录凭据、不运行浏览器、不绕过付费墙或平台限制。正文获取失败就标记摘要。完整小红书笔记、推荐信息流、图片与视频理解均不在本版范围内。

## 数据与兼容边界

所有数据通过官方 `StarTools.get_data_dir('astrbot_plugin_xiaod_life')` 保存到：

```text
data/plugin_data/astrbot_plugin_xiaod_life/
  interests.json
  activities.json
  scheduler/YYYY-MM-DD.json  # 每日自动尝试占位，失败/中断也保留
  archive-<id>.json  # 活动超过1000条或8 MiB时保留完整归档快照
```

V0.3 新增 `schema_version=3`、`exploration_mode`、`candidates`、`selection_reason`、`read_level`、`read_content_summary` 和 `usage`。已有 `trigger` / `scheduled_day` 保留；旧记录缺字段仍可读取，显示阅读深度时默认按摘要，不迁移或覆盖旧兴趣。活动包含 `id`、`started_at`、`finished_at`、`status`、`topic`、`sources`、`model_findings`、`subjective_evaluation`、`next_direction`、`errors`。`sources` 保留编号、URL、feed URL、标题、实际摘录和抓取时间。失败保留已抓到的内容、错误类型及时间，不删除旧记录，不将失败伪装为成功经历。

采用临时文件 + fsync + 原子替换。JSON 损坏时拒绝继续写入，绝不静默重建。单文件读取上限 16 MiB。达到 1000 条或 8 MiB 时先保存完整归档快照，再在活动文件保留最后五条并追加新记录；归档之间会重叠五条，可按 `id` 去重。归档不自动删除，应定期备份并由管理员管理磁盘空间。单进程内串行写入，不支持多个 AstrBot 进程同时共用同一个数据目录。

不读写 LivingMemory 数据库、配置或内部存储；不读取 QQ 聊天记录；不改 Proactive Chat，不调用主动消息 API，仅创建本插件每日探索任务，不注入普通聊天提示词。`llm_generate` 不传对话历史或工具，只发送本插件的兴趣、简短近期主题和公开候选/阅读材料。现有服务提供商仍会收到这些输入；请勿在兴趣文件中写入敏感个人数据。

## 限制与防护

- 全局至多一次探索并发；两次开始时间至少间隔 30 秒。
- 每次最多三次模型调用，各 45 秒；探索主体总上限 200 秒。
- Tavily 最多两次 Search POST 和一次 Extract 批量 POST，各总限时 15 秒、最多 512 KiB 响应；发现模式最多三个 RSS 请求；每个请求 12 秒，连接 5 秒，最多 512 KiB 解压后响应体。
- 每个 feed 最多取 40 个可用条目，摘录最多 1800 字符；模型输出最多 12000 字符，并校验 JSON 及引用编号。
- 只读公开 HTTPS，不使用环境代理；仅 Tavily 使用配置的 API Key，不关闭 TLS 校验，不跟随重定向。
- 本地 HTTP 连接解析器检查 DNS 结果，拒绝回环、局域网、链路本地和保留地址；Extract 的页面抓取由 Tavily 执行，其 DNS 与内部重定向不受本地解析器控制，不能宣称本插件验证了远端完整访问链。传给 Extract 的 URL 只来自选中候选且经公网 HTTPS 形式校验，返回正文仅接受原 URL 精确匹配。
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
- 绑定 DeepSeek ID 后执行三阶段官方模型调用，验证规划、候选选择和阅读 JSON 格式。
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
- [Tavily Extract API](https://docs.tavily.com/documentation/api-reference/endpoint/extract)
- [v4.29.4 插件生命周期](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_manager.py)
- [v4.29.4 StarTools](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_tools.py)
- [v4.29.4 命令过滤器](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/filter/command.py)

指定的 `https://github.com/AstrBotDevs/helloworld` 返回 404。通过目标仓库的模板来源确认实际模板为 [Soulter/helloworld](https://github.com/Soulter/helloworld)，已读取其入口与元数据，并保留原有 LICENSE、.gitignore。入口与生命周期最终以 v4.29.4 官方源码为准，未沿用模板已废弃的 register 装饰器。

本次 V0.3 修改日期为 2026-11-03。沿用仓库现有 AGPL-3.0 许可证，见 LICENSE。

V0.3 不包含兴趣自动演化、视频理解、普通聊天记忆注入或主动分享。针对 2 CPU / 2 GB 使用单任务、无浏览器/本地模型、有限响应体设计；未在该规格真实服务器做性能压测。

### V0.3 部署验收清单

1. 首次升级先备份插件独立数据目录，关闭 `auto_enabled`，配置原有 DeepSeek 的 `provider_id` 与 Tavily Key，其余预算保持默认。
2. 管理员发送 `/life explore`，检查 `activities.json` 中 `exploration_mode`。模型决定模式，因此一次手动执行不保证 discovery；需要时隔至少30秒再运行，注意额外费用，不修改/删除当天自动占位来强制重试。
3. 对一条 discovery 成功记录核对 `candidates` 中不同搜索/RSS来源、最多两条 `selection_reason`，并确认 `sources` 只包含选中材料。
4. 至少一个来源实际 `read_level=full_text` 且 `read_content` 为真实公开正文，并核对原链接、`extracted_at`、发现和阅读概括有据可查。若全是 summary，不算完成正文验收；先换可公开提取的主题或来源再测，平台拒绝访问时不绕过限制。
5. 可暂设 `max_extract_pages=0` 重载，确认摘要标记；再恢复 2。用域名列表分别测试三个平台公开搜索，不能保证所有平台页面都能提取。
6. 确认 `usage.search_calls<=2`、`extract_pages<=2`、`llm_calls<=3`，每轮仅增加一条日志。`usage` 为尝试上限，供预算核对，不作为收费凭证。
7. 执行 `/life today`，重载后再次查询，历史应保留。开启自动探索并设置临近北京时间，无 QQ 消息时确认运行，同日重载不能重复。
8. 与 LivingMemory、Proactive Chat 同时启用测试普通聊天、长期记忆与主动消息，确认仍由各自插件负责。以上均为需用户真实部署完成的验收，不包含在离线测试通过结论中。
