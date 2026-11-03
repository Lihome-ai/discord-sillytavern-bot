"""SQLite 数据库操作：用户记录 + 审核工单 + 资源帖。"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    # 用户表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS members (
            user_id TEXT PRIMARY KEY,
            first_join_time TEXT NOT NULL,
            got_newbie_role INTEGER DEFAULT 0,
            newbie_got_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # 审核工单表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            started_at TEXT,
            created_at TEXT NOT NULL,
            submitted_at TEXT,
            reviewed_at TEXT
        )
    """)
    # 资源帖面板表：记录哪些 Forum 帖子已发过面板
    conn.execute("""
        CREATE TABLE IF NOT EXISTS panels (
            thread_id TEXT PRIMARY KEY,
            message_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # 资源文件表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS resource_files (
            file_id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            version TEXT,
            uploader_id TEXT NOT NULL,
            password TEXT,
            require_like INTEGER DEFAULT 0,
            file_path TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # 领取记录表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            claimed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # 兼容旧库
    member_cols = [r[1] for r in conn.execute("PRAGMA table_info(members)").fetchall()]
    if "newbie_got_at" not in member_cols:
        conn.execute("ALTER TABLE members ADD COLUMN newbie_got_at TEXT")
    ticket_cols = [r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()]
    if "started_at" not in ticket_cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN started_at TEXT")
    # 资源文件表的兼容字段（锁定/删除/审核/延时）
    rf_cols = [r[1] for r in conn.execute("PRAGMA table_info(resource_files)").fetchall()]
    for _int_col in ("is_locked", "is_deleted", "delete_requested", "review_done"):
        if _int_col not in rf_cols:
            conn.execute(f"ALTER TABLE resource_files ADD COLUMN {_int_col} INTEGER DEFAULT 0")
    if "delete_reason" not in rf_cols:
        conn.execute("ALTER TABLE resource_files ADD COLUMN delete_reason TEXT")
    if "delete_at" not in rf_cols:
        conn.execute("ALTER TABLE resource_files ADD COLUMN delete_at TEXT")
    conn.commit()
    conn.close()


# === 用户 ===

def record_new_member(user_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("SELECT first_join_time FROM members WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO members (user_id, first_join_time) VALUES (?, ?)",
        (user_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return True


def mark_got_newbie_role(user_id: str):
    conn = get_conn()
    conn.execute(
        "UPDATE members SET got_newbie_role = 1, newbie_got_at = datetime('now') WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def get_member(user_id: str):
    conn = get_conn()
    cur = conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_expired_newbie_users(days: int = 3):
    conn = get_conn()
    cur = conn.execute("""
        SELECT user_id, newbie_got_at FROM members
        WHERE got_newbie_role = 1 AND newbie_got_at IS NOT NULL
          AND newbie_got_at <= datetime('now', ?)
    """, (f"-{days} days",))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_newbie_flag(user_id: str):
    conn = get_conn()
    conn.execute("UPDATE members SET got_newbie_role = 0, newbie_got_at = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# === 工单 ===

def create_ticket(user_id: str, channel_id: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tickets (user_id, channel_id, status, created_at) VALUES (?, ?, 'open', datetime('now'))",
        (user_id, channel_id)
    )
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


def get_open_ticket(user_id: str):
    conn = get_conn()
    cur = conn.execute(
        "SELECT * FROM tickets WHERE user_id = ? AND status IN ('open','answering','submitted') ORDER BY ticket_id DESC LIMIT 1",
        (user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_ticket_status(ticket_id: int, status: str):
    conn = get_conn()
    conn.execute("UPDATE tickets SET status = ? WHERE ticket_id = ?", (status, ticket_id))
    conn.commit()
    conn.close()


def mark_ticket_started(ticket_id: int):
    conn = get_conn()
    conn.execute("UPDATE tickets SET started_at = datetime('now') WHERE ticket_id = ?", (ticket_id,))
    conn.commit()
    conn.close()


def get_expired_answering_tickets(hours: int = 3):
    conn = get_conn()
    cur = conn.execute("""
        SELECT * FROM tickets WHERE status = 'answering' AND started_at IS NOT NULL
          AND started_at <= datetime('now', ?)
    """, (f"-{hours} hours",))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket_by_channel(channel_id: str):
    conn = get_conn()
    cur = conn.execute("SELECT * FROM tickets WHERE channel_id = ? ORDER BY ticket_id DESC LIMIT 1", (channel_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# === 资源帖面板 ===

def has_panel(thread_id: str) -> bool:
    conn = get_conn()
    cur = conn.execute("SELECT 1 FROM panels WHERE thread_id = ?", (thread_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def set_panel(thread_id: str, message_id: str):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO panels (thread_id, message_id) VALUES (?, ?)", (thread_id, message_id))
    conn.commit()
    conn.close()


def get_panel_message_id(thread_id: str) -> str:
    conn = get_conn()
    cur = conn.execute("SELECT message_id FROM panels WHERE thread_id = ?", (thread_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


# === 资源文件 ===

def add_resource_file(thread_id: str, filename: str, version: str, uploader_id: str,
                      password: str, require_like: int, file_path: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO resource_files (thread_id, filename, version, uploader_id, password, require_like, file_path)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (thread_id, filename, version, uploader_id, password, require_like, file_path)
    )
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid


def get_files_by_thread(thread_id: str):
    conn = get_conn()
    cur = conn.execute("SELECT * FROM resource_files WHERE thread_id = ? ORDER BY file_id DESC", (thread_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_file(file_id: int):
    conn = get_conn()
    cur = conn.execute("SELECT * FROM resource_files WHERE file_id = ?", (file_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# === 领取记录 ===

def add_claim(file_id: int, user_id: str):
    conn = get_conn()
    conn.execute("INSERT INTO claims (file_id, user_id) VALUES (?, ?)", (file_id, user_id))
    conn.commit()
    conn.close()


def get_recent_claims(user_id: str, minutes: int = 5):
    conn = get_conn()
    cur = conn.execute(
        "SELECT COUNT(*) as cnt FROM claims WHERE user_id = ? AND claimed_at >= datetime('now', ?)",
        (user_id, f"-{minutes} minutes")
    )
    row = cur.fetchone()
    conn.close()
    return row["cnt"] if row else 0


def toggle_file_lock(file_id: int) -> bool:
    """切换文件的锁定状态，返回新状态。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT is_locked FROM resource_files WHERE file_id = ?", (file_id,))
    row = c.fetchone()
    if not row:
        return False
    new_state = 0 if row[0] else 1
    c.execute("UPDATE resource_files SET is_locked = ? WHERE file_id = ?", (new_state, file_id))
    conn.commit()
    conn.close()
    return bool(new_state)


def set_file_deleted(file_id: int):
    """标记文件为已删除。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE resource_files SET is_deleted = 1 WHERE file_id = ?", (file_id,))
    conn.commit()
    conn.close()


def set_delete_requested(file_id: int, reason: str):
    """标记文件有删除申请。"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE resource_files SET delete_requested = 1, delete_reason = ? WHERE file_id = ?", (reason, file_id))
    conn.commit()
    conn.close()


# === 审核幂等 + 延时持久化 ===

def try_claim_review(file_id: int) -> bool:
    """原子抢占审核：只有 review_done=0 时能置为1，返回是否抢占成功。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE resource_files SET review_done=1 WHERE file_id=? AND review_done=0",
        (file_id,),
    )
    conn.commit()
    ok = cur.rowcount == 1
    conn.close()
    return ok


def reset_review(file_id: int):
    """重置审核抢占标记（用于延时场景，等待真正执行）。"""
    conn = get_conn()
    conn.execute("UPDATE resource_files SET review_done=0 WHERE file_id=?", (file_id,))
    conn.commit()
    conn.close()


def set_delete_at(file_id: int, when_iso):
    """记录延时删除的目标时间（UTC ISO 字符串）。"""
    conn = get_conn()
    conn.execute("UPDATE resource_files SET delete_at=? WHERE file_id=?", (when_iso, file_id))
    conn.commit()
    conn.close()


def clear_delete_at(file_id: int):
    """清除延时时间标记。"""
    conn = get_conn()
    conn.execute("UPDATE resource_files SET delete_at=NULL WHERE file_id=?", (file_id,))
    conn.commit()
    conn.close()


def get_pending_delays():
    """返回所有未执行的延时任务（delete_at 非空且未删除）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT file_id, delete_at FROM resource_files "
        "WHERE delete_at IS NOT NULL AND is_deleted=0"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
