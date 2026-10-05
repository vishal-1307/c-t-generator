"""Slowing down password guessing at the sign-in endpoint.

A sliding window of failed attempts, counted per client address and per
username separately: one address guessing many usernames, and many addresses
guessing one username, are both stopped. In memory, which is right for the
single instance this runs as - a restart forgets the counts, which only ever
errs toward letting someone in to try again.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from .config import settings


class LoginLimiter:
    def __init__(self, attempts: int, window_seconds: float):
        self.attempts = attempts
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _keys(self, client: str, username: str) -> tuple[str, str]:
        return f"ip:{client}", f"user:{username.strip().lower()}"

    def _trim(self, q: deque[float], now: float) -> None:
        while q and now - q[0] > self.window:
            q.popleft()

    def retry_after(self, client: str, username: str) -> float:
        """Seconds until another attempt is allowed; 0 when it is allowed now."""
        if self.attempts <= 0:
            return 0
        now = time.monotonic()
        with self._lock:
            wait = 0.0
            for key in self._keys(client, username):
                q = self._failures[key]
                self._trim(q, now)
                if len(q) >= self.attempts:
                    wait = max(wait, self.window - (now - q[0]))
            return wait

    def failed(self, client: str, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in self._keys(client, username):
                self._failures[key].append(now)

    def succeeded(self, client: str, username: str) -> None:
        # A right password clears that username's count; the address keeps its
        # own, so one good account cannot launder guesses at others.
        with self._lock:
            self._failures.pop(self._keys(client, username)[1], None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginLimiter(settings.login_max_attempts, settings.login_window_seconds)
