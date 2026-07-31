# -*- coding: utf-8 -*-
"""
لایهٔ دیتابیس ربات مرخصی — با پشتیبانی از مناطق کاری (regions).

سلسله‌مراتب:
  مدیر (is_admin)
    └── منطقه کاری (regions)
          └── تکنسین ارشد (is_senior + region_id)
                └── گروه کاری (groups.region_id + color)
                      └── اعضا (users)

سیاست انتقال:
  - جابه‌جایی منطقه → فقط مرخصی‌های آینده حذف می‌شوند
  - تغییر گروه (همان منطقه) → مرخصی‌ها دست‌نخورده؛ رنگ/ظرفیت گروه جدید اعمال می‌شود
"""
from __future__ import annotations

import sqlite3
import contextlib
import datetime
import json
from typing import Optional

import config

try:
    from cache import (
        cache,
        inv_settings,
        inv_regions,
        inv_region,
        inv_group,
        inv_user,
        inv_leaves_month,
    )
except ImportError:
    # در صورت نبود کش، no-op
    class _NoCache:
        def get(self, *a, **k): return None
        def set(self, *a, **k): pass
        def delete(self, *a, **k): pass
        def delete_prefix(self, *a, **k): pass
        def get_or_set(self, key, factory, ttl=None): return factory()

    cache = _NoCache()

    def inv_settings(): pass
    def inv_regions(): pass
    def inv_region(region_id): pass
    def inv_group(group_id, region_id): pass
    def inv_user(user_id): pass
    def inv_leaves_month(region_id, year, month): pass


# ---------------------------------------------------------------------------
# اسکیما
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS regions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id       INTEGER,
    name            TEXT NOT NULL,
    max_concurrent  INTEGER NOT NULL DEFAULT 1,
    color           TEXT NOT NULL DEFAULT '#4fa9a2',
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(region_id, name),
    FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    user_id            INTEGER PRIMARY KEY,
    bale_first_name    TEXT,
    bale_username      TEXT,
    first_name         TEXT,
    last_name          TEXT,
    personnel_number   TEXT,
    role               TEXT,
    region_id          INTEGER,
    group_id           INTEGER,
    shift_index        INTEGER,
    is_admin           INTEGER NOT NULL DEFAULT 0,
    is_senior          INTEGER NOT NULL DEFAULT 0,
    is_shift_lead      INTEGER NOT NULL DEFAULT 0,
    approved           INTEGER NOT NULL DEFAULT 0,
    profile_complete   INTEGER NOT NULL DEFAULT 0,
    joined_at          TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (region_id) REFERENCES regions(id),
    FOREIGN KEY (group_id)  REFERENCES groups(id)
);

CREATE TABLE IF NOT EXISTS shift_lead_regions (
    user_id    INTEGER NOT NULL,
    region_id  INTEGER NOT NULL,
    PRIMARY KEY (user_id, region_id),
    FOREIGN KEY (user_id)   REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS invites (
    token       TEXT PRIMARY KEY,
    role        TEXT,
    region_id   INTEGER,
    group_id    INTEGER,
    is_senior   INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER,
    used_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (region_id) REFERENCES regions(id),
    FOREIGN KEY (group_id)  REFERENCES groups(id)
);

CREATE TABLE IF NOT EXISTS leaves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    leave_date      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    batch_id        TEXT,
    note_user       TEXT,
    note_admin      TEXT,
    requested_at    TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      INTEGER,
    UNIQUE(user_id, leave_date),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_region      ON users(region_id);
CREATE INDEX IF NOT EXISTS idx_users_group       ON users(group_id);
CREATE INDEX IF NOT EXISTS idx_users_senior      ON users(is_senior, region_id);
CREATE INDEX IF NOT EXISTS idx_users_shift_lead  ON users(is_shift_lead);
CREATE INDEX IF NOT EXISTS idx_slr_user          ON shift_lead_regions(user_id);
CREATE INDEX IF NOT EXISTS idx_slr_region        ON shift_lead_regions(region_id);
CREATE INDEX IF NOT EXISTS idx_groups_region     ON groups(region_id);
CREATE INDEX IF NOT EXISTS idx_leaves_date       ON leaves(leave_date);
CREATE INDEX IF NOT EXISTS idx_leaves_user       ON leaves(user_id);
CREATE INDEX IF NOT EXISTS idx_leaves_batch      ON leaves(batch_id);
CREATE INDEX IF NOT EXISTS idx_leaves_status     ON leaves(status);
"""

DEFAULT_REGION_NAME = "منطقه پیش‌فرض"
DEFAULT_GROUP_NAME = "گروه پیش‌فرض"
DEFAULT_GROUP_COLOR = "#4fa9a2"
ACTIVE_STATUSES = ("pending", "reviewing", "approved")


@contextlib.contextmanager
def _conn():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _today_jalali_str() -> str:
    import jalali
    y, m, d = jalali.today_jalali()
    return jalali.parse_date_str(y, m, d)


# ---------------------------------------------------------------------------
# مهاجرت نرم از نسخه‌های قدیمی
# ---------------------------------------------------------------------------

def _column_exists(con, table: str, column: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _migrate(con):
    """ستون‌ها/جداول جدید را به دیتابیس‌های قدیمی اضافه می‌کند."""
    # regions ممکن است از قبل نباشد
    con.executescript("""
        CREATE TABLE IF NOT EXISTS regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # groups: region_id, color
    if not _column_exists(con, "groups", "region_id"):
        con.execute("ALTER TABLE groups ADD COLUMN region_id INTEGER")
    if not _column_exists(con, "groups", "color"):
        con.execute(f"ALTER TABLE groups ADD COLUMN color TEXT NOT NULL DEFAULT '{DEFAULT_GROUP_COLOR}'")

    # users: region_id, is_senior, is_shift_lead
    if not _column_exists(con, "users", "region_id"):
        con.execute("ALTER TABLE users ADD COLUMN region_id INTEGER")
    if not _column_exists(con, "users", "is_senior"):
        con.execute("ALTER TABLE users ADD COLUMN is_senior INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(con, "users", "is_shift_lead"):
        con.execute("ALTER TABLE users ADD COLUMN is_shift_lead INTEGER NOT NULL DEFAULT 0")

    con.executescript("""
        CREATE TABLE IF NOT EXISTS shift_lead_regions (
            user_id    INTEGER NOT NULL,
            region_id  INTEGER NOT NULL,
            PRIMARY KEY (user_id, region_id),
            FOREIGN KEY (user_id)   REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (region_id) REFERENCES regions(id) ON DELETE CASCADE
        );
    """)

    # leaves: batch_id, note_user, note_admin, decided_by
    for col, decl in [
        ("batch_id", "TEXT"),
        ("note_user", "TEXT"),
        ("note_admin", "TEXT"),
        ("decided_by", "INTEGER"),
    ]:
        if not _column_exists(con, "leaves", col):
            con.execute(f"ALTER TABLE leaves ADD COLUMN {col} {decl}")

    # invites: region_id, is_senior
    if not _column_exists(con, "invites", "region_id"):
        con.execute("ALTER TABLE invites ADD COLUMN region_id INTEGER")
    if not _column_exists(con, "invites", "is_senior"):
        con.execute("ALTER TABLE invites ADD COLUMN is_senior INTEGER NOT NULL DEFAULT 0")

    # اگر گروهی بدون منطقه مانده، منطقه پیش‌فرض بساز و وصل کن
    has_orphan = con.execute(
        "SELECT 1 FROM groups WHERE region_id IS NULL LIMIT 1"
    ).fetchone()
    if has_orphan:
        row = con.execute(
            "SELECT id FROM regions WHERE name = ?", (DEFAULT_REGION_NAME,)
        ).fetchone()
        if row:
            rid = row["id"]
        else:
            cur = con.execute(
                "INSERT INTO regions (name) VALUES (?)", (DEFAULT_REGION_NAME,)
            )
            rid = cur.lastrowid
        con.execute(
            "UPDATE groups SET region_id = ? WHERE region_id IS NULL", (rid,)
        )
        con.execute(
            "UPDATE users SET region_id = ? WHERE region_id IS NULL AND group_id IS NOT NULL",
            (rid,),
        )


def init_db():
    with _conn() as con:
        con.executescript(SCHEMA)
        _migrate(con)


# ============================================================== تنظیمات ====

def get_setting(key: str, default=None):
    cached = cache.get("settings:all")
    if cached is not None and key in cached:
        return cached[key]
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with _conn() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    inv_settings()


def get_calendar_mode() -> str:
    return get_setting("calendar_mode", "workday")


def get_shift_config():
    required = [
        "shift_count", "cycle_length", "shift_labels_json",
        "ref_date", "ref_shift_index", "ref_slot_index",
    ]
    with _conn() as con:
        placeholders = ",".join("?" for _ in required)
        rows = con.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})", required
        ).fetchall()
    data = {r["key"]: r["value"] for r in rows}
    if not all(k in data for k in required):
        return None
    return {
        "shift_count": int(data["shift_count"]),
        "cycle_length": int(data["cycle_length"]),
        "labels": json.loads(data["shift_labels_json"]),
        "ref_date": data["ref_date"],
        "ref_shift_index": int(data["ref_shift_index"]),
        "ref_slot_index": int(data["ref_slot_index"]),
    }


def save_shift_config(shift_count, cycle_length, labels, ref_date, ref_shift_index, ref_slot_index):
    set_setting("shift_count", shift_count)
    set_setting("cycle_length", cycle_length)
    set_setting("shift_labels_json", json.dumps(labels, ensure_ascii=False))
    set_setting("ref_date", ref_date)
    set_setting("ref_shift_index", ref_shift_index)
    set_setting("ref_slot_index", ref_slot_index)


# ============================================================== مناطق ====

def create_region(name: str) -> Optional[int]:
    with _conn() as con:
        try:
            cur = con.execute("INSERT INTO regions (name) VALUES (?)", (name.strip(),))
            rid = cur.lastrowid
        except sqlite3.IntegrityError:
            return None
    inv_regions()
    return rid


def list_regions():
    hit = cache.get("regions:all")
    if hit is not None:
        return hit
    with _conn() as con:
        rows = con.execute("SELECT * FROM regions ORDER BY name").fetchall()
        result = [dict(r) for r in rows]
    cache.set("regions:all", result)
    return result


def get_region(region_id: int):
    key = f"regions:{region_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _conn() as con:
        row = con.execute("SELECT * FROM regions WHERE id = ?", (region_id,)).fetchone()
        result = dict(row) if row else None
    if result:
        cache.set(key, result)
    return result


def rename_region(region_id: int, name: str) -> bool:
    with _conn() as con:
        try:
            cur = con.execute(
                "UPDATE regions SET name = ? WHERE id = ?", (name.strip(), region_id)
            )
            ok = cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False
    if ok:
        inv_region(region_id)
    return ok


def delete_region(region_id: int) -> bool:
    """حذف منطقه فقط اگر عضو و گروه نداشته باشد."""
    with _conn() as con:
        n_users = con.execute(
            "SELECT COUNT(*) AS c FROM users WHERE region_id = ?", (region_id,)
        ).fetchone()["c"]
        n_groups = con.execute(
            "SELECT COUNT(*) AS c FROM groups WHERE region_id = ?", (region_id,)
        ).fetchone()["c"]
        if n_users or n_groups:
            return False
        cur = con.execute("DELETE FROM regions WHERE id = ?", (region_id,))
        ok = cur.rowcount > 0
    if ok:
        inv_region(region_id)
    return ok


# ============================================================== گروه‌ها ====

def create_group(name: str, max_concurrent: int, region_id: int,
                 color: str = DEFAULT_GROUP_COLOR) -> Optional[int]:
    with _conn() as con:
        try:
            cur = con.execute(
                "INSERT INTO groups (name, max_concurrent, region_id, color) VALUES (?, ?, ?, ?)",
                (name.strip(), max_concurrent, region_id, color),
            )
            gid = cur.lastrowid
        except sqlite3.IntegrityError:
            return None
    inv_group(gid, region_id)
    return gid


def list_groups(region_id: Optional[int] = None):
    if region_id is not None:
        key = f"groups:region:{region_id}"
        hit = cache.get(key)
        if hit is not None:
            return hit
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM groups WHERE region_id = ? ORDER BY name", (region_id,)
            ).fetchall()
            result = [dict(r) for r in rows]
        cache.set(key, result)
        return result
    with _conn() as con:
        rows = con.execute("SELECT * FROM groups ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def get_group(group_id: int):
    key = f"groups:{group_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _conn() as con:
        row = con.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        result = dict(row) if row else None
    if result:
        cache.set(key, result)
    return result


def update_group_capacity(group_id: int, max_concurrent: int):
    g = get_group(group_id)
    with _conn() as con:
        con.execute(
            "UPDATE groups SET max_concurrent = ? WHERE id = ?",
            (max_concurrent, group_id),
        )
    if g:
        inv_group(group_id, g.get("region_id"))


def update_group_color(group_id: int, color: str):
    g = get_group(group_id)
    with _conn() as con:
        con.execute("UPDATE groups SET color = ? WHERE id = ?", (color, group_id))
    if g:
        inv_group(group_id, g.get("region_id"))


def rename_group(group_id: int, name: str) -> bool:
    g = get_group(group_id)
    with _conn() as con:
        try:
            cur = con.execute(
                "UPDATE groups SET name = ? WHERE id = ?", (name.strip(), group_id)
            )
            ok = cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False
    if ok and g:
        inv_group(group_id, g.get("region_id"))
    return ok


def delete_group(group_id: int) -> bool:
    """حذف گروه فقط اگر عضوی نداشته باشد."""
    g = get_group(group_id)
    with _conn() as con:
        n = con.execute(
            "SELECT COUNT(*) AS c FROM users WHERE group_id = ?", (group_id,)
        ).fetchone()["c"]
        if n:
            return False
        cur = con.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        ok = cur.rowcount > 0
    if ok and g:
        inv_group(group_id, g.get("region_id"))
    return ok


# ============================================================== کاربران ====

def get_user(user_id: int):
    key = f"user:{user_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        result = dict(row) if row else None
    if result:
        cache.set(key, result, ttl=120)
    return result


def any_admin_exists() -> bool:
    with _conn() as con:
        row = con.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
        return row is not None


def touch_user_bale_info(user_id: int, bale_first_name, bale_username):
    with _conn() as con:
        con.execute(
            """
            INSERT INTO users (user_id, bale_first_name, bale_username)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                bale_first_name = excluded.bale_first_name,
                bale_username = excluded.bale_username
            """,
            (user_id, bale_first_name, bale_username),
        )
    inv_user(user_id)


def try_claim_admin(user_id: int):
    """
    اولین نفری که ثبت‌نام کامل می‌کند مدیر می‌شود.
    یک منطقه و گروه پیش‌فرض می‌سازد و او را به آن‌ها وصل می‌کند.
    خروجی: (claimed: bool, group_id | None)
    """
    con = sqlite3.connect(config.DB_PATH, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        exists = con.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
        if exists:
            con.execute("COMMIT")
            return False, None

        # منطقه پیش‌فرض
        rrow = con.execute(
            "SELECT id FROM regions WHERE name = ?", (DEFAULT_REGION_NAME,)
        ).fetchone()
        if rrow:
            rid = rrow["id"]
        else:
            cur = con.execute(
                "INSERT INTO regions (name) VALUES (?)", (DEFAULT_REGION_NAME,)
            )
            rid = cur.lastrowid

        # گروه پیش‌فرض
        grow = con.execute(
            "SELECT id FROM groups WHERE name = ? AND region_id = ?",
            (DEFAULT_GROUP_NAME, rid),
        ).fetchone()
        if grow:
            gid = grow["id"]
        else:
            cur = con.execute(
                "INSERT INTO groups (name, max_concurrent, region_id, color) VALUES (?, 1, ?, ?)",
                (DEFAULT_GROUP_NAME, rid, DEFAULT_GROUP_COLOR),
            )
            gid = cur.lastrowid

        con.execute(
            """
            UPDATE users
            SET is_admin = 1, approved = 1, role = 'snr',
                region_id = ?, group_id = ?, is_senior = 0
            WHERE user_id = ?
            """,
            (rid, gid, user_id),
        )
        con.execute("COMMIT")
        inv_regions()
        inv_user(user_id)
        return True, gid
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def list_admin_ids():
    with _conn() as con:
        rows = con.execute("SELECT user_id FROM users WHERE is_admin = 1").fetchall()
        return [r["user_id"] for r in rows]


def list_seniors_by_region():
    """[{user_id, region_id, region_name}, ...] برای گزارش ماهانه."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT u.user_id, u.region_id, r.name AS region_name
            FROM users u
            JOIN regions r ON r.id = u.region_id
            WHERE u.is_senior = 1 AND u.approved = 1
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_seniors_by_group():
    """[{user_id, group_id, group_name, region_name}, ...] — ارشد سطح گروه است."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT u.user_id, u.group_id, g.name AS group_name, r.name AS region_name
            FROM users u
            JOIN groups g ON g.id = u.group_id
            LEFT JOIN regions r ON r.id = g.region_id
            WHERE u.is_senior = 1 AND u.approved = 1
            """
        ).fetchall()
        return [dict(r) for r in rows]


def set_user_shift(user_id: int, shift_index):
    with _conn() as con:
        con.execute(
            "UPDATE users SET shift_index = ? WHERE user_id = ?",
            (shift_index, user_id),
        )
    inv_user(user_id)


def complete_profile(user_id: int, first_name: str, last_name: str, personnel_number: str):
    with _conn() as con:
        con.execute(
            """
            UPDATE users
            SET first_name = ?, last_name = ?, personnel_number = ?, profile_complete = 1
            WHERE user_id = ?
            """,
            (first_name, last_name, personnel_number, user_id),
        )
    inv_user(user_id)


def list_pending_users():
    with _conn() as con:
        rows = con.execute(
            """
            SELECT * FROM users
            WHERE approved = 0 AND is_admin = 0 AND profile_complete = 1
            ORDER BY joined_at
            """
        ).fetchall()
        return [dict(r) for r in rows]


def list_pending_users_in_regions(region_ids: list):
    """
    فعلاً هیچ فیلد «منطقه‌ی درخواستی» در فرم ثبت‌نام نیست، پس نمی‌دانیم کاربرِ در
    انتظار دقیقاً برای کدام منطقه است. به همین دلیل همان فهرست کامل را برمی‌گردانیم؛
    محدودیتِ واقعیِ دسترسی هنگام «تعیین گروه» اعمال می‌شود (فقط گروه‌های همان
    مناطقی که این مسئول شیفت/ارشد اجازه‌ی مدیریتشان را دارد نشان داده می‌شود).
    """
    if not region_ids:
        return []
    return list_pending_users()


def approve_user(user_id: int, role: str, group_id: int, shift_index=None,
                 region_id: Optional[int] = None, is_senior: int = 0):
    if region_id is None and group_id is not None:
        g = get_group(group_id)
        region_id = g["region_id"] if g else None
    with _conn() as con:
        con.execute(
            """
            UPDATE users
            SET role = ?, group_id = ?, region_id = ?, shift_index = ?,
                is_senior = ?, approved = 1
            WHERE user_id = ?
            """,
            (role, group_id, region_id, shift_index, 1 if is_senior else 0, user_id),
        )
    inv_user(user_id)


def get_max_seniors_per_region() -> int:
    raw = get_setting("max_seniors_per_region", "2")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def set_max_seniors_per_region(n: int):
    set_setting("max_seniors_per_region", max(1, int(n)))


def set_senior(user_id: int, is_senior: bool):
    """
    تعیین/لغو تکنسین ارشد. سقفِ تعداد ارشدها در سطح «منطقه» اعمال می‌شود
    (پیش‌فرض ۲ نفر در هر منطقه، از تنظیمات قابل تغییر) — محدوده‌ی مدیریتیِ
    هر ارشد همچنان فقط گروهِ خودش است (users.group_id)، فقط سقفِ *تعداد*
    ارشدها منطقه‌ای است، نه یک‌نفره در هر گروه.

    خروجی اتمیک (با قفل نوشتاری فوری تا رقابت هم‌زمان مشکلی ایجاد نکند):
        ("ok", None)       -> ثبت شد
        ("full", count)    -> سقف منطقه پر است (count = تعداد ارشدهای فعلی منطقه)
        ("no_group", None) -> کاربر هنوز به هیچ گروهی اختصاص داده نشده
    """
    con = sqlite3.connect(config.DB_PATH, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        if is_senior:
            row = con.execute("SELECT group_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
            group_id = row["group_id"] if row else None
            if not group_id:
                con.execute("COMMIT")
                return "no_group", None
            grp = con.execute("SELECT region_id FROM groups WHERE id = ?", (group_id,)).fetchone()
            region_id = grp["region_id"] if grp else None
            existing = con.execute(
                """
                SELECT u.user_id FROM users u
                JOIN groups g ON g.id = u.group_id
                WHERE g.region_id = ? AND u.is_senior = 1 AND u.user_id != ?
                """,
                (region_id, user_id),
            ).fetchall()
            cap_row = con.execute(
                "SELECT value FROM settings WHERE key = 'max_seniors_per_region'"
            ).fetchone()
            cap = int(cap_row["value"]) if cap_row and cap_row["value"] else 2
            if len(existing) >= cap:
                con.execute("COMMIT")
                return "full", len(existing)
        con.execute("UPDATE users SET is_senior = ? WHERE user_id = ?", (1 if is_senior else 0, user_id))
        con.execute("COMMIT")
        inv_user(user_id)
        return "ok", None
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def can_manage_group(user_id: int, group_id: int) -> bool:
    """مدیر → همه‌ی گروه‌ها. مسئول شیفت → گروه‌های داخل مناطق تحت مدیریتش. ارشد → فقط گروهِ خودش."""
    u = get_user(user_id)
    if not u or not group_id:
        return False
    if u.get("is_admin"):
        return True
    group = get_group(group_id)
    if not group:
        return False
    if u.get("is_shift_lead") and group.get("region_id") in list_shift_lead_region_ids(user_id):
        return True
    if u.get("is_senior") and u.get("group_id") == group_id:
        return True
    return False


def list_all_active_users(region_id: Optional[int] = None):
    q = """
        SELECT u.*, g.name AS group_name, g.color AS group_color,
               r.name AS region_name
        FROM users u
        LEFT JOIN groups g ON g.id = u.group_id
        LEFT JOIN regions r ON r.id = u.region_id
        WHERE u.approved = 1 AND u.is_admin = 0
    """
    params: list = []
    if region_id is not None:
        q += " AND u.region_id = ?"
        params.append(region_id)
    q += " ORDER BY r.name, g.name, u.first_name"
    with _conn() as con:
        rows = con.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def change_user_group(user_id: int, new_group_id: int) -> dict:
    """
    تغییر گروه داخل همان منطقه.
    مرخصی‌ها حذف نمی‌شوند؛ فقط group_id عوض می‌شود تا رنگ/ظرفیت جدید اعمال شود.
    """
    u = get_user(user_id)
    if not u:
        raise ValueError("user not found")
    g = get_group(new_group_id)
    if not g:
        raise ValueError("group not found")
    if u.get("region_id") is not None and g.get("region_id") != u["region_id"]:
        raise ValueError("group is not in the user's region — use move_user_to_region")

    old_gid = u.get("group_id")
    with _conn() as con:
        con.execute(
            "UPDATE users SET group_id = ? WHERE user_id = ?",
            (new_group_id, user_id),
        )
    inv_user(user_id)
    if old_gid:
        old_g = get_group(old_gid)
        if old_g:
            inv_group(old_gid, old_g.get("region_id"))
    inv_group(new_group_id, g.get("region_id"))
    return {
        "user_id": user_id,
        "old_group_id": old_gid,
        "new_group_id": new_group_id,
        "leaves_touched": False,
    }


def move_user_to_region(
    user_id: int,
    new_region_id: int,
    new_group_id: Optional[int] = None,
    today: Optional[str] = None,
) -> dict:
    """
    جابه‌جایی به منطقه دیگر.
    فقط مرخصی‌های آینده (leave_date >= today) حذف می‌شوند.
    is_senior صفر می‌شود.
    """
    today = today or _today_jalali_str()
    u = get_user(user_id)
    if not u:
        raise ValueError("user not found")
    if not get_region(new_region_id):
        raise ValueError("region not found")
    if new_group_id is not None:
        g = get_group(new_group_id)
        if not g or g.get("region_id") != new_region_id:
            raise ValueError("group does not belong to target region")

    old_region = u.get("region_id")
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM leaves WHERE user_id = ? AND leave_date >= ?",
            (user_id, today),
        )
        deleted = cur.rowcount
        con.execute(
            """
            UPDATE users
            SET region_id = ?, group_id = ?, is_senior = 0, shift_index = NULL
            WHERE user_id = ?
            """,
            (new_region_id, new_group_id, user_id),
        )
    inv_user(user_id)
    if old_region is not None:
        inv_region(old_region)
    inv_region(new_region_id)
    return {
        "user_id": user_id,
        "old_region_id": old_region,
        "new_region_id": new_region_id,
        "new_group_id": new_group_id,
        "deleted_future_leaves": deleted,
    }


def remove_user_from_system(
    user_id: int,
    today: Optional[str] = None,
    keep_past_leaves: bool = True,
) -> dict:
    today = today or _today_jalali_str()
    u = get_user(user_id)
    old_region = u.get("region_id") if u else None
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM leaves WHERE user_id = ? AND leave_date >= ?",
            (user_id, today),
        )
        deleted_future = cur.rowcount
        if not keep_past_leaves:
            con.execute("DELETE FROM leaves WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    inv_user(user_id)
    if old_region is not None:
        inv_region(old_region)
    return {
        "user_id": user_id,
        "old_region_id": old_region,
        "deleted_future_leaves": deleted_future,
    }


# ============================================================== دعوت‌ها ====

def create_invite(token: str, role, group_id, created_by: int,
                  region_id: Optional[int] = None, is_senior: int = 0):
    if region_id is None and group_id is not None:
        g = get_group(group_id)
        region_id = g["region_id"] if g else None
    with _conn() as con:
        con.execute(
            """
            INSERT INTO invites (token, role, group_id, region_id, is_senior, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token, role, group_id, region_id, is_senior, created_by),
        )


def get_invite(token: str):
    with _conn() as con:
        row = con.execute("SELECT * FROM invites WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def increment_invite_use(token: str):
    with _conn() as con:
        con.execute(
            "UPDATE invites SET used_count = used_count + 1 WHERE token = ?", (token,)
        )


# ============================================================== مرخصی‌ها ====

def get_leave(leave_id: int):
    with _conn() as con:
        row = con.execute("SELECT * FROM leaves WHERE id = ?", (leave_id,)).fetchone()
        return dict(row) if row else None


def get_user_leave_on_date(user_id: int, date_str: str):
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM leaves WHERE user_id = ? AND leave_date = ?",
            (user_id, date_str),
        ).fetchone()
        return dict(row) if row else None


def request_leave(user_id: int, date_str: str, note_user: str = None, batch_id: str = None):
    """
    ثبت درخواست (pending). ظرفیت فقط در تایید نهایی چک می‌شود.
    """
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM leaves WHERE user_id = ? AND leave_date = ?",
            (user_id, date_str),
        ).fetchone()
        now = now_iso()
        if row is None:
            con.execute(
                """
                INSERT INTO leaves (user_id, leave_date, status, requested_at, note_user, batch_id)
                VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (user_id, date_str, now, note_user, batch_id),
            )
            cur = con.execute("SELECT last_insert_rowid() AS id").fetchone()
            leave_id = cur["id"]
            result = ("created", leave_id)
        elif row["status"] in ACTIVE_STATUSES:
            result = ("exists", row["id"])
        else:
            con.execute(
                """
                UPDATE leaves
                SET status = 'pending', requested_at = ?, decided_at = NULL,
                    note_user = ?, note_admin = NULL, decided_by = NULL, batch_id = ?
                WHERE id = ?
                """,
                (now, note_user, batch_id, row["id"]),
            )
            result = ("created", row["id"])
    u = get_user(user_id)
    if u and u.get("region_id") is not None:
        inv_region(u["region_id"])
    inv_user(user_id)
    return result


def try_set_status(leave_id: int, new_status: str, decided_by: int = None,
                   note_admin: str = None):
    """
    تغییر وضعیت. برای approved ظرفیت گروه چک می‌شود.
    خروجی: ("ok", None) | ("full", owners_list) | ("not_found", None)
    """
    con = sqlite3.connect(config.DB_PATH, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        leave = con.execute("SELECT * FROM leaves WHERE id = ?", (leave_id,)).fetchone()
        if leave is None:
            con.execute("COMMIT")
            return "not_found", None

        if new_status == "approved":
            user = con.execute(
                "SELECT group_id, region_id FROM users WHERE user_id = ?",
                (leave["user_id"],),
            ).fetchone()
            if not user or not user["group_id"]:
                con.execute("COMMIT")
                return "not_found", None
            group = con.execute(
                "SELECT max_concurrent FROM groups WHERE id = ?",
                (user["group_id"],),
            ).fetchone()
            owners = con.execute(
                """
                SELECT u.user_id, u.first_name, u.last_name
                FROM leaves l JOIN users u ON u.user_id = l.user_id
                WHERE u.group_id = ? AND l.leave_date = ? AND l.status = 'approved' AND l.id != ?
                """,
                (user["group_id"], leave["leave_date"], leave_id),
            ).fetchall()
            if group and len(owners) >= group["max_concurrent"]:
                con.execute("COMMIT")
                return "full", [dict(o) for o in owners]

        con.execute(
            """
            UPDATE leaves
            SET status = ?, decided_at = ?, decided_by = ?,
                note_admin = COALESCE(?, note_admin)
            WHERE id = ?
            """,
            (new_status, now_iso(), decided_by, note_admin, leave_id),
        )
        con.execute("COMMIT")

        # invalidate
        urow = None
        with _conn() as c2:
            urow = c2.execute(
                "SELECT region_id FROM users WHERE user_id = ?", (leave["user_id"],)
            ).fetchone()
        if urow and urow["region_id"] is not None:
            inv_region(urow["region_id"])
        inv_user(leave["user_id"])
        return "ok", None
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def cancel_leave(leave_id: int) -> bool:
    leave = get_leave(leave_id)
    with _conn() as con:
        cur = con.execute("DELETE FROM leaves WHERE id = ?", (leave_id,))
        ok = cur.rowcount > 0
    if ok and leave:
        u = get_user(leave["user_id"])
        if u and u.get("region_id") is not None:
            inv_region(u["region_id"])
        inv_user(leave["user_id"])
    return ok


def list_user_active_leaves(user_id: int, from_date_str: str = None):
    q = "SELECT * FROM leaves WHERE user_id = ? AND status IN ('pending','reviewing','approved')"
    params: list = [user_id]
    if from_date_str:
        q += " AND leave_date >= ?"
        params.append(from_date_str)
    q += " ORDER BY leave_date"
    with _conn() as con:
        rows = con.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def group_active_leaves_in_month(group_id: int, year_month_prefix: str):
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.leave_date, l.status, l.user_id
            FROM leaves l JOIN users u ON u.user_id = l.user_id
            WHERE u.group_id = ? AND l.leave_date LIKE ?
              AND l.status IN ('pending','reviewing','approved')
            """,
            (group_id, year_month_prefix + "-%"),
        ).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["leave_date"], []).append(
            {"user_id": r["user_id"], "status": r["status"]}
        )
    return out


def group_leaves_in_month(group_id: int, year: int, month: int):
    """مرخصی‌های یک گروه مشخص در یک ماه — با کش."""
    ym = f"{year:04d}-{month:02d}"
    key = f"leaves:month:group:{group_id}:{ym}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, u.group_id,
                   g.name AS group_name, g.color AS group_color,
                   r.name AS region_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN regions r ON r.id = g.region_id
            WHERE u.group_id = ? AND l.leave_date LIKE ?
            ORDER BY l.leave_date
            """,
            (group_id, ym + "-%"),
        ).fetchall()
    result = [dict(r) for r in rows]
    cache.set(key, result, ttl=120)
    return result


def region_leaves_in_month(region_id: int, year: int, month: int):
    """مرخصی‌های یک منطقه در یک ماه — با کش."""
    ym = f"{year:04d}-{month:02d}"
    key = f"leaves:month:{region_id}:{ym}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, u.group_id,
                   g.name AS group_name, g.color AS group_color,
                   r.name AS region_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN regions r ON r.id = u.region_id
            WHERE u.region_id = ? AND l.leave_date LIKE ?
              AND l.status IN ('pending','reviewing','approved','rejected')
            ORDER BY l.leave_date
            """,
            (region_id, ym + "-%"),
        ).fetchall()
        result = [dict(r) for r in rows]
    cache.set(key, result, ttl=60)
    return result


def all_leaves_in_month(year: int, month: int):
    """همه مناطق — فقط برای مدیر."""
    ym = f"{year:04d}-{month:02d}"
    key = f"leaves:month:all:{ym}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, u.group_id, u.region_id,
                   g.name AS group_name, g.color AS group_color,
                   r.name AS region_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN regions r ON r.id = u.region_id
            WHERE l.leave_date LIKE ?
              AND l.status IN ('pending','reviewing','approved','rejected')
            ORDER BY l.leave_date
            """,
            (ym + "-%",),
        ).fetchall()
        result = [dict(r) for r in rows]
    cache.set(key, result, ttl=60)
    return result


def list_group_leaves(group_id: int, from_date_str: str = None, limit: int = 100):
    q = """
        SELECT l.*, u.first_name, u.last_name
        FROM leaves l JOIN users u ON u.user_id = l.user_id
        WHERE u.group_id = ? AND l.status IN ('pending','reviewing','approved')
    """
    params: list = [group_id]
    if from_date_str:
        q += " AND l.leave_date >= ?"
        params.append(from_date_str)
    q += " ORDER BY l.leave_date LIMIT ?"
    params.append(limit)
    with _conn() as con:
        rows = con.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def list_pending_for_senior(region_id: int, limit: int = 100):
    """صف تایید تکنسین‌ارشد: درخواست اعضای منطقه (غیر از خود ارشدها و مدیر)."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, g.name AS group_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            WHERE u.region_id = ?
              AND u.is_senior = 0 AND u.is_admin = 0
              AND l.status IN ('pending', 'reviewing')
            ORDER BY l.requested_at
            LIMIT ?
            """,
            (region_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def list_pending_for_admin(limit: int = 100):
    """صف تایید مدیر: فقط درخواست تکنسین‌های ارشد."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, g.name AS group_name,
                   r.name AS region_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN regions r ON r.id = u.region_id
            WHERE u.is_senior = 1
              AND l.status IN ('pending', 'reviewing')
            ORDER BY l.requested_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_pending_review_queue(limit: int = 100):
    """سازگاری با کد قدیمی — همهٔ pending/reviewing."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, g.name AS group_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            WHERE l.status IN ('pending', 'reviewing')
            ORDER BY l.requested_at
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_future_leaves(from_date_str: str, limit: int = 200):
    with _conn() as con:
        rows = con.execute(
            """
            SELECT l.*, u.first_name, u.last_name, g.name AS group_name,
                   r.name AS region_name
            FROM leaves l
            JOIN users u ON u.user_id = l.user_id
            LEFT JOIN groups g ON g.id = u.group_id
            LEFT JOIN regions r ON r.id = u.region_id
            WHERE l.leave_date >= ? AND l.status IN ('pending','reviewing','approved')
            ORDER BY l.requested_at
            LIMIT ?
            """,
            (from_date_str, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def leaves_on_date_for_viewer(date_str: str, region_id: Optional[int] = None):
    """
    برای کلیک روی یک روز تقویم: لیست افراد مرخصی‌دار آن روز.
    اگر region_id داده شود فقط همان منطقه.
    """
    q = """
        SELECT l.*, u.first_name, u.last_name, g.name AS group_name, g.color AS group_color
        FROM leaves l
        JOIN users u ON u.user_id = l.user_id
        LEFT JOIN groups g ON g.id = u.group_id
        WHERE l.leave_date = ? AND l.status IN ('pending','reviewing','approved')
    """
    params: list = [date_str]
    if region_id is not None:
        q += " AND u.region_id = ?"
        params.append(region_id)
    q += " ORDER BY g.name, u.first_name"
    with _conn() as con:
        rows = con.execute(q, params).fetchall()
        return [dict(r) for r in rows]


# ============================================================== مسئول شیفت ====

def get_max_shift_leads() -> int:
    raw = get_setting("max_shift_leads", "0")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def set_max_shift_leads(n: int):
    """سقف تعداد مسئول شیفت — فقط مدیر در لایهٔ bot فراخوانی می‌کند."""
    set_setting("max_shift_leads", max(0, int(n)))


def count_shift_leads() -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS c FROM users WHERE is_shift_lead = 1"
        ).fetchone()
        return row["c"]


def list_shift_leads():
    """لیست مسئولان شیفت با مناطق تخصیص‌یافته."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT u.*
            FROM users u
            WHERE u.is_shift_lead = 1
            ORDER BY u.first_name, u.last_name
            """
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            regs = con.execute(
                """
                SELECT r.id, r.name
                FROM shift_lead_regions slr
                JOIN regions r ON r.id = slr.region_id
                WHERE slr.user_id = ?
                ORDER BY r.name
                """,
                (d["user_id"],),
            ).fetchall()
            d["regions"] = [dict(x) for x in regs]
            result.append(d)
        return result


def list_shift_lead_region_ids(user_id: int) -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT region_id FROM shift_lead_regions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [r["region_id"] for r in rows]


def can_manage_region(user_id: int, region_id: int) -> bool:
    """
    مدیر → همه مناطق.
    مسئول شیفت → فقط مناطق داخل shift_lead_regions.
    (تکنسین ارشد در این تابع لحاظ نمی‌شود؛ محدوده‌ی او فقط گروهِ خودش است — از can_manage_group استفاده کنید.)
    """
    u = get_user(user_id)
    if not u:
        return False
    if u.get("is_admin"):
        return True
    if u.get("is_shift_lead") and region_id in list_shift_lead_region_ids(user_id):
        return True
    return False


def managed_region_ids(user_id: int) -> list:
    """مناطقی که این کاربر حق مدیریت عملیاتی (افزودن/حذف/جابه‌جایی عضو) دارد."""
    u = get_user(user_id)
    if not u:
        return []
    if u.get("is_admin"):
        return [r["id"] for r in list_regions()]
    ids = []
    if u.get("is_shift_lead"):
        ids += list_shift_lead_region_ids(user_id)
    if u.get("is_senior") and u.get("region_id") and u["region_id"] not in ids:
        ids.append(u["region_id"])
    return ids


def assignable_group_ids(user_id: int) -> list:
    """
    گروه‌هایی که این کاربر می‌تواند عضو جدید به آن‌ها بفرستد یا نقش تعیین کند:
    مدیر → همه‌ی گروه‌ها. مسئول شیفت → گروه‌های داخل مناطق تحت مدیریتش.
    تکنسین ارشد → فقط گروهِ خودش.
    """
    u = get_user(user_id)
    if not u:
        return []
    if u.get("is_admin"):
        return [g["id"] for g in list_groups()]
    ids = []
    if u.get("is_shift_lead"):
        for rid in list_shift_lead_region_ids(user_id):
            ids += [g["id"] for g in list_groups(rid)]
    if u.get("is_senior") and u.get("group_id") and u["group_id"] not in ids:
        ids.append(u["group_id"])
    return ids


def appoint_shift_lead(
    user_id: int,
    region_ids: list,
    shift_index: Optional[int] = None,
) -> dict:
    """
    انتصاب مسئول شیفت توسط مدیر.
    سقف max_shift_leads رعایت می‌شود (اگر از قبل مسئول نباشد).
    """
    u = get_user(user_id)
    if not u:
        raise ValueError("user not found")
    if not u.get("approved") and not u.get("is_admin"):
        raise ValueError("user not approved")

    region_ids = list(dict.fromkeys(int(x) for x in region_ids))  # unique, order-preserving
    if not region_ids:
        raise ValueError("at least one region required")

    for rid in region_ids:
        if not get_region(rid):
            raise ValueError(f"region {rid} not found")

    already = bool(u.get("is_shift_lead"))
    if not already:
        cap = get_max_shift_leads()
        if count_shift_leads() >= cap:
            raise ValueError(f"max shift leads reached ({cap})")

    with _conn() as con:
        con.execute(
            "UPDATE users SET is_shift_lead = 1, approved = 1 WHERE user_id = ?",
            (user_id,),
        )
        if shift_index is not None:
            con.execute(
                "UPDATE users SET shift_index = ? WHERE user_id = ?",
                (shift_index, user_id),
            )
        con.execute(
            "DELETE FROM shift_lead_regions WHERE user_id = ?", (user_id,)
        )
        for rid in region_ids:
            con.execute(
                "INSERT OR IGNORE INTO shift_lead_regions (user_id, region_id) VALUES (?, ?)",
                (user_id, rid),
            )
    inv_user(user_id)
    return {
        "user_id": user_id,
        "region_ids": region_ids,
        "shift_index": shift_index,
    }


def set_shift_lead_regions(user_id: int, region_ids: list) -> None:
    """تغییر مناطق تخصیص‌یافتهٔ یک مسئول شیفت — فقط مدیر."""
    u = get_user(user_id)
    if not u or not u.get("is_shift_lead"):
        raise ValueError("not a shift lead")
    region_ids = list(dict.fromkeys(int(x) for x in region_ids))
    for rid in region_ids:
        if not get_region(rid):
            raise ValueError(f"region {rid} not found")
    with _conn() as con:
        con.execute("DELETE FROM shift_lead_regions WHERE user_id = ?", (user_id,))
        for rid in region_ids:
            con.execute(
                "INSERT INTO shift_lead_regions (user_id, region_id) VALUES (?, ?)",
                (user_id, rid),
            )
    inv_user(user_id)


def remove_shift_lead(user_id: int) -> dict:
    """عزل مسئول شیفت (توسط مدیر یا بعد از انتقال)."""
    with _conn() as con:
        con.execute(
            "UPDATE users SET is_shift_lead = 0 WHERE user_id = ?", (user_id,)
        )
        con.execute(
            "DELETE FROM shift_lead_regions WHERE user_id = ?", (user_id,)
        )
    inv_user(user_id)
    return {"user_id": user_id, "removed": True}


def transfer_shift_lead(from_uid: int, to_uid: int) -> dict:
    """
    مسئول شیفت نقش خود را به کاربر دیگر می‌دهد و خودش از نقش خارج می‌شود.
    مناطق و shift_index منتقل می‌شوند.
    """
    src = get_user(from_uid)
    dst = get_user(to_uid)
    if not src or not src.get("is_shift_lead"):
        raise ValueError("source is not a shift lead")
    if not dst:
        raise ValueError("target user not found")
    if from_uid == to_uid:
        raise ValueError("cannot transfer to self")

    region_ids = list_shift_lead_region_ids(from_uid)
    shift_index = src.get("shift_index")

    # اگر مقصد از قبل مسئول نبود، سقف را چک کن (چون مبدأ حذف می‌شود، معمولاً جا باز است)
    if not dst.get("is_shift_lead"):
        # موقتاً مبدأ را می‌شماریم؛ بعد از انتقال تعداد ثابت می‌ماند
        pass

    with _conn() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            # مقصد
            con.execute(
                """
                UPDATE users
                SET is_shift_lead = 1, approved = 1, shift_index = ?
                WHERE user_id = ?
                """,
                (shift_index, to_uid),
            )
            con.execute(
                "DELETE FROM shift_lead_regions WHERE user_id = ?", (to_uid,)
            )
            for rid in region_ids:
                con.execute(
                    "INSERT INTO shift_lead_regions (user_id, region_id) VALUES (?, ?)",
                    (to_uid, rid),
                )
            # مبدأ
            con.execute(
                "UPDATE users SET is_shift_lead = 0 WHERE user_id = ?", (from_uid,)
            )
            con.execute(
                "DELETE FROM shift_lead_regions WHERE user_id = ?", (from_uid,)
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    inv_user(from_uid)
    inv_user(to_uid)
    return {
        "from_uid": from_uid,
        "to_uid": to_uid,
        "region_ids": region_ids,
        "shift_index": shift_index,
    }


# ============================================================== جایگزینی مدیر ====

def replace_admin(from_uid: int, to_uid: int) -> dict:
    """
    تعویض مدیر فقط به‌صورت جایگزینی اتمیک.
    در هر لحظه فقط یک is_admin=1 وجود دارد.
    """
    if from_uid == to_uid:
        raise ValueError("cannot replace with self")

    con = sqlite3.connect(config.DB_PATH, isolation_level=None, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE")
        src = con.execute(
            "SELECT * FROM users WHERE user_id = ?", (from_uid,)
        ).fetchone()
        dst = con.execute(
            "SELECT * FROM users WHERE user_id = ?", (to_uid,)
        ).fetchone()
        if not src or not src["is_admin"]:
            con.execute("ROLLBACK")
            raise ValueError("source is not admin")
        if not dst:
            con.execute("ROLLBACK")
            raise ValueError("target user not found")

        con.execute(
            "UPDATE users SET is_admin = 0 WHERE user_id = ?", (from_uid,)
        )
        con.execute(
            "UPDATE users SET is_admin = 1, approved = 1, profile_complete = 1 WHERE user_id = ?",
            (to_uid,),
        )
        # اطمینان از تک‌مدیر
        con.execute(
            "UPDATE users SET is_admin = 0 WHERE user_id NOT IN (?, ?)",
            (to_uid, from_uid),
        )
        con.execute(
            "UPDATE users SET is_admin = 0 WHERE user_id = ?", (from_uid,)
        )
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()

    inv_user(from_uid)
    inv_user(to_uid)
    return {"from_uid": from_uid, "to_uid": to_uid}
