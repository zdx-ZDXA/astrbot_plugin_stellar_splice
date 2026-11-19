# -*- coding: utf-8 -*-
"""schedule_authority.py —— 星缀的日程权威层

对话驱动的日程系统：三段结构 + 大事项保护

【三段结构】
  1. time_range:  "19:00-21:00"        # 时间段（用户可设，AI自动分配）
  2. intent:      "看电影"              # 大事项（用户指定后锁定，AI不得改）
  3. steps:       "订票-到影院-入场..."  # 详细步骤（AI推理生成，不问用户确认）

【大事项保护】
  - locked=True 时，intent 不可被 commit_chat 修改
  - 只能更新 steps，或用户明确解锁后重新设定 intent
  - AI 自动推理时只能填 steps，根本无权改 intent

【数据流】
  backbone（主干，每天生成）  ┐
                               ├─ overlay() ─ 合并 ─ 落库
  chat_plan（对话覆盖）        ┘

  对话片段 → segment_engine 提取 → normalize_plan → commit_chat()
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

DAY_MIN = 24 * 60

# 来源优先级：聊天 > 手动指令 > 行程覆盖 > 主干模板
SRC_RANK = {"chat": 40, "manual": 30, "trip": 20, "backbone": 10}

CATEGORIES = ("sleep", "work", "study", "meal", "rest", "play",
              "commute", "life", "social", "exercise")

_CATEGORY_HINTS = (
    ("sleep", ("睡", "眯", "赖床", "闭眼")),
    ("meal", ("吃", "饭", "外卖", "奶茶", "咖啡", "夜宵", "早餐", "午饭", "晚饭")),
    ("play", ("电影", "逛", "景区", "景点", "玩", "唱歌", "游戏", "散步", "拍照")),
    ("commute", ("路上", "地铁", "公交", "打车", "高铁", "火车", "开车", "机场", "回家路上")),
    ("work", ("上班", "工作", "开会", "加班", "单位", "工位", "写方案")),
    ("study", ("看书", "学习", "复习", "网课")),
    ("exercise", ("健身", "跑步", "瑜伽")),
)


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------
def to_min(value, default=None):
    """'19:30' / '19：30' / 1170 -> 分钟数；认不出返回 default。'24:00' 合法。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    match = re.match(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$", str(value or ""))
    if not match:
        return default
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour == 24 and minute == 0:
        return DAY_MIN
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return hour * 60 + minute


def to_hm(minutes):
    """分钟数 -> 'HH:MM'"""
    value = max(0, min(DAY_MIN, int(minutes)))
    if value >= DAY_MIN:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def _range(item):
    """日程段 -> (start, end) 分钟；脏数据返回 None。"""
    if not isinstance(item, dict):
        return None
    begin = to_min(item.get("start"), None)
    if begin is None:
        return None
    finish = to_min(item.get("end"), None)
    if finish is None or finish <= begin:
        finish = min(DAY_MIN, begin + 60)
    return begin, finish


def _src_rank(item):
    return SRC_RANK.get(str((item or {}).get("_src") or "backbone"), SRC_RANK["backbone"])


# ---------------------------------------------------------------------------
# 三段结构正规化
# ---------------------------------------------------------------------------
def validate_schedule_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """确保日程条目有完整的三段结构。"""
    if not isinstance(item, dict):
        return {}
    
    validated = {}
    
    # 时间段
    start = to_min(item.get("start"), None)
    if start is None:
        start = 0
    end = to_min(item.get("end"), None)
    if end is None or end <= start:
        end = min(DAY_MIN, start + 60)
    
    validated["start"] = to_hm(start)
    validated["end"] = to_hm(end)
    
    # 大事项
    intent = str(item.get("intent") or item.get("activity") or "").strip()[:24]
    validated["intent"] = intent or "待着"
    validated["locked"] = bool(item.get("locked", False))
    
    # 详细步骤
    steps = str(item.get("steps") or item.get("detail") or "").strip()[:200]
    validated["steps"] = steps or ""
    
    # 元数据
    validated["place"] = str(item.get("place") or "").strip()[:12] or "家"
    validated["category"] = str(item.get("category") or "").strip() or "rest"
    validated["status"] = str(item.get("status") or "pending").strip()  # running/pending/future
    validated["_src"] = str(item.get("_src") or "backbone").strip()
    
    return validated


# ---------------------------------------------------------------------------
# 合并核心
# ---------------------------------------------------------------------------
def _fill_gaps(items: List[Dict]) -> List[Dict]:
    """空档用上一段的内容顺延，保证 24 小时闭环。"""
    if not items:
        return items
    
    out = []
    cursor = 0
    
    for row in items:
        start = to_min(row.get("start"), 0)
        end = to_min(row.get("end"), start + 60)
        
        if start > cursor:
            fill = dict(out[-1]) if out else {
                "intent": "睡觉",
                "place": "家",
                "category": "sleep",
                "_src": "backbone"
            }
            fill = dict(fill)
            fill["start"] = to_hm(cursor)
            fill["end"] = to_hm(start)
            fill["_filled"] = True
            fill["locked"] = False
            fill["steps"] = ""
            fill["status"] = "pending"
            out.append(fill)
        
        out.append(row)
        cursor = max(cursor, end)
    
    if cursor < DAY_MIN:
        fill = dict(out[-1]) if out else {
            "intent": "睡觉",
            "place": "家",
            "category": "sleep",
            "_src": "backbone"
        }
        fill = dict(fill)
        fill["start"] = to_hm(cursor)
        fill["end"] = "24:00"
        fill["_filled"] = True
        fill["locked"] = False
        fill["steps"] = ""
        fill["status"] = "pending"
        out.append(fill)
    
    return out


def overlay(base: List[Dict], patches: List[Dict]) -> List[Dict]:
    """主干 + 覆盖段 -> 合并后的日程。
    
    原子区间算法：把两边所有边界排序，逐区间选出优先级最高的那一段。
    19:30-21:10 的「看电影」只吃掉这一段，前后主干原样保留。
    """
    entries = []
    
    for item in base or []:
        bounds = _range(item)
        if not bounds:
            continue
        validated = validate_schedule_item(item)
        row = dict(validated)
        row["_src"] = "backbone"
        entries.append((bounds[0], bounds[1], _src_rank(row), row))
    
    for item in patches or []:
        bounds = _range(item)
        if not bounds:
            continue
        validated = validate_schedule_item(item)
        row = dict(validated)
        row["_src"] = row.get("_src") or "chat"
        entries.append((bounds[0], bounds[1], _src_rank(row), row))
    
    if not entries:
        result = []
        for item in base or []:
            result.append(validate_schedule_item(item))
        return _fill_gaps(result)
    
    # 收集所有边界
    bounds = {0, DAY_MIN}
    for start, end, _rank, _row in entries:
        bounds.add(start)
        bounds.add(end)
    marks = sorted(point for point in bounds if 0 <= point <= DAY_MIN)
    
    # 逐区间选择
    picked = []
    for index in range(len(marks) - 1):
        begin, finish = marks[index], marks[index + 1]
        if begin >= finish:
            continue
        
        middle = (begin + finish) / 2.0
        best = None
        best_key = None
        
        for start, end, rank, row in entries:
            if not (start <= middle < end):
                continue
            key = (rank, start)
            if best_key is None or key > best_key:
                best = row
                best_key = key
        
        if best is None:
            continue
        
        row = dict(best)
        row["start"] = to_hm(begin)
        row["end"] = to_hm(finish)
        if row.get("_filled"):
            row.pop("_filled")
        picked.append(row)
    
    # 合并相邻同质段
    merged = []
    for row in picked:
        if merged:
            prev = merged[-1]
            same = (
                prev.get("end") == row.get("start")
                and str(prev.get("intent")) == str(row.get("intent"))
                and str(prev.get("place")) == str(row.get("place"))
                and str(prev.get("category")) == str(row.get("category"))
                and str(prev.get("_src")) == str(row.get("_src"))
            )
            if same:
                # 后一段的 steps/locked 可能更新，需要保留
                for key in ("steps", "locked", "status"):
                    if row.get(key) not in (None, "", False):
                        prev[key] = row[key]
                prev["end"] = row.get("end")
                continue
        merged.append(row)
    
    return _fill_gaps(merged)


# ---------------------------------------------------------------------------
# 对话覆盖段：模型 JSON -> 合法段
# ---------------------------------------------------------------------------
def _category_of(raw: str, activity: str) -> str:
    """从原始输入或活动文本推断分类。"""
    name = str(raw or "").strip().lower()
    if name in CATEGORIES:
        return name
    for category, words in _CATEGORY_HINTS:
        if any(word in activity for word in words):
            return category
    return "rest"


def normalize_plan(data: Dict, base: List[Dict], now_min: int, keep_past: bool = False) -> List[Dict]:
    """模型 JSON -> 覆盖段列表。只认已经说定的事，过去的部分不动。
    
    输入格式：
    {
      "decisions": [
        {
          "start": "19:30",
          "end": "21:00",
          "intent": "看电影",           # 大事项
          "steps": "买票-进场-开场",     # 详细步骤（可选，AI推理）
          "place": "电影院",
          "category": "play",
          "mode": "replace"
        }
      ]
    }
    """
    out = []
    
    for raw in (data or {}).get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        
        mode = str(raw.get("mode") or "replace").strip().lower()
        
        # intent 优先级：decisions.intent > decisions.activity
        intent = str(raw.get("intent") or raw.get("activity") or "").strip()
        
        start = to_min(raw.get("start"), None)
        if start is None:
            start = now_min
        
        end = to_min(raw.get("end"), None)
        if end is None:
            end = min(DAY_MIN, start + 60)
        
        if not keep_past:
            if start + 30 < now_min:
                continue
            start = max(start, now_min)
        
        if end <= start:
            continue
        
        if not intent:
            continue
        
        place = str(raw.get("place") or "").strip()
        category = _category_of(raw.get("category"), intent)
        steps = str(raw.get("steps") or "").strip()[:200]
        
        out.append({
            "start": to_hm(start),
            "end": to_hm(end),
            "intent": intent[:24],
            "place": (place or "家")[:12],
            "category": category,
            "steps": steps,
            "locked": False,  # 对话提取的 intent 默认不锁定
            "status": "pending",
            "_src": "chat",
            "mode": mode,
        })
    
    out.sort(key=lambda row: to_min(row.get("start"), 0))
    return out


def commit_chat(day: str, segments: List[Dict], base: List[Dict] = None) -> Tuple[List[Dict], List[Dict]]:
    """对话提交：返回 (merged_result, patches_to_store)"""
    if base is None:
        base = []
    
    # 对话段落经过验证与标准化
    validated_segments = []
    for seg in segments:
        validated_segments.append(validate_schedule_item(seg))
    
    # 合并
    merged = overlay(base, validated_segments)
    
    return merged, validated_segments


def update_intent_with_lock(schedule: List[Dict], segment_idx: int, new_intent: str, new_steps: str = "") -> List[Dict]:
    """更新特定段落，尊重 locked 标记。
    
    如果 locked=True，只能更新 steps；无法更新 intent。
    用户必须明确解锁才能改 intent。
    """
    if not (0 <= segment_idx < len(schedule)):
        return schedule
    
    item = schedule[segment_idx]
    
    if item.get("locked", False):
        # 已锁定：只能改 steps
        schedule[segment_idx]["steps"] = str(new_steps).strip()[:200]
    else:
        # 未锁定：可以改 intent 和 steps
        schedule[segment_idx]["intent"] = str(new_intent).strip()[:24] or item.get("intent")
        schedule[segment_idx]["steps"] = str(new_steps).strip()[:200]
    
    return schedule


def lock_intent(schedule: List[Dict], segment_idx: int) -> List[Dict]:
    """锁定特定段落的 intent。"""
    if 0 <= segment_idx < len(schedule):
        schedule[segment_idx]["locked"] = True
    return schedule


def unlock_intent(schedule: List[Dict], segment_idx: int) -> List[Dict]:
    """解锁特定段落的 intent。"""
    if 0 <= segment_idx < len(schedule):
        schedule[segment_idx]["locked"] = False
    return schedule


# 导出主要接口
__all__ = [
    "to_min",
    "to_hm",
    "validate_schedule_item",
    "overlay",
    "normalize_plan",
    "commit_chat",
    "update_intent_with_lock",
    "lock_intent",
    "unlock_intent",
]
