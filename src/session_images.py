"""Bounded image cache with optional durable storage, isolated by session."""
from collections import OrderedDict
import hashlib
import threading
import sqlite3

MAX_IMAGES = 8
MAX_SESSION_BYTES = 32 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024


class SessionImages:
    def __init__(self, path=None):
        self.frames = OrderedDict()
        self.versions = {}
        self.dropped = {}
        self.lock = threading.RLock()
        self.storage = None
        self.error = ''
        if path is not None:
            from image_storage import ImageStorage
            try:
                self.storage = ImageStorage(path)
            except (OSError, sqlite3.Error):
                self.error = 'Không đọc/lưu được kho ảnh; dữ liệu cũ chưa bị ghi đè.'
        self.persistent = path is not None

    def _state(self, session_id):
        if self.storage is not None:
            try:
                generation, dropped = self.storage.state(session_id)
                if (generation != self.versions.get(session_id, 0)
                        or dropped != self.dropped.get(session_id, 0)):
                    self.frames.pop(session_id, None)
                self.versions[session_id], self.dropped[session_id] = generation, dropped
                self.error = ''
            except (OSError, sqlite3.Error):
                self.error = 'Không đọc được kho ảnh; kiểm tra ổ đĩa/quyền ghi.'

    def _trim_cache(self):
        while sum(len(p) for rows in self.frames.values() for _, p in rows) > MAX_CACHE_BYTES:
            ident, removed = self.frames.popitem(last=False)
            if not self.persistent:
                self.dropped[ident] = self.dropped.get(ident, 0) + len(removed)

    def warning(self, session_id):
        """Persistent notice while a session has pages no longer in memory."""
        with self.lock:
            self._state(session_id)
            if self.error:
                return self.error
            count = self.dropped.get(session_id, 0)
            return (f"{count} ảnh cũ đã bị bỏ do giới hạn dung lượng hoặc dữ liệu hỏng; "
                    "chụp lại phần còn thiếu trước khi hỏi về phần đó.") if count else ''

    def generation(self, session_id):
        with self.lock:
            self._state(session_id)
            return self.versions.get(session_id, 0)

    def get(self, session_id):
        with self.lock:
            self._state(session_id)
            if self.storage is not None and session_id not in self.frames:
                try:
                    pngs = self.storage.read(session_id, MAX_IMAGES, MAX_SESSION_BYTES)
                    self._state(session_id)
                    self.frames[session_id] = [(hashlib.sha256(png).digest(), png) for png in pngs]
                    self.error = ''
                except (OSError, sqlite3.Error):
                    self.error = 'Không đọc được kho ảnh; kiểm tra ổ đĩa/quyền ghi.'
            if session_id in self.frames:
                self.frames.move_to_end(session_id)
            result = [png for digest, png in self.frames.get(session_id, [])]
            self._trim_cache()
            return result

    def add(self, session_id, png, generation=0):
        if not isinstance(png, bytes) or not png or len(png) > MAX_SESSION_BYTES:
            raise ValueError('Ảnh trống hoặc vượt 32 MiB')
        digest = hashlib.sha256(png).digest()
        with self.lock:
            if self.persistent:
                if self.storage is None:
                    raise ValueError(self.error)
                try:
                    if not self.storage.add(session_id, png, generation, MAX_IMAGES, MAX_SESSION_BYTES):
                        return []
                    self.frames.pop(session_id, None)
                    self.error = ''
                    return self.get(session_id)
                except (OSError, sqlite3.Error):
                    self.error = 'Chưa lưu được ảnh; chưa gửi AI. Kiểm tra ổ đĩa/quyền ghi rồi chụp lại.'
                    raise ValueError(self.error) from None
            if generation != self.versions.get(session_id, 0):
                return []
            frames = self.frames.setdefault(session_id, [])
            if not any(ident == digest for ident, _ in frames):
                frames.append((digest, png))
            while len(frames) > MAX_IMAGES or sum(len(p) for _, p in frames) > MAX_SESSION_BYTES:
                # Keep the first screenshot and recent pages when possible.
                frames.pop(1 if len(frames) > 2 else 0)
                self.dropped[session_id] = self.dropped.get(session_id, 0) + 1
            self.frames.move_to_end(session_id)
            self._trim_cache()
            return [p for _, p in frames]

    def clear(self, session_id):
        with self.lock:
            if self.persistent:
                if self.storage is None:
                    raise ValueError(self.error)
                try:
                    self.storage.clear(session_id)
                    self.error = ''
                except (OSError, sqlite3.Error):
                    self.error = 'Chưa xóa được ảnh; dữ liệu vẫn giữ. Kiểm tra ổ đĩa/quyền ghi.'
                    raise ValueError(self.error) from None
            self.frames.pop(session_id, None)
            self.dropped.pop(session_id, None)
            self.versions[session_id] = self.versions.get(session_id, 0) + 1

    def remove(self, session_id, digest):
        with self.lock:
            if self.persistent:
                if self.storage is None:
                    raise ValueError(self.error)
                try:
                    removed = self.storage.remove(session_id, digest)
                except (OSError, sqlite3.Error):
                    raise ValueError('Chưa xóa được ảnh; dữ liệu vẫn giữ.') from None
                self.frames.pop(session_id, None)
                self._state(session_id)
                return removed
            rows = self.frames.get(session_id, [])
            remaining = [(ident, png) for ident, png in rows if ident.hex() != digest]
            if len(remaining) == len(rows):
                return False
            self.frames[session_id] = remaining
            self.versions[session_id] = self.versions.get(session_id, 0) + 1
            return True
