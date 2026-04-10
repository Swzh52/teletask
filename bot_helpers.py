"""
bot_helpers.py
辅助模块：消息延迟删除、关键词冷却、用户触发计数（自动Ban）
"""
import asyncio, time, logging
from collections import defaultdict

log = logging.getLogger(__name__)

# ======== 延迟删除消息 ========
async def delete_later(bot, chat_id, message_id, delay_seconds: float):
    """等待 delay_seconds 后删除指定消息"""
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        log.info(f"✅ 已删除消息 {message_id} (chat={chat_id})")
    except Exception as e:
        log.debug(f"删除消息失败(可能已被手动删除): {e}")

# ======== 关键词冷却（5秒内同一关键词只回复一次） ========
_kw_last_reply: dict[int, float] = {}
KW_COOLDOWN_SECONDS = 5.0

def check_kw_cooldown(kw_id: int) -> bool:
    """
    返回 True  → 可以回复（已过冷却期）
    返回 False → 冷却中，跳过
    """
    now = time.monotonic()
    if now - _kw_last_reply.get(kw_id, 0) < KW_COOLDOWN_SECONDS:
        return False
    _kw_last_reply[kw_id] = now
    return True

# ======== 用户触发计数（自动Ban用） ========
# { user_id: [timestamp, timestamp, ...] }
_user_trigger_history: dict[int, list[float]] = defaultdict(list)
_HISTORY_WINDOW = 3600  # 最多保留最近1小时

def record_trigger(user_id: int):
    """记录用户触发一次"""
    now = time.monotonic()
    _user_trigger_history[user_id].append(now)
    # 清理过期记录
    cutoff = now - _HISTORY_WINDOW
    _user_trigger_history[user_id] = [t for t in _user_trigger_history[user_id] if t > cutoff]

def get_trigger_count(user_id: int, window_seconds: int) -> int:
    """返回用户在 window_seconds 内的触发次数"""
    now = time.monotonic()
    cutoff = now - window_seconds
    return sum(1 for t in _user_trigger_history.get(user_id, []) if t > cutoff)
