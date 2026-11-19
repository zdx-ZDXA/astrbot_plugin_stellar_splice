# 🌟 星缀 StellarSplice

一个有日程、有情绪、记得住你的轻量陪伴大脑。

## 三个核心特性

**日程是聊出来的** — 你说「明天带你去电影院」，她自动记进日程，分三段：时间段 / 大事项 / 详细步骤。用户可锁死大事项，AI 只能补步骤。

**私聊秒回，群聊看场合** — 私聊永远回；群聊只判断有没有被 @、是不是刚说过话。不刷屏，也不装死。

**人格只读一次** — 开局锁住，稳定到你想重置为止。不会聊着聊着就换了性格。

## 功能清单

- **七维情绪**：喜 怒 哀 惧 爱 精力 饿，每条消息微调、自然回落
- **衣柜系统**：面板直接加衣服，选场景、选槽位，自动按场景穿搭
- **记忆隔离**：私聊 / 群聊分开记
- **精简状态注入**：只给三行状态，不灌几万字

## 五件套配置

| 插件 | 用途 | 可选 |
|---|---|---|
| 星缀（本体） | 日程、情绪、判定引擎 | ❌ 必装 |
| 星忆 | 记忆库 + 衣柜 | ⚠️ 推荐装 |
| 星绘 | 生图 | ✅ 可选 |
| 星言 | 语音 | ✅ 可选 |
| 星盾 | 文本清洗 + 断句 | ✅ 可选 |

## 快速开始

1. **装主体** — AstrBot 插件页 → 选 zip `astrbot_plugin_stellar_splice-1.0.2.zip`
2. **装星忆** — 支持从 Amo_data 迁移，表结构完全兼容
   ```
   cp ~/.astrbot/data/Astr_Amo_data/amo_data.db \
      ~/.astrbot/data/StellarMemory/stellar_memory.db
   # 或用指令：/stellar_migrate <路径>
   ```
3. **装其他执行器** — 按需加星绘/星言/星盾

## 主要指令

```
/stellar_status                        查看状态（人格/情绪/衣着/日程）
/stellar_schedule                      看今天日程
/stellar_plan 19:00-21:00 看电影 电影院  手动加日程段
/stellar_migrate <路径>                从 Amo_data 迁移
/stellar_reset                         重置人格
```

## 配置项

| 项 | 类型 | 默认 | 说明 |
|---|---|---|---|
| bot_name | string | 星缀 | 机器人名字 |
| bot_aliases | string | | 别名（逗号分隔） |
| group_reply_threshold | float | 0.5 | 群聊回复阈值（0~1） |
| weather_enabled | bool | true | 天气查询 |
| auto_steps_enabled | bool | true | 自动生成日程步骤 |
| inject_state | bool | true | 每轮注入三行状态 |

## 仓库

| 项目 | 链接 |
|---|---|
| 星缀（主体） | https://github.com/zdx-ZDXA/astrbot_plugin_stellar_splice |
| 星忆（记忆） | https://github.com/zdx-ZDXA/StellarMemory |
| 星绘（生图） | https://github.com/zdx-ZDXA/StellarCanvas |
| 星言（语音） | https://github.com/zdx-ZDXA/StellarVoice |
| 星盾（守护） | https://github.com/zdx-ZDXA/StellarGuard |

## 故障排查

**插件装不上？**
- 确认 AstrBot 版本 ≥ 4.5.0
- zip 根目录要是插件文件夹

**日程不生成？**
- 对话要含时间词（明天/下午/晚上 等）
- 需要装星忆才能持久化

**记忆迁移失败？**
- 检查 Amo_data.db 路径是否存在
- 用 `/stellar_migrate` 指令查看详细错误

---

v1.0.2 · MIT License · 零第三方依赖

由 Kiro 开发 | 基于 AstrBot 4.x | 兼容 Amo_data 数据库
