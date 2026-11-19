# -*- coding: utf-8 -*-
"""wardrobe.py —— 星缀衣柜

存储与管理角色服装，兼容 Amo_data 的 wardrobe 表结构。
支持面板直接输入（不走市场），自己管理服装库。

表结构：与 Amo_data wardrobe 表完全兼容
  id, name, slot, scenes, gender, enabled, note, created
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

SCENES = ("work", "home", "casual", "out", "formal", "sport", "sleep", "beach")
SCENE_LABELS = {
    "work": "上班",
    "home": "在家",
    "casual": "日常",
    "out": "出门",
    "formal": "正式",
    "sport": "运动",
    "sleep": "睡觉",
    "beach": "海边",
}

SLOTS = ("top", "bottom", "dress", "jk_skirt", "jk_shirt", "socks", "shoes", "accessory")
SLOT_LABELS = {
    "top": "上衣",
    "bottom": "裤子/裙子",
    "dress": "整套",
    "jk_skirt": "JK 格裙",
    "jk_shirt": "JK 衬衣",
    "socks": "袜子",
    "shoes": "鞋",
    "accessory": "配饰",
}

# 场景关键词自动推断
_SCENE_HINTS = (
    (("睡衣", "睡裙", "家居", "居家", "棉拖"), ("sleep", "home")),
    (("泳", "比基尼", "泳装"), ("beach",)),
    (("西装", "礼服", "套装", "正装"), ("work", "formal")),
    (("运动", "速干", "跑鞋", "瑜伽", "健身"), ("sport",)),
    (("卫衣", "牛仔", "T恤", "外套", "针织", "开衫", "风衣"), ("casual", "out")),
)

MIN_NAME = 2
MAX_NAME = 40
MAX_ITEMS = 200


def clean_name(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = value.strip("，,。.；;：:、!！?？\"'""''")
    return value[:MAX_NAME]


def guess_scenes(name: str) -> List[str]:
    """根据名称猜测场景。"""
    lowered = str(name or "").lower()
    for words, scenes in _SCENE_HINTS:
        if any(word in lowered for word in words):
            return list(scenes)
    return ["casual", "out"]


class Wardrobe:
    """衣柜管理器，兼容 Amo_data wardrobe 表"""
    
    def __init__(self, db_plugin=None):
        """
        db_plugin: Amo_data/星忆 插件实例（可选）
                   有则读写数据库，没有则退回内存
        """
        self._db = db_plugin
        self._memory: List[Dict] = []   # 内存快照
        self._loaded = False
        self._last_refresh = 0.0
    
    async def refresh(self) -> None:
        """从数据库拉取衣柜到内存快照。"""
        if self._db is not None:
            try:
                fn = getattr(self._db, "wardrobe_list", None)
                if callable(fn):
                    rows = await fn()
                    if isinstance(rows, list):
                        self._memory = rows
                        self._loaded = True
                        self._last_refresh = time.time()
                        return
            except Exception:
                pass
        self._loaded = True
    
    def _ensure(self) -> List[Dict]:
        return self._memory
    
    def count(self) -> int:
        return len(self._memory)
    
    def all(self) -> List[Dict]:
        """返回所有启用的服装。"""
        return [item for item in self._memory if item.get("enabled", True)]
    
    def for_scene(self, scene: str, gender: str = "女") -> List[Dict]:
        """返回适合某场景的服装。"""
        result = []
        for item in self.all():
            if gender != "通用" and item.get("gender") not in (gender, "通用"):
                continue
            if scene in (item.get("scenes") or []):
                result.append(item)
        return result
    
    async def add(
        self,
        name: str,
        scenes: List[str] = None,
        gender: str = "女",
        slot: str = "dress",
        note: str = "",
    ) -> Dict:
        """添加一件服装（同名则更新）。"""
        name = clean_name(name)
        if len(name) < MIN_NAME:
            return {}
        
        scenes = [s for s in (scenes or []) if s in SCENES]
        if not scenes:
            scenes = guess_scenes(name)
        
        # 检查是否已存在
        rows = self._ensure()
        for item in rows:
            if item["name"] == name:
                if scenes:
                    item["scenes"] = scenes
                if gender in ("女", "男", "通用"):
                    item["gender"] = gender
                if slot in SLOTS:
                    item["slot"] = slot
                if note:
                    item["note"] = str(note)[:120]
                item["enabled"] = True
                fresh = dict(item)
                await self._persist(fresh)
                return fresh
        
        # 新建
        item = {
            "id": "w" + uuid.uuid4().hex[:10],
            "name": name,
            "slot": slot if slot in SLOTS else "dress",
            "scenes": scenes,
            "gender": gender if gender in ("女", "男", "通用") else "女",
            "enabled": True,
            "note": str(note or "")[:120],
            "created": time.time(),
        }
        rows.append(item)
        if len(rows) > MAX_ITEMS:
            del rows[:-MAX_ITEMS]
        
        await self._persist(item)
        return dict(item)
    
    async def remove(self, item_id: str) -> bool:
        """禁用（软删除）一件服装。"""
        rows = self._ensure()
        for item in rows:
            if item.get("id") == item_id:
                item["enabled"] = False
                await self._persist(item)
                return True
        return False
    
    async def _persist(self, item: Dict) -> None:
        """写回数据库。"""
        if self._db is None:
            return
        try:
            fn = getattr(self._db, "wardrobe_save", None)
            if callable(fn):
                await fn(item)
        except Exception:
            pass
    
    def api_list(self) -> Dict:
        """面板接口：返回衣柜列表。"""
        rows = self.all()
        return {
            "code": 0,
            "data": {
                "items": rows,
                "total": len(rows),
                "scenes": [{"key": k, "label": v} for k, v in SCENE_LABELS.items()],
                "slots": [{"key": k, "label": v} for k, v in SLOT_LABELS.items()],
            },
        }
    
    async def api_add(self, payload: Dict) -> Dict:
        """面板接口：添加服装。"""
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"code": 1, "msg": "名称不能为空"}
        
        scenes = payload.get("scenes") or []
        if isinstance(scenes, str):
            scenes = [s.strip() for s in scenes.split(",")]
        
        item = await self.add(
            name=name,
            scenes=scenes,
            gender=str(payload.get("gender") or "女"),
            slot=str(payload.get("slot") or "dress"),
            note=str(payload.get("note") or ""),
        )
        
        if not item:
            return {"code": 1, "msg": "名称太短，至少 2 个字"}
        
        return {"code": 0, "data": item, "msg": "已添加"}
    
    async def api_delete(self, payload: Dict) -> Dict:
        """面板接口：删除服装。"""
        item_id = str(payload.get("id") or "").strip()
        if not item_id:
            return {"code": 1, "msg": "缺少 id"}
        
        ok = await self.remove(item_id)
        return {"code": 0, "msg": "已删除"} if ok else {"code": 1, "msg": "未找到"}
    
    def pick_for_scene(self, scene: str = "casual", gender: str = "女") -> Optional[str]:
        """为场景挑一件衣服，返回名称字符串。"""
        candidates = self.for_scene(scene, gender)
        if not candidates:
            # 兜底：试试日常
            candidates = self.for_scene("casual", gender)
        if not candidates:
            # 最终兜底
            return DEFAULT_OUTFITS.get(scene, "换了件舒适的衣服")
        
        # 简单选法：按 created 时间取最近添加的（模拟"新衣服穿得更多"）
        candidates.sort(key=lambda x: float(x.get("created") or 0), reverse=True)
        return candidates[0]["name"]


# 兜底服装池（无衣柜时使用）
DEFAULT_OUTFITS = {
    "work":    "米色V领针织开衫配碎花半身裙",
    "home":    "宽松长袖纯棉卫衣配运动裤",
    "casual":  "纯白T恤配浅蓝直筒牛仔裤",
    "out":     "经典卡其色长款风衣",
    "formal":  "黑色修身长款丝绒晚礼服",
    "sport":   "浅色透气瑜伽服套装",
    "sleep":   "浅粉色真丝吊带睡裙",
    "beach":   "蓝白拼色连体保守泳衣",
}
