"""Bounded in-memory screenshots, isolated by session; no desktop polling."""
from collections import OrderedDict
import hashlib
import threading

MAX_IMAGES = 8
MAX_SESSION_BYTES = 32 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024


class SessionImages:
    def __init__(self):
        self.frames = OrderedDict()
        self.versions = {}
        self.lock = threading.Lock()

    def generation(self, session_id):
        with self.lock:
            return self.versions.get(session_id, 0)

    def get(self, session_id):
        with self.lock:
            if session_id in self.frames:
                self.frames.move_to_end(session_id)
            return [png for digest, png in self.frames.get(session_id, [])]

    def add(self, session_id, png, generation=0):
        if not isinstance(png, bytes) or not png or len(png) > MAX_SESSION_BYTES:
            raise ValueError('Ảnh trống hoặc vượt 32 MiB')
        digest = hashlib.sha256(png).digest()
        with self.lock:
            if generation != self.versions.get(session_id, 0):
                return []
            frames = self.frames.setdefault(session_id, [])
            if not any(ident == digest for ident, _ in frames):
                frames.append((digest, png))
            while len(frames) > MAX_IMAGES or sum(len(p) for _, p in frames) > MAX_SESSION_BYTES:
                # Keep the first screenshot and recent pages when possible.
                frames.pop(1 if len(frames) > 2 else 0)
            self.frames.move_to_end(session_id)
            while sum(len(p) for rows in self.frames.values() for _, p in rows) > MAX_CACHE_BYTES:
                self.frames.popitem(last=False)
            return [p for _, p in frames]

    def clear(self, session_id):
        with self.lock:
            self.frames.pop(session_id, None)
            self.versions[session_id] = self.versions.get(session_id, 0) + 1
