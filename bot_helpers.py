"""
bot_helpers.py
辅助模块：消息延迟删除、关键词冷却、用户触发计数（自动Ban）
"""
import asyncio, time, logging
from collections import defaultdict

log = logging.getLogger(__name__)

# ======== 延迟删除消息 ========
async def delete_later(bot, chat_id, message_id, delay_seconds: float):
    await asyncio.sleep(delay_seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        log.info(f"✅ 已删除消息 {message_id} (chat={chat_id})")
    except Exception as e:
        log.debug(f"删除消息失败(可能已被手动删除): {e}")


# ======== 关键词冷却 ========
# key 为 (kw_id, chat_id)，不同群组冷却互不影响
_kw_last_reply: dict[tuple, float] = {}
KW_COOLDOWN_SECONDS = 5.0
# 超过此时长未触发的冷却记录视为过期，在下次检查时清理
_KW_COOLDOWN_GC_INTERVAL = 3600.0  # 每小时清理一次
_kw_last_gc: float = 0.0


def _gc_cooldown():
    """清理超过冷却期的过期条目，防止内存只增不减。"""
    global _kw_last_gc
    now = time.monotonic()
    if now - _kw_last_gc < _KW_COOLDOWN_GC_INTERVAL:
        return
    _kw_last_gc = now
    cutoff = now - KW_COOLDOWN_SECONDS
    expired = [k for k, v in _kw_last_reply.items() if v < cutoff]
    for k in expired:
        del _kw_last_reply[k]


def check_kw_cooldown(kw_id: int, chat_id: int) -> bool:
    """
    返回 True  → 可以回复
    返回 False → 冷却中，跳过
    """
    _gc_cooldown()
    key = (kw_id, chat_id)
    now = time.monotonic()
    if now - _kw_last_reply.get(key, 0) < KW_COOLDOWN_SECONDS:
        return False
    _kw_last_reply[key] = now
    return True


# ======== 用户触发计数（自动Ban用） ========
_user_trigger_history: dict[int, list[float]] = defaultdict(list)
# Bug5修复：把保留窗口从 1 小时改为 24 小时，覆盖前端允许配置的最大窗口（小时级）
_HISTORY_WINDOW = 86400  # 最多保留最近 24 小时记录

def record_trigger(user_id: int):
    """记录用户触发一次，并清理过期记录"""
    now    = time.monotonic()
    cutoff = now - _HISTORY_WINDOW
    _user_trigger_history[user_id].append(now)
    _user_trigger_history[user_id] = [
        t for t in _user_trigger_history[user_id] if t > cutoff
    ]
    # Bug 9 修复：列表清空后删除整个 key，防止内存只增不减
    if not _user_trigger_history[user_id]:
        del _user_trigger_history[user_id]

def get_trigger_count(user_id: int, window_seconds: int) -> int:
    """返回用户在 window_seconds 内的触发次数"""
    now    = time.monotonic()
    cutoff = now - window_seconds
    return sum(1 for t in _user_trigger_history.get(user_id, []) if t > cutoff)


# ======== 群组内用户触发计数（群组屏蔽规则用） ========
# key: (user_id, chat_id) → list[monotonic timestamp]
_group_trigger_history: dict[tuple, list] = defaultdict(list)
_GROUP_HISTORY_WINDOW = 86400  # 保留最近24小时记录

def record_group_trigger(user_id: int, chat_id: int):
    """记录用户在某群组触发一次关键词"""
    now    = time.monotonic()
    cutoff = now - _GROUP_HISTORY_WINDOW
    key = (user_id, chat_id)
    _group_trigger_history[key].append(now)
    _group_trigger_history[key] = [t for t in _group_trigger_history[key] if t > cutoff]
    if not _group_trigger_history[key]:
        del _group_trigger_history[key]

def get_group_trigger_count(user_id: int, chat_id: int) -> int:
    """返回用户在该群组当天触发的次数（以单调时钟计，最近24h内）"""
    now    = time.monotonic()
    cutoff = now - _GROUP_HISTORY_WINDOW
    key = (user_id, chat_id)
    return sum(1 for t in _group_trigger_history.get(key, []) if t > cutoff)

def reset_group_trigger(user_id: int, chat_id: int):
    """解除屏蔽时清零该用户在该群的计数"""
    key = (user_id, chat_id)
    _group_trigger_history.pop(key, None)
