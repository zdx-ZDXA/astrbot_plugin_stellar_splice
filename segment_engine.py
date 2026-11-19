# -*- coding: utf-8 -*-
"""segment_engine.py —— 从对话中提取日程片段

把用户说的「明天带你去电影院」「晚上一起吃饭」「我们去爬山」这类话
提取成标准日程片段，交给 schedule_authority 处理。

关键规则：
1. 只提取明确说定的事，不猜测
2. 大事项保持原意，不擅自改写
3. 时间模糊时推断合理时段
4. 提取失败宁可返回空，不乱写
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 表示"计划"的高频动词
_PLAN_VERBS = (
    "去", "带你去", "陪你", "我们去", "一起去", "想去",
    "打算", "计划", "安排", "明天", "后天", "今晚",
    "要去", "过去", "出门", "出去",
)

# 表示"否定"的词，含这些词的不算计划
_NEGATION = ("不", "没", "别", "不想", "取消", "算了", "不去", "改天")

# 时间关键词 -> 粗略分钟
_TIME_HINTS: List[Tuple[Tuple[str, ...], int, int]] = [
    (("早上", "早晨", "上午"),              7 * 60,   9 * 60),
    (("中午", "午饭"),                      12 * 60,  13 * 60),
    (("下午"),                              14 * 60,  18 * 60),
    (("傍晚", "黄昏"),                      17 * 60,  19 * 60),
    (("晚上", "夜里", "今晚", "晚饭"),      18 * 60,  21 * 60),
    (("夜间", "深夜", "半夜"),              22 * 60,  24 * 60),
]

# 活动类型关键词 -> 合理时长（分钟）
_DURATION_HINTS: List[Tuple[Tuple[str, ...], int]] = [
    (("电影", "看片", "影院"),              120),
    (("爬山", "徒步", "登山"),              240),
    (("逛街", "购物", "商场"),              180),
    (("吃饭", "餐厅", "火锅", "烧烤"),     90),
    (("咖啡", "奶茶", "下午茶"),           60),
    (("游乐园", "景区", "公园"),            240),
    (("ktv", "唱歌"),                       180),
    (("演唱会", "音乐会"),                  180),
    (("美甲", "美容", "按摩"),             90),
    (("工作", "上班", "开会"),             480),
    (("健身", "跑步", "游泳"),             90),
]


# ---------------------------------------------------------------------------
# 时间推断
# ---------------------------------------------------------------------------
def _guess_time(text: str, now_min: int = -1) -> Tuple[int, int]:
    """从文本推断 (start, end) 分钟数。"""
    # 尝试匹配明确时间 "19:30" 或 "下午三点"
    explicit = re.search(r"(\d{1,2})[：:点时](\d{0,2})", text)
    if explicit:
        hour = int(explicit.group(1))
        minute = int(explicit.group(2) or "0")
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            start = hour * 60 + minute
            duration = _guess_duration(text)
            return start, min(24 * 60, start + duration)
    
    # 时间段关键词
    for hints, default_start, default_end in _TIME_HINTS:
        if any(h in text for h in hints):
            duration = _guess_duration(text)
            end = min(24 * 60, default_start + duration)
            return default_start, max(end, default_end)
    
    # 兜底：如果 now_min 有效，从当前时刻往后推
    if now_min >= 0:
        duration = _guess_duration(text)
        return now_min, min(24 * 60, now_min + duration)
    
    # 完全没有时间信息
    return -1, -1


def _guess_duration(text: str) -> int:
    """根据活动类型估算时长（分钟）。"""
    for keywords, duration in _DURATION_HINTS:
        if any(k in text for k in keywords):
            return duration
    return 60  # 默认 1 小时


# ---------------------------------------------------------------------------
# 日期推断
# ---------------------------------------------------------------------------
def _guess_target_day(text: str, today_str: str) -> str:
    """推断日程属于哪一天。"""
    if "明天" in text or "明日" in text:
        tomorrow = datetime.strptime(today_str, "%Y-%m-%d") + timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d")
    if "后天" in text:
        day_after = datetime.strptime(today_str, "%Y-%m-%d") + timedelta(days=2)
        return day_after.strftime("%Y-%m-%d")
    if "下周" in text:
        next_week = datetime.strptime(today_str, "%Y-%m-%d") + timedelta(days=7)
        return next_week.strftime("%Y-%m-%d")
    return today_str  # 默认今天


# ---------------------------------------------------------------------------
# 核心提取
# ---------------------------------------------------------------------------
def _has_plan_intent(text: str) -> bool:
    """文本是否表达了出行/活动计划。"""
    has_neg = any(neg in text for neg in _NEGATION)
    if has_neg:
        return False
    return any(verb in text for verb in _PLAN_VERBS)


def _extract_place(text: str) -> str:
    """简单抽取地点名。"""
    patterns = [
        r"去(.{1,8}?)(?:[玩看吃逛买游]|$)",
        r"在(.{1,8}?)(?:里|里面|那边|那里|附近)",
        r"(.{1,8}?(?:公园|广场|商场|商城|影院|餐厅|博物馆|景区|山|海|湖|寺|庙))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            place = m.group(1).strip()
            if len(place) >= 2:
                return place[:12]
    return ""


def extract_schedule_segments(
    user_text: str,
    bot_text: str = "",
    today_str: str = None,
    now_min: int = -1,
) -> Optional[Dict]:
    """从一轮对话中提取日程变化。
    
    返回 None 表示这轮没有明确的日程变化。
    返回 {"target_day": "2026-11-20", "decisions": [...]} 表示有变化。
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 合并用户话和机器人确认的话
    combined = f"{user_text or ''} {bot_text or ''}".strip()
    
    if not combined:
        return None
    
    if not _has_plan_intent(combined):
        return None
    
    # 推断目标日期
    target_day = _guess_target_day(combined, today_str)
    
    # 提取意图（大事项）
    # 简单策略：找动词后的活动描述
    intent = _extract_intent(combined)
    if not intent:
        return None
    
    # 推断时间
    start_min, end_min = _guess_time(combined, now_min)
    if start_min < 0:
        # 如果是明天/后天的计划，默认给一个合理时段
        if "明天" in combined or "明日" in combined:
            # 白天合理时段
            duration = _guess_duration(combined)
            # 检查是否有时间提示
            has_time_hint = any(any(hint in combined for hint in hints) for hints, *_ in _TIME_HINTS)
            if has_time_hint:
                for hints, ds, de in _TIME_HINTS:
                    if any(h in combined for h in hints):
                        start_min = ds
                        end_min = min(24 * 60, ds + duration)
                        break
            else:
                # 完全没时间信息，给全天估算
                return None
        else:
            return None
    
    # 提取地点
    place = _extract_place(combined)
    
    return {
        "target_day": target_day,
        "decisions": [{
            "start": f"{start_min // 60:02d}:{start_min % 60:02d}",
            "end":   f"{end_min // 60:02d}:{end_min % 60:02d}",
            "intent": intent,
            "place": place or "待定",
            "steps": "",   # steps 为空，由 AI 生成
            "mode": "replace",
        }],
    }


def _extract_intent(text: str) -> str:
    """从文本提取大事项核心描述。"""
    # 策略1：找「带你/陪你/一起 + 活动」
    m = re.search(r"(?:带你|陪你|一起|我们)(?:去)?(.{2,16}?)(?:吧|啊|呀|哦|噢|$)", text)
    if m:
        intent = m.group(1).strip()
        if _is_valid_intent(intent):
            return intent[:24]
    
    # 策略2：找「去 + 地点/活动」
    m = re.search(r"(?:去|打算|计划|安排)(.{2,16}?)(?:吧|啊|呀|，|,|。|\s|$)", text)
    if m:
        intent = m.group(1).strip()
        if _is_valid_intent(intent):
            return intent[:24]
    
    # 策略3：找「明天 + 动词短语」
    m = re.search(r"明天(.{2,16}?)(?:吧|啊|呀|，|,|。|\s|$)", text)
    if m:
        intent = m.group(1).strip()
        if _is_valid_intent(intent):
            return intent[:24]
    
    return ""


def _is_valid_intent(text: str) -> bool:
    """基本校验：排除太短、只有虚词的情况。"""
    text = text.strip()
    if len(text) < 2:
        return False
    # 排除只有疑问/感叹词
    invalid = ("怎么", "什么", "吗", "呢", "啊", "哦", "嗯")
    if text in invalid or all(c in "?？！!。. " for c in text):
        return False
    return True


def looks_like_plan_turn(user_text: str, bot_text: str) -> bool:
    """快速判断：这轮对话是否可能在聊计划/安排。
    
    性能敏感路径：用简单关键词判断，不用 AI。
    """
    combined = f"{user_text or ''} {bot_text or ''}".lower()
    return _has_plan_intent(combined)
