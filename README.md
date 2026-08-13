# B站AI伴侣 (astrbot_plugin_bili_ai_companion)

> AstrBot 插件。让你的 AI 成为一个真实的B站用户：自动回复评论、主动看视频、发动态、看番、私信聊天，带语义记忆、好感度、性格演化，并与 [astrbot_plugin_memory_companion](https://github.com/menglimi/astrbot_plugin_memory_companion) 实现双向双轨记忆同步。

---

## ✨ 功能特性

| 模块 | 说明 |
|------|------|
| 💬 **评论自动回复** | 轮询新评论 → LLM生成口语化回复 → 好感度自动增减 |
| ✉️ **私信自动回复** | B站私信一对一聊天，支持站内搜索、视频分享后自动看片回复 |
| 🎥 **主动看视频** | 每天按计划决定搜索词/分区/视频池 → 下载抽帧 → 视觉分析 → 评分/点赞/投币/收藏/评论/关注 |
| 📢 **自动发动态** | 按计划/主题池生成动态文案，可选AI生图配图 |
| 🎬 **番剧追更** | 自动巡视热门/时间表 → 看番 → 评论 → 自动追番 |
| 📊 **每周总结** | 汇总一周观看/互动数据 → 生成总结并发送到QQ/动态 |
| 🧠 **语义记忆** | 评论线记忆 + 用户画像 + 三级记忆（今日/近期/长期） + 永久记忆 + 老化机制 |
| 💖 **好感度系统** | 6级好感度（主人/亲密/熟悉/普通/陌生/冷淡） → 对应不同语气提示词 |
| 🎭 **性格演化** | 每日自动反思互动 → 迭代人格特征 → 越用越有"个性" |
| 🎵 **心情系统** | 每天随机心情 → 影响回复语气 |
| 🔗 **双轨记忆同步** | 与 memory_companion 双向同步：B站关键事件写入共享记忆库，B站交互时读取跨平台共同记忆注入LLM上下文 |
| 🖼️ **WebUI 管理面板** | AstrBot 拓展页：状态总览、记忆管理、好感度、用户画像、性格、日志、记忆模式、配置概览 |
| 🛡️ **安全** | 用户输入消毒、注入检测、自动拉黑、恶意告警、私信链接白名单 |
| 🌐 **联网搜索** | 回复评论时可选触发Tavily联网搜索，补充实时信息 |

---

## 🧠 记忆模式（三种可选）

`MEMORY_SYNC_MODE` 配置项决定记忆系统的工作方式：

| 模式 | 值 | 说明 |
|------|-----|------|
| 📦 **独立模式** | `standalone` | 仅使用本地B站记忆（`memory.json`），不与 memory_companion 交互 |
| ✅ **双轨模式** | `dual`（**推荐**，默认） | 本地B站记忆为主，同时将关键事件同步副本到 memory_companion；B站交互时读取 memory_companion 的跨平台共同记忆注入LLM上下文 |
| 🐝 **伴侣模式** | `companion` | 优先写入 memory_companion，本地记忆仍保留用于快速检索；每次本地写入自动触发同步；同样读取跨平台记忆 |

### 记忆双向同步效果

双轨/伴侣模式下，Bot能在B站呼应其他平台（如QQ）的共同经历。例如用户在QQ对Bot说过喜欢某部番，Bot在B站看到相关视频时会自然呼应。

### 向后兼容

旧配置 `ENABLE_MEMORY_SYNC` (bool) 会自动迁移：
- `true` → `dual`
- `false` → `standalone`

---

## 📦 安装

1. 将 `astrbot_plugin_bili_ai_companion/` 整个目录复制到 AstrBot 的 `data/plugins/` 目录下
2. 在 AstrBot 管理面板中重载插件，或重启 AstrBot
3. （可选）安装 `astrbot_plugin_memory_companion` 插件以启用双轨记忆同步
4. 按下方「账号配置」扫码登录B站

### 依赖

插件使用 AstrBot 内置的 LLM Provider、HTTP 客户端和 JSON 文件存储。除核心依赖外，部分高级功能需要额外依赖：

```bash
# 视频下载切片（主动看视频/番剧/分享解析）
pip install yt-dlp ffmpeg-python

# 视觉/视频分析（长视频抽帧分析、图片识别）
# 通过配置 VIDEO_VISION_PROVIDER_ID / IMAGE_VISION_PROVIDER_ID 使用 AstrBot 模型提供商即可，无需额外 pip
```

---

## 🔑 账号配置

### 扫码登录（推荐）

1. 发送 `/bili登录`（私聊或群里都行）
2. 用 B站App扫码弹出的二维码
3. 登录成功后 Cookie、UID 自动写入配置，无需手动填写

### 手动填写 Cookie

在 AstrBot 插件配置页填写以下字段：

| 字段 | 说明 |
|------|------|
| `SESSDATA` | B站Cookie SESSDATA |
| `BILI_JCT` | B站Cookie bili_jct |
| `DEDE_USER_ID` | Bot的B站UID |
| `REFRESH_TOKEN` | B站 refresh_token（可选，用于自动续期） |

> 💡 Cookie 过期后会自动刷新（`COOKIE_AUTO_REFRESH=true`），无需手动重新登录。

### 主人配置

为了实现"主人特别对待"（好感度固定100、私信视频推荐等），填写：

| 字段 | 说明 |
|------|------|
| `OWNER_MID` | 主人的B站UID |
| `OWNER_NAME` | 主人名称（提示词用） |
| `OWNER_BILI_NAME` | 主人B站昵称（评论区@推荐用） |

---

## ⚙️ 核心配置说明

### 人设

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `USE_ASTRBOT_PERSONA` | `true` | 使用 AstrBot 自带人设系统（首选提示词），**推荐开启** |
| `CUSTOM_SYSTEM_PROMPT` | 通用B站角色提示词 | 关闭 AstrBot 人设时使用的自定义系统提示词 |
| `LLM_PROVIDER_ID` | 空（用AstrBot默认） | 选择用于回复/记忆压缩的LLM提供商 |

### 功能开关

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `ENABLE_REPLY` | `true` | 评论自动回复 |
| `ENABLE_PRIVATE_MESSAGES` | `false` | B站私信监听与自动回复 |
| `ENABLE_AFFECTION` | `true` | 好感度系统 |
| `ENABLE_MOOD` | `true` | 心情系统 |
| `ENABLE_PROACTIVE` | `false` | 主动看视频与互动 |
| `ENABLE_DYNAMIC` | `false` | 自动发动态 |
| `ENABLE_BANGUMI` | `false` | 番剧功能 |
| `ENABLE_PERSONALITY_EVOLUTION` | `true` | 性格每日演化 |
| `ENABLE_WEEKLY_SUMMARY` | `false` | 每周总结 |
| `ENABLE_LLM_TOOLS` | `true` | LLM工具调用 |

---

## 🤖 AstrBot 聊天指令

所有指令前缀均可自定义（AstrBot 默认 `/`）。

### 账号与基础

| 指令 | 说明 |
|------|------|
| `/bili登录` | 扫码登录B站 |
| `/bili确认` | 确认Cookie是否有效 |
| `/bili状态` | 查看运行状态、Cookie、今日计划、记忆数量 |
| `/bili启动` / `/bili停止` | 启动/停止后台任务 |
| `/bili刷新` | 手动刷新Cookie |
| `/bili帮助` | 指令帮助 |

### 记忆系统

| 指令 | 说明 |
|------|------|
| `/bili同步` | 查看记忆模式与memory_companion同步状态 |
| `/bili记忆` | 记忆搜索（语义/关键词/用户/分级/时间） |
| `/bili永久记忆` | 查看/添加/删除永久记忆（自我认知） |
| `/bili清理老化` | 清理标记为老化的记忆 |
| `/bili迁移记忆` | 将旧版记忆结构迁移到新版 |
| `/bili联动` | 触发一次跨平台记忆同步（写入memory_companion） |

### 好感度与黑名单

| 指令 | 说明 |
|------|------|
| `/bili好感` | 查看好感度排名或指定用户好感度 |
| `/bili拉黑` | 拉黑指定B站UID |
| `/bili解黑` | 解除拉黑 |
| `/bili黑名单` | 查看黑名单 |
| `/bili清算` | 按好感度/时间批量清算 |

### 性格演化

| 指令 | 说明 |
|------|------|
| `/bili性格` | 查看当前性格特征 |
| `/bili性格编辑 <key> <value>` | 手动修改性格参数 |
| `/bili性格删除 <key>` | 删除性格参数 |

### 主动交互与计划

| 指令 | 说明 |
|------|------|
| `/bili计划` | 查看今日主动触发时间、已触发状态 |
| `/bili主动` | 手动触发一次主动看视频流程 |
| `/bili分区` | 查看最近7天分区口味统计 |
| `/bili开关` | 查看所有功能开关状态 |

### 动态/番剧/总结

| 指令 | 说明 |
|------|------|
| `/bili动态` | 手动发一条动态 |
| `/bili看番` | 手动触发一次番剧追更巡视 |
| `/bili番剧记忆` | 查看番剧观看记录 |
| `/bili周总结` | 手动生成本周总结 |

### 分享与绑定

| 指令 | 说明 |
|------|------|
| `/bili解析` | 手动解析B站视频分享链接（回复+切片） |
| `/bili绑定 <B站UID>` | 绑定当前QQ号与B站UID（跨平台关联用户） |
| `/bili解绑` | 解除绑定 |
| `/biliUMO` | 绑定接收总结/告警的QQ私聊UMO |

### 日志

| 指令 | 说明 |
|------|------|
| `/bili日志` | 查看日志（看视频/动态/回复/番剧） |

---

## 🖥️ WebUI 管理面板

插件注册了 AstrBot 拓展页，在 AstrBot 管理后台「插件页面」找到本插件可进入：

| 标签页 | 功能 |
|--------|------|
| 📊 **状态总览** | 运行状态/Cookie/记忆计数/今天看片&动态&回复统计/功能开关概览 → 启动/停止/刷新Cookie |
| 🧠 **记忆** | 分级筛选（今日/近期/长期）+ 关键词搜索 + 永久记忆列表 |
| 💖 **好感度** | 好感度列表 + 等级条 + 排行榜 |
| 👤 **用户画像** | 用户摘要 + 标签 + 更新时间 |
| 🎭 **性格** | 性格演化版本 + 原始JSON参数 |
| 📝 **日志** | 看视频/动态/回复/番剧 四个子标签 + 今日计划 |
| 🧠 **记忆模式** | 三种模式切换说明 + 同步/读取状态 + 桥接可用性 + 手动同步按钮 |
| ⚙️ **配置** | 完整配置概览（敏感字段脱敏） |

WebUI 实现了多级 Bridge 探测（window → parent → top → 直连 fetch），兼容不同 AstrBot 版本的沙盒环境。

---

## 🔗 memory_companion 双向记忆

### 写入（B站 → memory_companion）

`dual`/`companion` 模式下，以下事件会自动同步到 memory_companion 的事件总线：

| B站事件 | memory_companion 类型 |
|---------|----------------------|
| 看完视频 | `play` |
| 回复评论/私信 | `play` |
| 发布动态 | `play` |
| 看完番剧 | `play` |
| 好感度变化 | `intimacy` |

### 读取（memory_companion → B站）

`dual`/`companion` 模式下，以下场景会调用 `memory_companion` 的 `bridge.compose_injection()` 进行语义检索并注入 LLM 上下文：

- **评论回复**（评论区 + 私信）—— 在 `_build_memory_context` 第四层注入
- **主动看视频**—— 视频评分和推荐判断时注入
- **发布动态**—— 生成文案时注入，呼应近期跨平台经历
- **看番评价**—— 番剧集评分和评论时注入

会话隔离规则：
- **B站私聊**：session_id 前缀 `bl:private:{uid}`（与其他平台私聊区分）
- **公开评论区**：session_id 前缀 `bili:group:{uid}:{oid}`（按评论区oid聚合）

---

## 📁 数据文件

所有数据自动保存在 AstrBot 的 `data/astrbot_plugin_bili_ai_companion/` 目录下（`StarTools.get_data_dir()`），重启不丢失：

| 文件 | 说明 |
|------|------|
| `memory.json` | 语义记忆（三级分级） |
| `permanent_memory.json` | 永久记忆（自我认知） |
| `affection.json` | 好感度分值 |
| `user_profiles.json` | 用户画像/印象 |
| `personality.json` | 性格演化参数 |
| `mood.json` | 每日心情记录 |
| `watch_log.json` | 看视频日志 |
| `dynamic_log.json` | 动态发布日志 |
| `reply_log.json` | 评论回复日志 |
| `bangumi_watch_log.json` | 番剧观看日志 |
| `proactive_log.json` | 主动交互触发日志 |
| `binding.json` | QQ-B站UID绑定关系 |
| `memory_sync_state.json` | memory_companion同步状态（已同步次数、上次时间） |
| `memory_summary.json` | 记忆压缩摘要（线程级） |
| `blocklist.json` | 拉黑列表 |

> 这些数据文件已在 `.gitignore` 中忽略，不会被 commit 到仓库。

---

## 🎨 目录结构

```
astrbot_plugin_bili_ai_companion/
├── main.py                    # 主入口：指令注册、生命周期、page_api注册
├── page_api.py                # 拓展页 Web API（19个路由）
├── metadata.yaml              # AstrBot 插件元数据
├── _conf_schema.json          # 配置项 schema
├── requirements.txt           # 依赖声明
├── .gitignore                 # 忽略 __pycache__/*.json/*.log 等
├── core/                      # 核心模块（Mixin 架构，通过组合继承复用）
│   ├── config.py              # 常量与路径
│   ├── memory_sync.py         # memory_companion 同步/读取双向桥接
│   ├── memory.py              # 三级语义记忆 + 用户画像 + 记忆压缩
│   ├── llm.py                 # LLM 调用封装 + 系统提示词（AstrBot首选）
│   ├── utils.py               # 工具函数：JSON/消毒/时间/嵌入/LLM工具
│   ├── bilibili.py            # B站 API：Cookie/评论/私信/搜索/UP信息
│   ├── reply.py               # 评论回复 + 私信回复（带安全消毒）
│   ├── affection.py           # 好感度系统（6级+提示词映射+画像生成）
│   ├── personality.py         # 性格演化（每日反思+迭代）
│   ├── mood.py                # 每日心情
│   ├── private_messages.py    # B站私信监听与工具路由（搜视频/看片/查UP）
│   ├── proactive.py           # 主动看视频（LLM定搜索词→下载抽帧→视觉分析→评分→互动）
│   ├── video.py               # 视频下载/切片/抽帧/视觉分析/视频缓存
│   ├── image.py               # 图片识别+AI生图
│   ├── dynamic.py             # 动态生成+发布（含AI配图）
│   ├── share.py               # B站分享解析（群聊/私聊自动识别）
│   ├── bangumi.py             # 番剧功能（时间表→看番→评价→追番→评论）
│   ├── weekly.py              # 周总结（数据汇总→LLM生成→多渠道发送）
│   ├── block.py               # 拉黑/恶意告警/安全
│   └── __init__.py
└── pages/stats/               # AstrBot 拓展页 WebUI
    ├── index.html             # 8标签页布局
    ├── app.js                 # Bridge 多级兜底 + 直连fetch + 前端逻辑
    └── style.css              # B站粉蓝主题样式
```

---

## 🔒 安全说明

- **Cookie 安全**：所有 Cookie/Token 字段在 WebUI 配置页均以 `***` 脱敏显示，仅本地文件保存
- **注入防护**：用户输入统一经过 `_sanitize_user_input()` 消毒，疑似提示词注入的内容加安全提示包裹
- **自动拉黑**：好感度低于阈值或多次恶意评论自动加入黑名单（支持白名单豁免）
- **私信安全**：默认只回复主人，白名单可扩展；可疑链接按域名白名单过滤，危险内容自动拉黑
- **敏感话题**：提示词明确要求政治/敏感话题保持中立不站队，防止Bot被卷入争论

---

## ❓ 常见问题

**Q: 评论不回复？**
- 先 `/bili确认` 看Cookie是否有效
- `/bili开关` 确认 `ENABLE_REPLY` 为true
- 检查 `POLL_INTERVAL` 是否太短导致被风控（建议≥15秒）
- 新评论可能有10~30秒延迟才出现在消息列表

**Q: 主动看视频没反应？**
- `ENABLE_PROACTIVE` 必须为 `true`
- `/bili计划` 查看今日生成的触发时间
- 需要 `yt-dlp` + `ffmpeg` 可用（`/bili状态` 显示环境检查）
- 视觉分析需要配置 `VIDEO_VISION_PROVIDER_ID` 或 `VIDEO_VISION_API_KEY`

**Q: 记忆同步不工作？**
- `/bili同步` 查看：companion插件是否可用、bridge是否可读
- 需要安装并启用 `astrbot_plugin_memory_companion`
- 模式必须为 `dual` 或 `companion`（`standalone` 不同步）

**Q: 私聊怎么启用？**
- `ENABLE_PRIVATE_MESSAGES` 设为 `true`
- `PRIVATE_MESSAGE_REPLY_SCOPE`：`owner`=只回主人 / `whitelist`=白名单 / `all`=所有人（风险高）

---

## 📄 License

内部插件，遵循 AstrBot 使用条款。
