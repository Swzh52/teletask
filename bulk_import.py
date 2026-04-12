"""
bulk_import.py
批量导入关键词自动回复 与 定时推送任务
支持 .xlsx 和 .csv 两种格式
"""
import csv, io, logging
from datetime import datetime
import database as db

log = logging.getLogger(__name__)

# ============ 列名规范 ============
# 关键词表列：pattern, match, mode, reply_type, reply_text, reply_file_id,
#            reply_caption, delete_after_seconds, expire_after_seconds,
#            start_at, chat_ids, active
#  - chat_ids 支持逗号/空格分隔，例: "-1001234567890,-1009876543210"
#  - 同一 pattern 多行 → 合并成多条回复
#
# 定时任务列：name, chat_id, cron, once, msg_type, msg_text, msg_file_id,
#            msg_caption, delete_after_seconds, active
#  - once 为 1 时 cron 应是具体时间，格式 "YYYY-MM-DD HH:MM:SS"

KW_COLS = ["pattern", "match", "mode", "reply_type", "reply_text", "reply_file_id",
           "reply_caption", "delete_after_seconds", "expire_after_seconds",
           "start_at", "chat_ids", "active"]

SC_COLS = ["name", "chat_id", "cron", "once", "msg_type", "msg_text",
           "msg_file_id", "msg_caption", "delete_after_seconds", "active"]


# ============ 解析辅助 ============
def _safe_int(v, default=None):
    if v is None or v == "":
        return default
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def _safe_str(v, default=""):
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _parse_chat_ids(v):
    """解析 '-1001,-1002' 或 '-1001 -1002' 为 int 列表"""
    if v is None or v == "":
        return []
    s = str(v).replace("，", ",").replace(";", ",").replace(" ", ",")
    ids = []
    for p in s.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            ids.append(int(float(p)))
        except ValueError:
            pass
    return ids


# ============ 读取文件 ============
def _read_rows(filename, content_bytes):
    """返回 list[dict]；列名统一 lower-case strip"""
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext == "csv":
        # 尝试 utf-8-sig（兼容 Excel 导出的 BOM），回退 gbk
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                text = content_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("无法识别 CSV 编码，请保存为 UTF-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for r in reader:
            rows.append({(k or "").strip().lower(): v for k, v in r.items()})
        return rows
    elif ext in ("xlsx", "xls"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise RuntimeError("未安装 openpyxl，请执行: pip install openpyxl")
        wb = load_workbook(io.BytesIO(content_bytes), data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [(str(h).strip().lower() if h is not None else "")
                   for h in next(rows_iter, [])]
        rows = []
        for r in rows_iter:
            if all(v is None or str(v).strip() == "" for v in r):
                continue
            rows.append({headers[i]: r[i] for i in range(min(len(headers), len(r)))})
        return rows
    elif ext == "json":
        import json
        try:
            text = content_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content_bytes.decode("utf-8", errors="replace")
        data = json.loads(text)
        # 允许三种 JSON 顶层结构：
        #   1) [ {...}, {...} ]
        #   2) { "keywords": [ ... ] } 或 { "schedules": [ ... ] }
        #   3) { "data": [ ... ] }
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("keywords") or data.get("schedules") or data.get("data") or []
        else:
            raise ValueError("JSON 顶层必须是数组或对象")
        rows = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # 列名统一 lower-case；chat_ids 如果是数组就 join 成字符串，复用既有解析
            norm = {}
            for k, v in item.items():
                key = (k or "").strip().lower()
                if key == "chat_ids" and isinstance(v, list):
                    v = ",".join(str(x) for x in v)
                norm[key] = v
            rows.append(norm)
        return rows
    else:
        raise ValueError(f"不支持的文件格式: .{ext}，仅支持 .xlsx / .csv / .json")


# ============ 关键词导入 ============
def import_keywords(filename, content_bytes):
    """
    返回 (ok_count, fail_count, errors: list[str])
    相同 pattern 的多行会合并为多条回复（按文件中出现的顺序）
    """
    rows = _read_rows(filename, content_bytes)
    if not rows:
        return 0, 0, ["文件为空"]

    # 按 pattern 分组
    groups = {}        # pattern → list of row dict
    order  = []        # 保持 pattern 首次出现顺序
    for i, r in enumerate(rows, start=2):      # 第2行起（跳表头）
        pattern = _safe_str(r.get("pattern"))
        if not pattern:
            continue
        key = (pattern, _safe_str(r.get("match"), "contains"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        r["_row_num"] = i
        groups[key].append(r)

    ok, fail, errors = 0, 0, []
    for key in order:
        pattern, match = key
        rs = groups[key]
        first = rs[0]
        try:
            replies = []
            for r in rs:
                rtype = _safe_str(r.get("reply_type"), "text")
                replies.append({
                    "reply_type":    rtype,
                    "reply_text":    _safe_str(r.get("reply_text")) or None,
                    "reply_file_id": _safe_str(r.get("reply_file_id")) or None,
                    "reply_caption": _safe_str(r.get("reply_caption")) or None,
                })
            # 去掉完全空的回复
            replies = [r for r in replies
                       if r["reply_text"] or r["reply_file_id"] or r["reply_type"] == "sticker"]
            if not replies:
                fail += 1
                errors.append(f"第{first['_row_num']}行 [{pattern}]: 无有效回复内容")
                continue

            mode      = _safe_str(first.get("mode"), "random")
            das       = _safe_int(first.get("delete_after_seconds"))
            eas       = _safe_int(first.get("expire_after_seconds"))
            start_at  = _safe_str(first.get("start_at")) or None
            chat_ids  = _parse_chat_ids(first.get("chat_ids"))
            active    = _safe_int(first.get("active"), 1)

            kid = db.add_keyword(pattern, match, mode, replies,
                                 delete_after_seconds=das,
                                 expire_after_seconds=eas,
                                 start_at=start_at,
                                 chat_ids=chat_ids)
            if active == 0 and kid:
                db.toggle_keyword(kid)
            ok += 1
        except Exception as e:
            fail += 1
            errors.append(f"第{first['_row_num']}行 [{pattern}]: {e}")
    return ok, fail, errors


# ============ 定时任务导入 ============
def import_schedules(filename, content_bytes):
    rows = _read_rows(filename, content_bytes)
    if not rows:
        return 0, 0, ["文件为空"]

    ok, fail, errors = 0, 0, []
    for i, r in enumerate(rows, start=2):
        name = _safe_str(r.get("name"))
        chat_id = _safe_str(r.get("chat_id"))
        cron = _safe_str(r.get("cron"))
        if not name or not chat_id or not cron:
            # 允许跳过完全空行
            if any(r.get(c) for c in SC_COLS):
                fail += 1
                errors.append(f"第{i}行: name/chat_id/cron 缺失")
            continue
        try:
            once = _safe_int(r.get("once"), 0) == 1
            if once and len(cron) == 16:   # "YYYY-MM-DD HH:MM"
                cron = cron + ":00"
            # cron 格式校验
            if not once and len(cron.split()) != 5:
                fail += 1
                errors.append(f"第{i}行 [{name}]: cron 表达式必须是5个字段")
                continue

            db.add_schedule(
                name=name,
                chat_id=chat_id,
                cron=cron,
                msg_type=_safe_str(r.get("msg_type"), "text"),
                msg_text=_safe_str(r.get("msg_text")) or None,
                msg_file_id=_safe_str(r.get("msg_file_id")) or None,
                msg_caption=_safe_str(r.get("msg_caption")) or None,
                once=1 if once else 0,
                delete_after_seconds=_safe_int(r.get("delete_after_seconds")),
                start_at=None,
            )
            ok += 1
        except Exception as e:
            fail += 1
            errors.append(f"第{i}行 [{name}]: {e}")
    return ok, fail, errors


# ============ 模板导出 ============
def build_keyword_template_csv():
    """生成关键词 CSV 模板，返回 bytes"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(KW_COLS)
    w.writerow(["价格", "contains", "random", "text",
                "我们的价格请咨询客服 <b>@support</b>", "", "",
                "10", "", "", "", "1"])
    w.writerow(["价格", "contains", "random", "text",
                "价目表已更新，请查看置顶。", "", "",
                "10", "", "", "", "1"])
    w.writerow(["早安", "exact", "random", "text",
                "早上好 🌞", "", "", "0", "", "", "-1001234567890", "1"])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def build_schedule_template_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(SC_COLS)
    w.writerow(["早报推送", "-1001234567890", "0 9 * * *", "0", "text",
                "<b>早安！</b>新的一天开始啦", "", "", "0", "1"])
    w.writerow(["新年祝福", "-1001234567890", "2026-01-01 00:00:00", "1", "text",
                "🎉 新年快乐！", "", "", "0", "1"])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def build_keyword_template_json():
    import json
    data = {
        "keywords": [
            {
                "pattern": "价格",
                "match": "contains",
                "mode": "random",
                "reply_type": "text",
                "reply_text": "我们的价格请咨询客服 <b>@support</b>",
                "delete_after_seconds": 10,
                "chat_ids": [],
                "active": 1
            },
            {
                "pattern": "价格",
                "match": "contains",
                "mode": "random",
                "reply_type": "text",
                "reply_text": "价目表已更新，请查看置顶。",
                "delete_after_seconds": 10,
                "active": 1
            },
            {
                "pattern": "早安",
                "match": "exact",
                "mode": "random",
                "reply_type": "text",
                "reply_text": "早上好 🌞",
                "chat_ids": [-1001234567890],
                "active": 1
            }
        ]
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def build_schedule_template_json():
    import json
    data = {
        "schedules": [
            {
                "name": "早报推送",
                "chat_id": "-1001234567890",
                "cron": "0 9 * * *",
                "once": 0,
                "msg_type": "text",
                "msg_text": "<b>早安！</b>新的一天开始啦",
                "delete_after_seconds": 0,
                "active": 1
            },
            {
                "name": "新年祝福",
                "chat_id": "-1001234567890",
                "cron": "2026-01-01 00:00:00",
                "once": 1,
                "msg_type": "text",
                "msg_text": "🎉 新年快乐！",
                "active": 1
            }
        ]
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
