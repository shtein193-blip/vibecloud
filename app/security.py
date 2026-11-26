from __future__ import annotations

import hashlib
import hmac
import time


class Signer:
    """Short-lived signed URLs for <audio> streaming and file downloads (no headers needed)."""

    def __init__(self, secret: bytes):
        self.secret = secret

    def _mac(self, *parts) -> str:
        msg = ":".join(map(str, parts)).encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()[:24]

    def sign(self, uid: int, tid: int, ttl: int = 86400) -> str:
        exp = int(time.time()) + ttl
        return f"u={uid}&e={exp}&s={self._mac(uid, tid, exp)}"

    def verify(self, uid: int, tid: int, exp: int, sig: str) -> bool:
        return exp >= time.time() and hmac.compare_digest(self._mac(uid, tid, exp), sig)

    # SSE (EventSource can't send headers): a long-lived token instead of initData in the URL
    def event_token(self, uid: int, ttl: int = 7 * 86400) -> str:
        exp = int(time.time()) + ttl
        return f"{uid}.{exp}.{self._mac('ev', uid, exp)}"

    def verify_event(self, token: str) -> int | None:
        try:
            uid, exp, sig = token.split(".")
            uid_i, exp_i = int(uid), int(exp)
        except ValueError:
            return None
        if exp_i < time.time() or not hmac.compare_digest(self._mac("ev", uid_i, exp_i), sig):
            return None
        return uid_i
