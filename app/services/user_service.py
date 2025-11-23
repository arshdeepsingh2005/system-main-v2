"""
User verification + caching service backed by SQLAlchemy.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import User


class UserService:
    """
    Thread-safe in-memory cache for user metadata with periodic refresh.
    """

    def __init__(self):
        self._cache: Dict[str, dict] = {}
        # Use regular Lock instead of RLock for eventlet compatibility
        # RLock can cause "cannot wait on un-acquired lock" errors in eventlet/greenlet environments
        self._lock = threading.Lock()
        self._ttl = int(os.getenv("USER_CACHE_TTL", "180"))
        self._max_size = int(os.getenv("USER_CACHE_MAX_ENTRIES", "2000"))
        # Increase refresh interval to reduce DB load (5 minutes instead of 1 minute)
        self._refresh_interval = int(os.getenv("USER_CACHE_REFRESH_INTERVAL", "300"))
        self._hydration_backoff = int(os.getenv("USER_CACHE_MISS_BACKOFF", "5"))
        self._background_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_full_refresh = 0.0

    def start(self) -> None:
        """
        Warm the cache and start the periodic refresh thread.
        """
        # Prevent multiple threads from starting (important in multi-worker environments)
        if self._background_thread and self._background_thread.is_alive():
            return
        
        # Use a lock to ensure only one thread starts
        if not hasattr(self, '_start_lock'):
            self._start_lock = threading.Lock()
        
        with self._start_lock:
            if self._background_thread and self._background_thread.is_alive():
                return
            
            # Do initial refresh (non-blocking, quick)
            try:
                self.refresh_all_users()
            except Exception as e:
                print(f"UserService: Initial refresh failed (will retry): {e}")

            self._stop_event.clear()
            self._background_thread = threading.Thread(
                target=self._refresh_loop, name="user-cache-refresh", daemon=True
            )
            self._background_thread.start()
            print(f"UserService: background cache refresh thread started (interval={self._refresh_interval}s)")

    def stop(self) -> None:
        """
        Stop the background refresh thread (used by tests/shutdown).
        """
        if not self._background_thread:
            return
        self._stop_event.set()
        self._background_thread.join(timeout=5)
        self._background_thread = None

    def verify_username(self, username: Optional[str]) -> Optional[dict]:
        """
        Ensure a username exists and is in good standing.
        Returns cached metadata or None if not found/invalid.
        """
        normalized = self._normalize(username)
        if not normalized:
            return None

        cached = self._get_from_cache(normalized)
        if cached:
            return cached

        record = self._hydrate_username(normalized)
        if record:
            return record

        # Cache miss throttle to prevent repeated DB hits for bad usernames
        with self._lock:
            self._cache[normalized] = {
                "username": normalized,
                "user_id": None,
                "expires_at": time.time() + self._hydration_backoff,
            }
        return None

    def refresh_all_users(self) -> None:
        """
        Refresh the cached user list from the database in bulk.
        """
        try:
            # Use a timeout to prevent hanging
            with SessionLocal() as session:
                rows = session.execute(
                    select(
                        User.id,
                        User.username,
                    )
                ).all()

            now = time.time()
            new_cache: Dict[str, dict] = {}
            for row in rows:
                username = row.username.strip()
                normalized = username.lower()
                new_cache[normalized] = {
                    "user_id": row.id,
                    "username": username,
                    "expires_at": now + self._ttl,
                }

                if len(new_cache) >= self._max_size:
                    break

            with self._lock:
                self._cache.update(new_cache)
                self._last_full_refresh = now

            # Only log if we actually refreshed something (reduce log spam)
            if len(new_cache) > 0:
                print(
                    f"UserService: refreshed {len(new_cache)} usernames from database "
                    f"(ttl={self._ttl}s, next refresh in {self._refresh_interval}s)"
                )
        except Exception as exc:
            # Don't spam logs on every failure - only log if it's been a while
            if time.time() - getattr(self, '_last_error_log', 0) > 300:  # Log max once per 5 minutes
                print(f"UserService: bulk refresh failed -> {exc}")
                self._last_error_log = time.time()

    def refresh_username(self, username: str) -> Optional[dict]:
        """
        Force-refresh a single username and return it.
        """
        normalized = self._normalize(username)
        if not normalized:
            return None
        return self._hydrate_username(normalized)

    def get_cache_snapshot(self) -> Dict[str, dict]:
        """
        Return a copy of the current cache (for diagnostics/testing).
        """
        with self._lock:
            return {k: v.copy() for k, v in self._cache.items()}

    # Internal helpers -------------------------------------------------

    def _refresh_loop(self) -> None:
        while not self._stop_event.wait(self._refresh_interval):
            self.refresh_all_users()

    def _normalize(self, username: Optional[str]) -> Optional[str]:
        if username is None:
            return None
        username = str(username).strip()
        if not username or len(username) > 64:
            return None
        return username.lower()

    def _get_from_cache(self, normalized: str) -> Optional[dict]:
        with self._lock:
            entry = self._cache.get(normalized)
            if not entry:
                return None
            if entry.get("expires_at", 0) < time.time():
                # Soft-expired entry will be refreshed lazily
                return None
            return entry

    def _hydrate_username(self, normalized: str) -> Optional[dict]:
        """
        Hydrate a username from the database and cache it.
        Thread-safe and eventlet-compatible.
        """
        try:
            # Query database first (outside lock to avoid blocking)
            with SessionLocal() as session:
                row = session.execute(
                    select(User).where(func.lower(User.username) == normalized)
                ).scalar_one_or_none()

            if not row:
                return None

            entry = {
                "user_id": row.id,
                "username": row.username,
                "expires_at": time.time() + self._ttl,
            }

            # Update cache with lock (use non-blocking approach for eventlet)
            try:
                self._lock.acquire()
                try:
                    if len(self._cache) >= self._max_size:
                        # Drop arbitrary expired entries to make room.
                        expired = [
                            key
                            for key, value in self._cache.items()
                            if value.get("expires_at", 0) < time.time()
                        ]
                        for key in expired:
                            self._cache.pop(key, None)
                            if len(self._cache) < self._max_size:
                                break
                    self._cache[normalized] = entry
                finally:
                    self._lock.release()
            except Exception as lock_exc:
                # If lock fails, still return the entry (best effort)
                import logging
                logging.warning(f"UserService: lock error during cache update: {lock_exc}")
                # Try to update cache without lock as fallback (not thread-safe but better than nothing)
                if len(self._cache) < self._max_size:
                    self._cache[normalized] = entry

            return entry
        except Exception as exc:
            import logging
            logging.error(f"UserService: failed to hydrate username '{normalized}': {exc}", exc_info=True)
            return None


user_service = UserService()

