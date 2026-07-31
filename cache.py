# -*- coding: utf-8 -*-
"""
کش درحافظه‌ای سبک برای ربات مرخصی.

طراحی برای اجرای تک‌پردازشی (polling). اگر بعداً چند instance شد،
همین API را روی Redis نگه دارید و فقط backend را عوض کنید.
"""
from __future__ import annotations

import time
import threading
from typing import Any, Callable, Optional


class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: Optional[float]):
        self.value = value
        self.expires_at = (time.monotonic() + ttl) if ttl is not None else None

    def alive(self) -> bool:
        return self.expires_at is None or time.monotonic() < self.expires_at


class Cache:
    def __init__(self):
        self._store: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any:
        with self._lock:
            e = self._store.get(key)
            if e is None or not e.alive():
                if e is not None:
                    self._store.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return e.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        with self._lock:
            self._store[key] = _Entry(value, ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            for k in [k for k in self._store if k.startswith(prefix)]:
                del self._store[k]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl: Optional[float] = None) -> Any:
        v = self.get(key)
        if v is not None:
            return v
        v = factory()
        self.set(key, v, ttl)
        return v

    def stats(self) -> dict:
        total = self.hits + self.misses
        rate = (self.hits / total) if total else 0.0
        with self._lock:
            size = len(self._store)
        return {"hits": self.hits, "misses": self.misses, "hit_rate": rate, "size": size}


# نمونهٔ سراسری
cache = Cache()


# ---------------------------------------------------------------------------
# کلیدهای استاندارد (برای یکنواختی در database / bot)
# ---------------------------------------------------------------------------
# settings:all
# regions:all
# regions:{id}
# groups:region:{region_id}
# groups:{id}
# user:{user_id}
# leaves:month:{region_id}:{YYYY-MM}
# leaves:month:all:{YYYY-MM}
# leaves:user:{user_id}:{YYYY-MM}
# monthly_report:last_sent   → "YYYY-MM" آخرین ماهی که گزارشش ارسال شده


def inv_settings() -> None:
    cache.delete("settings:all")


def inv_regions() -> None:
    cache.delete("regions:all")
    cache.delete_prefix("regions:")


def inv_region(region_id: int) -> None:
    cache.delete("regions:all")
    cache.delete(f"regions:{region_id}")
    cache.delete(f"groups:region:{region_id}")
    cache.delete_prefix(f"leaves:month:{region_id}:")
    cache.delete_prefix("leaves:month:all:")


def inv_group(group_id: int, region_id: int) -> None:
    cache.delete(f"groups:{group_id}")
    cache.delete(f"groups:region:{region_id}")
    cache.delete_prefix(f"leaves:month:{region_id}:")
    cache.delete_prefix("leaves:month:all:")


def inv_user(user_id: int) -> None:
    cache.delete(f"user:{user_id}")
    cache.delete_prefix(f"leaves:user:{user_id}:")


def inv_leaves_month(region_id: Optional[int], year: int, month: int) -> None:
    ym = f"{year:04d}-{month:02d}"
    cache.delete_prefix("leaves:month:all:")
    if region_id is not None:
        cache.delete(f"leaves:month:{region_id}:{ym}")
    else:
        cache.delete_prefix("leaves:month:")
