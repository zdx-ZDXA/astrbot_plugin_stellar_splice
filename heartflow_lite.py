# -*- coding: utf-8 -*-
"""heartflow_lite.py —— 轻量心流判定

私聊：直接回，不走判定。
群聊：只看两个维度：
  1. addressed（0.6权重）：有没有点名/@ / 接着她说
  2. timing（0.4权重）：距上次说话是否够久

不调额外模型，纯规则。
"""

from __future__ import annotations

import re
import time
from typing import Optional

# ---------------------------------------------------------------------------
# 时机维度
# ---------------------------------------------------------------------------
# 冷却惩罚：说话后 N 秒内，分数打折
_TIMING_PENALTY = (
    (90,   -0.8),   # 90 秒内刚说过话：强烈惩罚
    (240,  -0.3),   # 4 分钟内：轻微惩罚
    (1800,  0.0),   # 30 分钟内：中性
)

# 点名增益：被 @ 或叫名字
_ADDRESSED_BONUS = 0.9


def _timing_score(last_reply_at: float) -> float:
    """基于上次说话时间计算时机得分（-1 ~ +0.3）"""
    if last_reply_at <= 0:
        return 0.1  # 从未说过话：轻微加成
    
    elapsed = time.time() - last_reply_at
    
    for threshold, penalty in _TIMING_PENALTY:
        if elapsed < threshold:
            return penalty
    
    # 超过 30 分钟，冷场了：给一点正向
    return 0.2


def _addressed_score(message: str, bot_names: tuple) -> float:
    """基于是否被点名计算得分（-0.4 ~ +1.0）"""
    if not message:
        return 0.0
    
    # 被 @ 或叫名字
    for name in bot_names:
        if name and name in message:
            return _ADDRESSED_BONUS
    
    # @ 符号
    if "@" in message or "＠" in message:
        return _ADDRESSED_BONUS
    
    # 明确的问句（以？结尾，更可能期待回复）
    if message.strip().endswith(("？", "?", "吗", "呢", "嗯", "啊")):
        return 0.3
    
    # 明确排除（在说别人）
    exclude_patterns = [
        r"@\w+",        # 在 @ 别人
        r"他说",        # 转述
        r"刚才.*说",
    ]
    for pattern in exclude_patterns:
        if re.search(pattern, message):
            return -0.4
    
    return 0.0  # 中性


def should_reply_group(
    message: str,
    last_reply_at: float,
    bot_names: tuple,
    threshold: float = 0.5,
) -> bool:
    """群聊是否应该回复。
    
    Args:
        message: 用户消息文本
        last_reply_at: 上次回复的时间戳（0 表示从未回复）
        bot_names: 机器人的名字/别名元组
        threshold: 回复阈值（默认 0.5）
    
    Returns:
        True = 应该回复
    """
    # 权重
    W_ADDRESSED = 0.6
    W_TIMING = 0.4
    
    addressed = _addressed_score(message, bot_names)
    timing = _timing_score(last_reply_at)
    
    score = W_ADDRESSED * addressed + W_TIMING * timing
    
    return score >= threshold


class HeartflowLite:
    """轻量心流状态管理器"""
    
    def __init__(self, bot_names: tuple = (), threshold: float = 0.5):
        self.bot_names = bot_names
        self.threshold = threshold
        self._last_reply: dict[str, float] = {}   # umo -> timestamp
    
    def on_replied(self, umo: str) -> None:
        """记录刚刚回复了某个会话。"""
        self._last_reply[umo] = time.time()
    
    def should_reply(self, umo: str, message: str, is_private: bool) -> bool:
        """判断是否应该回复。
        
        私聊：永远返回 True。
        群聊：走心流判定。
        """
        if is_private:
            return True
        
        last_at = self._last_reply.get(umo, 0)
        return should_reply_group(
            message=message,
            last_reply_at=last_at,
            bot_names=self.bot_names,
            threshold=self.threshold,
        )
    
    def set_names(self, names: tuple) -> None:
        """更新机器人名字/别名。"""
        self.bot_names = tuple(n for n in names if n)
