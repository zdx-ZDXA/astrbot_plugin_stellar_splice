# -*- coding: utf-8 -*-
"""
astrbot_plugin_stellar_splice —— 星缀 · 轻量陪伴大脑 v1.0.0
==============================================================
挂载执行器（按名字解析，不装就降级）：
  StellarCanvas   → 星绘（生图）  draw_for()
  StellarVoice    → 星言（语音）  speak()
  StellarGuard    → 星盾（守护）  process()
  StellarMemory   → 星忆（记忆）  recall() / save()

设计原则：
  - 人格只首次读一次，后续不再重读（除非 /stellar_reset）
  - 私聊：直接回复，不走判定
  - 群聊：只看 addressed + timing 两个维度
  - 日程三段结构：时间段 / 大事项（可锁定）/ 详细步骤
  - 对话驱动日程：「明天带你去XXX」自动写入
  - 注入精简：只注入模型真正用到的 3 行状态
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# ProviderRequest 仅用于类型标注，拿不到也不影响运行
try:
    from astrbot.api.provider import ProviderRequest
except ImportError:
    ProviderRequest = object

# TextPart：AstrBot 4.x 的临时内容片段，路径可能随版本变化
try:
    from astrbot.core.agent.message import TextPart
except ImportError:
    try:
        from astrbot.api.provider import TextPart
    except ImportError:
        TextPart = None


try:
    from .schedule_authority import (
        overlay, normalize_plan, commit_chat,
        validate_schedule_item, to_min, to_hm,
    )
    from .segment_engine import (
        extract_schedule_segments, looks_like_plan_turn,
    )
    from .heartflow_lite import HeartflowLite
    from .wardrobe import Wardrobe, DEFAULT_OUTFITS
except ImportError:
    from schedule_authority import (
        overlay, normalize_plan, commit_chat,
        validate_schedule_item, to_min, to_hm,
    )
    from segment_engine import (
        extract_schedule_segments, looks_like_plan_turn,
    )
    from heartflow_lite import HeartflowLite
    from wardrobe import Wardrobe, DEFAULT_OUTFITS

PLUGIN_VERSION = "1.0.1"


# ---------------------------------------------------------------------------
# 情绪系统（七维）
# ---------------------------------------------------------------------------
class Feelings:
    """七情六欲：每条消息微调，自然回落。"""

    KEYS = ("joy", "anger", "sorrow", "fear", "love", "energy", "hunger")
    LABELS = {"joy": "喜", "anger": "怒", "sorrow": "哀",
              "fear": "惧", "love": "爱", "energy": "精力", "hunger": "饿"}
    BASE = {"joy": 50, "anger": 20, "sorrow": 20, "fear": 20,
            "love": 55, "energy": 70, "hunger": 30}
    DECAY_PER_HOUR = {"joy": 3, "anger": 5, "sorrow": 4, "fear": 4,
                      "love": 1, "energy": 5, "hunger": 8}

    def __init__(self):
        self.values = dict(self.BASE)
        self._last_tick = time.time()

    def decay(self) -> None:
        now = time.time()
        hours = (now - self._last_tick) / 3600.0
        if hours < 0.1:
            return
        for k in self.KEYS:
            rate = self.DECAY_PER_HOUR.get(k, 2) * hours
            base = self.BASE[k]
            diff = self.values[k] - base
            self.values[k] = base + diff * max(0, 1.0 - rate / max(abs(diff) + 0.001, 1))
            self.values[k] = max(0, min(100, self.values[k]))
        self._last_tick = now

    def bump(self, deltas: Dict[str, float]) -> None:
        self.decay()
        for k, v in (deltas or {}).items():
            if k in self.values:
                self.values[k] = max(0, min(100, self.values[k] + v))

    def bump_from_text(self, text: str, relation: float = 0.5) -> None:
        """根据消息内容和关系系数微调情绪。"""
        self.decay()
        amp = max(0.1, min(2.0, relation))

        # 正向词
        pos = sum(w in text for w in ("谢", "谢谢", "好", "棒", "喜欢", "爱", "嗯", "好的"))
        # 负向词
        neg = sum(w in text for w in ("不", "糟", "差", "烦", "累", "难", "问题"))

        self.bump({
            "joy":    0.5 * pos * amp - 0.3 * neg * amp,
            "love":   0.3 * pos * amp - 0.1 * neg,
            "energy": -0.2 * neg,
        })

    def text(self) -> str:
        self.decay()
        parts = []
        for k, label in self.LABELS.items():
            v = int(round(self.values[k]))
            parts.append(f"{label}{v}")
        return " ".join(parts)

    def to_dict(self) -> Dict[str, int]:
        self.decay()
        return {k: int(round(v)) for k, v in self.values.items()}


# ---------------------------------------------------------------------------
# 主插件类
# ---------------------------------------------------------------------------
class StellarSplice(Star):
    """星缀 · 轻量陪伴大脑（AstrBot 4.x / Star 直接注册）"""

    def __init__(self, context: Context, config: Dict[str, Any] | None = None) -> None:
        super().__init__(context)
        cfg = config or {}

        # 基础配置
        self._bot_name: str = str(cfg.get("bot_name") or "星缀")
        self._bot_aliases: Tuple[str, ...] = tuple(
            a.strip() for a in str(cfg.get("bot_aliases") or "").split(",") if a.strip()
        )
        self._group_threshold: float = float(cfg.get("group_reply_threshold") or 0.5)
        self._persona_locked: bool = False
        self._persona_name: str = ""
        self._persona_prompt: str = ""

        # 情绪
        self._feelings = Feelings()

        # 心流（群聊判定）
        self._heartflow = HeartflowLite(
            bot_names=(self._bot_name,) + self._bot_aliases,
            threshold=self._group_threshold,
        )

        # 衣柜（启动时懒加载）
        self._wardrobe: Optional[Wardrobe] = None

        # 日程（按日期存）
        self._schedules: Dict[str, List[Dict]] = {}   # date -> items
        self._schedule_pending: Dict[str, List[Dict]] = {}   # date -> chat_patches

        # 挂载执行器缓存
        self._canvas_star = None     # 星绘
        self._voice_star = None      # 星言
        self._guard_star = None      # 星盾
        self._memory_star = None     # 星忆

        # 配置
        self._raw_config = config
        self._weather_enabled: bool = bool(cfg.get("weather_enabled", True))
        self._auto_steps_enabled: bool = bool(cfg.get("auto_steps_enabled", True))
        self._inject_state: bool = bool(cfg.get("inject_state", True))

        logger.info(f"[StellarSplice] v{PLUGIN_VERSION} 初始化，大脑已就绪")

    # -------------------------------------------------------------------------
    # 执行器解析（挂载）
    # -------------------------------------------------------------------------
    def _resolve_plugin(self, attr_name: str, plugin_id: str) -> Optional[Any]:
        """按名字解析已注册的子插件实例，找不到就 None。"""
        cached = getattr(self, attr_name, None)
        if cached is not None:
            return cached
        try:
            meta = self.context.get_registered_star(plugin_id)
            if meta is None:
                return None
            for attr in ("star_cls", "instance", "star", "plugin"):
                value = getattr(meta, attr, None)
                if value is not None and not isinstance(value, type):
                    setattr(self, attr_name, value)
                    return value
        except Exception as e:
            logger.debug(f"[StellarSplice] 解析 {plugin_id} 失败: {e}")
        return None

    @property
    def _canvas(self) -> Optional[Any]:
        return self._resolve_plugin("_canvas_star", "StellarCanvas")

    @property
    def _voice(self) -> Optional[Any]:
        return self._resolve_plugin("_voice_star", "StellarVoice")

    @property
    def _guard(self) -> Optional[Any]:
        return self._resolve_plugin("_guard_star", "StellarGuard")

    @property
    def _memory(self) -> Optional[Any]:
        return self._resolve_plugin("_memory_star", "StellarMemory")

    # -------------------------------------------------------------------------
    # 人格（只读一次）
    # -------------------------------------------------------------------------
    async def _ensure_persona(self, event: AstrMessageEvent) -> None:
        """只在首次或 /stellar_reset 后读取人格。"""
        if self._persona_locked:
            return
        try:
            umo = event.unified_msg_origin
            # 读取 AstrBot 当前会话的人格 prompt
            if hasattr(self.context, "get_llm_tool") and callable(
                getattr(self.context, "persona_manager", None)
            ):
                persona = await self.context.persona_manager.resolve_selected_persona(umo)
                if persona:
                    self._persona_prompt = str(getattr(persona, "prompt", "") or "")
                    self._persona_name = str(getattr(persona, "name", "") or "") or self._bot_name
            
            if not self._persona_name:
                self._persona_name = self._bot_name
            
            self._heartflow.set_names((self._persona_name,) + self._bot_aliases)
            self._persona_locked = True
            logger.info(f"[StellarSplice] 人格已锁定：{self._persona_name}")
        except Exception as e:
            logger.debug(f"[StellarSplice] 读取人格失败: {e}")
            self._persona_name = self._bot_name
            self._persona_locked = True

    # -------------------------------------------------------------------------
    # 记忆
    # -------------------------------------------------------------------------
    async def _recall(self, uid: str, text: str, umo: str = "", limit: int = 6) -> List[Dict]:
        """从星忆召回记忆（签名与 Amo_data 一致）。"""
        mem = self._memory
        if mem is None or not uid:
            return []
        try:
            fn = getattr(mem, "recall", None)
            if not callable(fn):
                return []
            is_private = ("FriendMessage" in str(umo)) or ("DirectMessage" in str(umo))
            scope = "private" if is_private else "group"
            group_id = "" if is_private else self._group_id_of(umo)
            return await fn("", uid, text, limit, scope, group_id) or []
        except Exception as e:
            logger.debug(f"[StellarSplice] 记忆召回失败: {e}")
        return []

    @staticmethod
    def _group_id_of(umo: str) -> str:
        """从 aiocqhttp:GroupMessage:12345 取出 12345"""
        parts = str(umo or "").split(":")
        return parts[-1] if len(parts) >= 3 else ""

    async def _save_memory(self, uid: str, content: str, kind: str = "fact", umo: str = "") -> None:
        """保存记忆到星忆（签名与 Amo_data 一致）。"""
        mem = self._memory
        if mem is None or not uid or not content:
            return
        try:
            fn = getattr(mem, "save", None)
            if not callable(fn):
                return
            is_private = ("FriendMessage" in str(umo)) or ("DirectMessage" in str(umo))
            scope = "private" if is_private else "group"
            group_id = "" if is_private else self._group_id_of(umo)
            await fn("", uid, content, kind=kind, scope=scope, group_id=group_id)
        except Exception as e:
            logger.debug(f"[StellarSplice] 记忆保存失败: {e}")

    # -------------------------------------------------------------------------
    # 日程
    # -------------------------------------------------------------------------
    def _today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _get_schedule(self, date: str) -> List[Dict]:
        return self._schedules.get(date) or []

    def _set_schedule(self, date: str, items: List[Dict]) -> None:
        self._schedules[date] = items

    def _current_slot(self, schedule: List[Dict]) -> Optional[Dict]:
        """返回当前正在进行的日程段。"""
        now_min = datetime.now().hour * 60 + datetime.now().minute
        for item in schedule:
            start = to_min(item.get("start"), -1)
            end = to_min(item.get("end"), -1)
            if start <= now_min < end:
                return item
        return None

    def _next_slot(self, schedule: List[Dict]) -> Optional[Dict]:
        """返回下一个即将开始的日程段。"""
        now_min = datetime.now().hour * 60 + datetime.now().minute
        candidates = []
        for item in schedule:
            start = to_min(item.get("start"), -1)
            if start > now_min:
                candidates.append((start, item))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]
        return None

    async def _commit_chat_plan(self, event: AstrMessageEvent, user_text: str, bot_text: str) -> bool:
        """尝试从对话中提取计划并写入日程。返回是否有变化。"""
        if not looks_like_plan_turn(user_text, bot_text):
            return False

        today = self._today_str()
        now_min = datetime.now().hour * 60 + datetime.now().minute

        try:
            result = extract_schedule_segments(
                user_text=user_text,
                bot_text=bot_text,
                today_str=today,
                now_min=now_min,
            )
        except Exception as e:
            logger.debug(f"[StellarSplice] 日程提取失败: {e}")
            return False

        if result is None:
            return False

        target_day = result.get("target_day", today)
        decisions_raw = result.get("decisions", [])

        if not decisions_raw:
            return False

        from .schedule_authority import normalize_plan
        base = self._get_schedule(target_day)
        segments = normalize_plan(result, base, now_min, keep_past=(target_day != today))

        if not segments:
            return False

        merged, patches = commit_chat(target_day, segments, base)
        self._set_schedule(target_day, merged)

        logger.info(
            f"[StellarSplice] 对话驱动日程更新 {target_day}，"
            f"新增 {len(patches)} 段"
        )
        return True

    # -------------------------------------------------------------------------
    # 状态注入（三行精简版）
    # -------------------------------------------------------------------------
    def _build_state_text(self, umo: str = "") -> str:
        """构建 <neo_state> 注入内容，只用 3 行。"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M")

        schedule = self._get_schedule(today)
        current = self._current_slot(schedule)

        if current:
            activity = current.get("intent") or current.get("activity") or "休息"
            place = current.get("place") or "家"
            time_range = f"{current.get('start', '')}-{current.get('end', '')}"
            status_line = f"{time_range} {activity}（{place}）"
        else:
            status_line = f"{time_str} 休息"

        feel = self._feelings.text()

        # 衣着
        outfit = ""
        if self._wardrobe:
            # 根据当前场景选衣服
            scene = "home"
            if current:
                cat = current.get("category", "rest")
                scene_map = {
                    "work": "work", "study": "work",
                    "play": "out", "social": "out",
                    "commute": "out", "meal": "casual",
                    "exercise": "sport", "sleep": "sleep",
                    "rest": "home", "life": "casual",
                }
                scene = scene_map.get(cat, "casual")
            outfit = self._wardrobe.pick_for_scene(scene)
        if not outfit:
            outfit = "换了件舒适的衣服"

        lines = [
            f"当前：{status_line} | 心情：{feel}",
            f"衣着：{outfit}",
        ]
        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # 消息处理
    # -------------------------------------------------------------------------
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        """LLM 请求前注入人格 + 状态。"""
        await self._ensure_persona(event)

        # 注入人格
        if self._persona_prompt:
            if req.system_prompt:
                req.system_prompt = self._persona_prompt + "\n\n---\n\n" + req.system_prompt
            else:
                req.system_prompt = self._persona_prompt

        # 注入状态（精简 3 行）
        if self._inject_state:
            state = self._build_state_text(event.unified_msg_origin)
            if state:
                body = f"<neo_state>\n{state}\n</neo_state>"
                try:
                    parts = getattr(req, "extra_user_content_parts", None)
                    if parts is not None:
                        part = None
                        if TextPart is not None:
                            try:
                                part = TextPart(text=body)
                                if hasattr(part, "mark_as_temp"):
                                    part = part.mark_as_temp()
                            except Exception:
                                part = None
                        if part is None:
                            # 老版本兜底：dict 形式，同样只在本轮生效
                            part = {"type": "text", "text": body, "_no_save": True}
                        parts.append(part)
                except Exception as exc:
                    logger.debug(f"[StellarSplice] 状态注入失败: {exc}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp) -> None:
        """LLM 回复后：更新情绪、尝试提取日程、记录回复时间。"""
        try:
            umo = event.unified_msg_origin
            user_text = str(event.message_str or "")

            # 获取机器人回复文本
            bot_text = ""
            if hasattr(resp, "result_message") and resp.result_message:
                for seg in (resp.result_message.message or []):
                    if hasattr(seg, "text"):
                        bot_text += seg.text
            elif hasattr(resp, "completion_text"):
                bot_text = str(resp.completion_text or "")

            # 更新情绪
            self._feelings.bump_from_text(user_text, relation=0.6)

            # 记录回复时间（群聊判定用）
            self._heartflow.on_replied(umo)

            # 尝试提取日程
            if user_text or bot_text:
                asyncio.ensure_future(
                    self._commit_chat_plan(event, user_text, bot_text)
                )

        except Exception as e:
            logger.debug(f"[StellarSplice] on_llm_response 失败: {e}")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> None:
        """消息入口：私聊直接处理，群聊走心流判定。"""
        try:
            umo = event.unified_msg_origin
            is_private = event.is_private_chat() if hasattr(event, "is_private_chat") else (
                "FriendMessage" in str(umo) or "DirectMessage" in str(umo)
            )
            message = str(event.message_str or "")

            if not is_private:
                # 群聊：判断要不要回
                if not self._heartflow.should_reply(umo, message, is_private=False):
                    event.stop_event()
                    return

            # 更新衣柜（懒加载）
            if self._wardrobe is None and self._memory is not None:
                self._wardrobe = Wardrobe(db_plugin=self._memory)
                asyncio.ensure_future(self._wardrobe.refresh())

        except Exception as e:
            logger.debug(f"[StellarSplice] on_message 失败: {e}")

    # -------------------------------------------------------------------------
    # 指令
    # -------------------------------------------------------------------------
    @filter.command("stellar_reset")
    async def cmd_reset(self, event: AstrMessageEvent):
        """重置人格（下一条消息重新读取）。"""
        self._persona_locked = False
        self._persona_name = ""
        self._persona_prompt = ""
        self._canvas_star = None
        self._voice_star = None
        self._guard_star = None
        self._memory_star = None
        yield event.plain_result("✅ 星缀已重置，下一条消息重新读取人格和挂载。")

    @filter.command("stellar_status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看当前状态。"""
        today = self._today_str()
        schedule = self._get_schedule(today)
        current = self._current_slot(schedule)
        next_slot = self._next_slot(schedule)

        canvas_ok = "✅" if self._canvas else "❌（未挂载星绘）"
        voice_ok = "✅" if self._voice else "❌（未挂载星言）"
        guard_ok = "✅" if self._guard else "❌（未挂载星盾）"
        memory_ok = "✅" if self._memory else "❌（未挂载星忆）"

        outfit = ""
        if self._wardrobe:
            scene = "casual"
            if current:
                cat = current.get("category", "rest")
                scene_map = {
                    "work": "work", "sleep": "sleep",
                    "exercise": "sport", "play": "out",
                }
                scene = scene_map.get(cat, "casual")
            outfit = self._wardrobe.pick_for_scene(scene) or "（未设置）"
        
        current_text = "（无）"
        if current:
            current_text = f"{current.get('start')}-{current.get('end')} {current.get('intent') or current.get('activity')}（{current.get('place') or '家'}）"
            if current.get("steps"):
                current_text += f"\n  步骤：{current['steps'][:40]}"

        next_text = "（今天没有更多安排）"
        if next_slot:
            next_text = f"{next_slot.get('start')}-{next_slot.get('end')} {next_slot.get('intent') or next_slot.get('activity')}"

        lines = [
            f"【星缀 StellarSplice v{PLUGIN_VERSION}】",
            f"人格：{self._persona_name}（{'已锁定' if self._persona_locked else '待读取'}）",
            f"情绪：{self._feelings.text()}",
            f"衣着：{outfit or '（无衣柜）'}",
            f"",
            f"📅 当前日程：{current_text}",
            f"📅 下一段：{next_text}",
            f"",
            f"星绘 {canvas_ok} | 星言 {voice_ok}",
            f"星盾 {guard_ok} | 星忆 {memory_ok}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("stellar_schedule")
    async def cmd_schedule(self, event: AstrMessageEvent):
        """查看今天的日程。"""
        today = self._today_str()
        schedule = self._get_schedule(today)

        if not schedule:
            yield event.plain_result("今天还没有日程，试试告诉我你的计划吧。")
            return

        now_min = datetime.now().hour * 60 + datetime.now().minute
        lines = [f"📅 {today} 日程：", ""]

        for item in schedule:
            start = to_min(item.get("start"), 0)
            end = to_min(item.get("end"), 0)
            intent = item.get("intent") or item.get("activity") or "休息"
            place = item.get("place") or ""
            steps = item.get("steps") or ""
            locked = "🔒" if item.get("locked") else ""
            src = "💬" if item.get("_src") == "chat" else ""

            # 状态标记
            if start <= now_min < end:
                state_mark = "▶ "
            elif end <= now_min:
                state_mark = "✓ "
            else:
                state_mark = "  "

            line = f"{state_mark}{item.get('start')}-{item.get('end')} {src}{intent}{locked}"
            if place:
                line += f"（{place}）"
            lines.append(line)

            if steps:
                lines.append(f"    步骤：{steps[:60]}")

        yield event.plain_result("\n".join(lines))

    @filter.command("stellar_migrate")
    async def cmd_migrate(self, event: AstrMessageEvent):
        """从 Amo_data 迁移记忆。用法：/stellar_migrate /path/to/amo_data.db"""
        arg = str(event.message_str or "").replace("/stellar_migrate", "").strip()
        if not arg:
            yield event.plain_result(
                "用法：/stellar_migrate <amo_data.db 的完整路径>\n"
                "例如：/stellar_migrate ~/.astrbot/data/Astr_Amo_data/amo_data.db"
            )
            return
        mem = self._memory
        if mem is None:
            yield event.plain_result("❌ 未挂载星忆，请先安装 StellarMemory")
            return
        fn = getattr(mem, "migrate_from", None)
        if not callable(fn):
            yield event.plain_result("❌ 当前星忆版本不支持迁移，请升级到 1.0.1+")
            return
        path = arg
        if path.startswith("~"):
            import os as _os
            path = _os.path.expanduser(path)
        yield event.plain_result("⏳ 正在迁移，请稍候…")
        try:
            result = await fn(path)
        except Exception as e:
            yield event.plain_result(f"❌ 迁移失败：{e}")
            return
        if result.get("ok"):
            st = result.get("stats") or {}
            yield event.plain_result(
                f"✅ 迁移完成\n"
                f"记忆 {st.get('memories', 0)} 条\n"
                f"画像 {st.get('profiles', 0)} 条\n"
                f"服装 {st.get('wardrobe', 0)} 件"
            )
        else:
            yield event.plain_result(f"❌ 迁移失败：{result.get('msg')}")

    @filter.command("stellar_plan")
    async def cmd_plan(self, event: AstrMessageEvent):
        """手动添加/更新日程段。用法：/stellar_plan 19:00-21:00 看电影 电影院"""
        args = str(event.message_str or "").replace("/stellar_plan", "").strip()
        if not args:
            yield event.plain_result(
                "用法：/stellar_plan [时间段] [大事项] [地点]\n"
                "例如：/stellar_plan 19:00-21:00 看电影 电影院\n"
                "时间段可省略（自动分配）"
            )
            return

        # 解析参数
        time_range_match = re.match(r"(\d{1,2}[:：]\d{2})-(\d{1,2}[:：]\d{2})\s*(.*)", args)
        if time_range_match:
            start_str = time_range_match.group(1).replace("：", ":")
            end_str = time_range_match.group(2).replace("：", ":")
            rest = time_range_match.group(3).strip()
        else:
            start_str = ""
            end_str = ""
            rest = args

        # 解析大事项和地点
        parts = rest.split()
        intent = parts[0] if parts else args
        place = parts[1] if len(parts) > 1 else ""

        if not intent:
            yield event.plain_result("请提供大事项，例如：看电影")
            return

        today = self._today_str()
        base = self._get_schedule(today)
        now_min = datetime.now().hour * 60 + datetime.now().minute

        # 构建日程条目
        if start_str and end_str:
            start_min = to_min(start_str, now_min)
            end_min = to_min(end_str, start_min + 60)
        else:
            start_min = now_min
            end_min = min(24 * 60, start_min + 60)

        new_item = {
            "start": to_hm(start_min),
            "end": to_hm(end_min),
            "intent": intent[:24],
            "place": place[:12] if place else "待定",
            "steps": "",
            "locked": True,     # 手动添加默认锁定
            "status": "pending",
            "_src": "manual",
        }

        from .schedule_authority import overlay, validate_schedule_item
        validated = validate_schedule_item(new_item)
        merged = overlay(base, [validated])
        self._set_schedule(today, merged)

        yield event.plain_result(
            f"✅ 已添加日程：{validated['start']}-{validated['end']} "
            f"{validated['intent']}（{validated['place']}）🔒"
        )

    # -------------------------------------------------------------------------
    # 衣柜面板 API
    # -------------------------------------------------------------------------
    async def initialize(self) -> None:
        """插件初始化完成后注册面板。"""
        try:
            if hasattr(self.context, "register_web_view"):
                self.context.register_web_view(
                    path="/stellar_splice",
                    file_path="views/wardrobe/index.html",
                    plugin_name="astrbot_plugin_stellar_splice",
                )
        except Exception as e:
            logger.debug(f"[StellarSplice] 面板注册失败（可能版本不支持）: {e}")

        # 预读衣柜
        if self._memory is not None:
            self._wardrobe = Wardrobe(db_plugin=self._memory)
            await self._wardrobe.refresh()
            logger.info(f"[StellarSplice] 衣柜加载完成，共 {self._wardrobe.count()} 件")

    async def _api_wardrobe_list(self) -> Dict:
        if self._wardrobe is None:
            self._wardrobe = Wardrobe()
        return self._wardrobe.api_list()

    async def _api_wardrobe_add(self, payload: Dict) -> Dict:
        if self._wardrobe is None:
            self._wardrobe = Wardrobe()
        return await self._wardrobe.api_add(payload)

    async def _api_wardrobe_delete(self, payload: Dict) -> Dict:
        if self._wardrobe is None:
            return {"code": 1, "msg": "衣柜未初始化"}
        return await self._wardrobe.api_delete(payload)

    async def _api_state(self) -> Dict:
        """面板状态接口。"""
        today = self._today_str()
        schedule = self._get_schedule(today)

        return {
            "version": PLUGIN_VERSION,
            "persona_name": self._persona_name,
            "persona_locked": self._persona_locked,
            "feelings": self._feelings.to_dict(),
            "feelings_text": self._feelings.text(),
            "schedule_today": schedule,
            "plugins": {
                "canvas":  self._canvas is not None,
                "voice":   self._voice is not None,
                "guard":   self._guard is not None,
                "memory":  self._memory is not None,
            },
        }
