# -*- coding: utf-8 -*-
"""memory_scope.py —— 记忆隔离

私聊记忆与群聊记忆严格隔离，兼容 StellarMemory/Amo_data 的 umo 格式。
"""
from __future__ import annotations
from typing import Any, Dict, Iterable, List

GROUP_MARK = "GroupMessage"


def is_group_umo(umo: Any) -> bool:
    return GROUP_MARK in str(umo or "")


def is_private_umo(umo: Any) -> bool:
    return not is_group_umo(umo)


def private_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只留私聊的行（无 umo 字段的老数据按私聊保留）。"""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        umo = row.get("umo")
        if umo and is_group_umo(umo):
            continue
        out.append(row)
    return out
