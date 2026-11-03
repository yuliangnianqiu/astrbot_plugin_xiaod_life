# 小D 的探索生活 · V0.1

AstrBot 手动探索插件。让现有角色「小D」根据独立兴趣状态和近期经历选题，读取公开 RSS/Atom 摘录，写下有来源的发现和主观感想。

## 安装与使用

运行要求：AstrBot v4.29.4 本体要求 Python >=3.12。

1. 在 AstrBot 插件页使用本仓库地址安装。**仓库当前为私有仓库**，未授权的 AstrBot 服务无法直接拉取；可将仓库下载后放入 `data/plugins/astrbot_plugin_xiaod_life`，或自行使用具备仓库读取权限的安装方式。插件不保存 GitHub 凭据。
2. 安装 `requirements.txt` 中依赖，重载插件。
3. 在插件配置中选中已经配置好的 **DeepSeek 提供商 ID**。留空会使用当前会话模型，不能保证就是 DeepSeek；插件不会创建模型、读取密钥或更改现有模型配置。
4. 使用 AstrBot 管理员账号发送 `/life explore`。完成后得到发现、主观评价及原始来源链接。
5. 使用 `/life today` 查看最近三次成功探索。该命令不联网、不调用模型。这里的 today 是近期活动入口，**并非只显示当天**，时间统一为 UTC。

默认 `admin_only=true`，防止群成员随意消耗模型额度或读取角色经历。若主动关闭，所有会话共享一份小D活动记录。需要在 AstrBot 本体中正确配置管理员账号。普通 QQ 私聊/群聊是否允许命令触发仍受本体唤醒与平台配置控制。

## 配置

- `enabled`：启用插件指令，默认 true。
- `search_sources`：1–3 个公开 HTTPS RSS/Atom 地址。默认 ScienceDaily、NASA、MIT Technology Review。可以替换为自己验证可访问的公开订阅源；不支持 HTTP、带账户密码的 URL、非 443 端口或重定向。
- `provider_id`：已配置模型的提供商 ID；推荐明确选择 DeepSeek。
- `max_sources`：单次最终来源数，1–3，硬上限 3。
- `admin_only`：仅管理员使用指令，默认 true。

修改配置后重载插件。首轮探索会创建 `interests.json`，默认兴趣为科学与自然、太空探索、技术与日常生活。可在插件停用后编辑其中的 `interests` 字符串数组，再重载；支持 1–20 项，每项最多 100 字符。不会自动读取或推断 LivingMemory 中的兴趣。

## 实际执行流程

1. 读取独立兴趣状态及最近五条活动的主题和下次方向。
2. 通过官方 `context.get_current_chat_provider_id()` / `context.llm_generate()` 让模型选出主题和中英文检索词。
3. 异步读取最多三个已配置订阅源，对当前 feed 中的标题/摘录做关键词匹配、去重和排序，只选至多三个相关条目。没有匹配时记录失败，不伪装成功。
4. 将抓到的摘录交给模型归纳发现、表达个人评价和下次方向。模型只能引用已给定的来源编号；来源 URL 永远来自 RSS，不接受模型提供的 URL。
5. 保存成功或失败记录，再回复当前指令。只处理自己的 life 指令，不拦截普通聊天。

**V0.1 的检索是 RSS 当前条目检索，不是全网搜索。** 没有发现用户实例中可确认可调用的既有搜索接口，因此先用 RSS 完成闭环，不猜测内部搜索工具名称、不注册任意工具执行器。未来可以在 `RSSClient.search` 接口后接入经确认的搜索服务。

**证据边界**：`sources` 是真实网络响应中的 RSS 标题和摘录，`url` 是该 feed 提供的文章链接；没有访问文章全文，也没有独立验证文章链接是否可用或发布者的说法是否真实。`published_at` 是 feed 原始日期字符串，可能缺失；`retrieved_at` 是本次抓取完成后的 UTC 时间。`model_findings` 是模型对摘录的归纳，仍可能有误；`subjective_evaluation` 明确是生成的个人感想，不是网络事实，也不是模型真的拥有线下经历。

## 数据与兼容边界

所有数据通过官方 `StarTools.get_data_dir('astrbot_plugin_xiaod_life')` 保存到：

```text
data/plugin_data/astrbot_plugin_xiaod_life/
  interests.json
  activities.json
  archive-<id>.json  # 活动超过1000条或8 MiB时保留完整归档快照
```

活动包含 `id`、`started_at`、`finished_at`、`status`、`topic`、`sources`、`model_findings`、`subjective_evaluation`、`next_direction`、`errors`。`sources` 保留编号、URL、feed URL、标题、实际摘录和抓取时间。失败保留已抓到的内容、错误类型及时间，不删除旧记录，不将失败伪装为成功经历。

采用临时文件 + fsync + 原子替换。JSON 损坏时拒绝继续写入，绝不静默重建。单文件读取上限 16 MiB。达到 1000 条或 8 MiB 时先保存完整归档快照，再在活动文件保留最后五条并追加新记录；归档之间会重叠五条，可按 `id` 去重。归档不自动删除，应定期备份并由管理员管理磁盘空间。单进程内串行写入，不支持多个 AstrBot 进程同时共用同一个数据目录。

不读写 LivingMemory 数据库、配置或内部存储；不读取 QQ 聊天记录；不改 Proactive Chat，不调用主动消息 API，不创建后台计时任务、不注入普通聊天提示词。`llm_generate` 不传对话历史或工具，只发送本插件的兴趣、简短近期主题和公开摘录。现有服务提供商仍会收到这些输入；请勿在兴趣文件中写入敏感个人数据。

## 限制与防护

- 全局至多一次探索并发；两次开始时间至少间隔 30 秒。
- 每次最多两次模型调用，各 45 秒；探索主体总上限 130 秒。
- 每次最多三个 RSS 请求；每个请求 12 秒，连接 5 秒，最多 512 KiB 解压后响应体。
- 每个 feed 最多取 40 个可用条目，摘录最多 1800 字符；模型输出最多 12000 字符，并校验 JSON 及引用编号。
- 只读公开 HTTPS，不使用环境代理/账户凭据，不关闭 TLS 校验，不跟随重定向。
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
- 在部署网络检查三个 RSS 地址是否允许访问；网络策略、地区限制、源重定向或限流都可能导致失败。必要时换成可访问的 HTTPS RSS。
- QQ 私聊及群内管理员/非管理员权限、指令前缀、消息分段和 URL 风控。
- 执行 explore → today → 重载 → today，确认真实历史持久化。
- 并发指令、模型超时、断网、不可写目录和取消卸载，不丢旧日志。
- 与 LivingMemory、Proactive Chat 同时启用，确认原有聊天/记忆/主动消息行为保持不变。代码不存在直接耦合，但实际插件事件优先级仍需共存实测。

## 官方依据

- [插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [v4.29.4 Context API](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/context.py)
- [v4.29.4 StarTools](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/star_tools.py)
- [v4.29.4 命令过滤器](https://github.com/AstrBotDevs/AstrBot/blob/v4.29.4/astrbot/core/star/filter/command.py)

指定的 `https://github.com/AstrBotDevs/helloworld` 返回 404。通过目标仓库的模板来源确认实际模板为 [Soulter/helloworld](https://github.com/Soulter/helloworld)，已读取其入口与元数据，并保留原有 LICENSE、.gitignore。入口与生命周期最终以 v4.29.4 官方源码为准，未沿用模板已废弃的 register 装饰器。

本次 V0.1 修改日期为 2026-11-03。沿用仓库现有 AGPL-3.0 许可证，见 LICENSE。
