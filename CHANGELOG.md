# 更新日志

## 1.0.3

**Amo 风格可视化面板 + 全面兼容 AstrBot 4.x**

### 面板（新增）

四个页签，配色和结构与 Amo 终端面板对齐（`--pink: #e8489a`、18px 圆角、亮暗双主题）：

- **状态**：人格 + 锁定状态、七维情绪雷达图（4 层网格 + 粉色填充 + 7 顶点）、7 条进度条、
  四宫格显示「在做什么 / 在哪 / 穿着 / 下一段」，20 秒自动刷新。
- **日程**：时间轴列表，播放中粉色高亮、已过变淡、锁定显示 🔒、对话来源显示「聊」；
  底部内嵌手动添加表单（时间段 / 大事项 / 地点）。
- **衣柜**：添加表单（名称 + 槽位 + 八场景芯片多选 + 备注）+ 列表删除。
- **挂载**：四个执行器状态 + 版本信息。

### 新增接口

- `GET  /state` — 人格、七维情绪、今日日程、当前衣着、四执行器挂载状态
- `GET  /wardrobe` — 衣柜列表
- `POST /wardrobe/add` — 添加服装
- `POST /wardrobe/delete` — 删除服装
- `POST /plan/add` — 面板直接添加日程段

### 修复

- `main.py` 里 `PLUGIN_VERSION` 与实际发布版本不一致（显示 1.0.1，实际 1.0.3）。
- `_api_state` 增加 `outfit` 字段，面板「衣着」不再显示空。
- 抽出 `_scene_of()` 统一「日程分类 → 衣柜场景」映射，面板与状态注入共用一套逻辑。
- 补齐 `CHANGELOG.md`（AstrBot 启动告警消除）。

## 1.0.2

**修复配置 schema 格式错误（插件无法加载的根因）**

- `_conf_schema.json` 从 JSON Schema 格式（`{"$schema":..., "properties":{...}}`）
  改为 AstrBot 要求的**扁平字典**格式（`{"key": {type, description, default, hint}}`）。
- 该错误导致 AstrBot 启动时报
  `TypeError: string indices must be integers, not 'str'` ——
  加载器把 `"$schema"` 这个字符串当成配置项去取 `["default"]`。

## 1.0.1

**修复 AstrBot 4.x 导入与注册兼容性**

- `from astrbot.api.message.components` → `from astrbot.api.message_components`。
- 去掉 `@register(...)` 装饰器，改为 `class StellarSplice(Star)` 直接继承
  （与 AstrBot 4.x 内置插件写法一致）。
- `TextPart` 改从 `astrbot.core.agent.message` 导入，并保留 dict 兜底
  （`{"type": "text", "text": ..., "_no_save": True}`）。
- 移除未使用的 `Plain` / `Image` 导入。

## 1.0.0

**首个版本**

- 三段日程结构：时间段 / 大事项（可锁定）/ 详细步骤。
- 对话驱动日程：从聊天里识别计划并写入日程，最多保留已过去的部分。
- 七维情绪：喜 怒 哀 惧 爱 精力 饿，每条消息微调、自然回落至基线。
- 私聊直连（不判定）；群聊只判断「是否被点名」与「是否刚说过话」两个维度。
- 人格开局读取一次后锁定，`/stellar_reset` 可重置。
- 三行精简状态注入（当前活动 / 心情 / 衣着）。
- 衣柜：面板直接添加，按场景自动穿搭。
- 记忆隔离：私聊记忆与群聊记忆严格分开。
- 兼容 Amo_data：`memories` / `profiles` / `wardrobe` 三表结构完全一致，可直连迁移。
