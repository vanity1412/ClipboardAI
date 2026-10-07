"""Transactional local image archive. No files or network outside its database."""
import hashlib
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager

MAX_DISK_BYTES = 256 * 1024 * 1024


class ImageStorage:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA auto_vacuum=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS sessions '
                       '(id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0, '
                       'dropped INTEGER NOT NULL DEFAULT 0, touched REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS images '
                       '(id INTEGER PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id), '
                       'digest TEXT NOT NULL, png BLOB NOT NULL, UNIQUE(session,digest))')
            db.execute('CREATE INDEX IF NOT EXISTS images_session ON images(session,id)')
            # Refuse incompatible/corrupt tables before an image is submitted.
            db.execute('SELECT id,generation,dropped,touched FROM sessions LIMIT 0')
            db.execute('SELECT id,session,digest,png FROM images LIMIT 0')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA secure_delete=ON')
            with db:
                yield db
        finally:
            db.close()

    def state(self, session):
        with self.connect() as db:
            row = db.execute('SELECT generation,dropped FROM sessions WHERE id=?', (session,)).fetchone()
            return row or (0, 0)

    def read(self, session, max_images, max_bytes):
        with self.connect() as db:
            result, total, bad = [], 0, []
            for ident, digest, size in db.execute(
                    'SELECT id,digest,length(png) FROM images WHERE session=? ORDER BY id', (session,)):
                if len(result) >= max_images or not size or total + size > max_bytes:
                    bad.append(ident)
                    continue
                png = db.execute('SELECT png FROM images WHERE id=?', (ident,)).fetchone()[0]
                if not isinstance(png, bytes) or hashlib.sha256(png).hexdigest() != digest:
                    bad.append(ident)
                    continue
                total += len(png)
                result.append(png)
            if bad:
                db.executemany('DELETE FROM images WHERE id=?', [(ident,) for ident in bad])
                db.execute('UPDATE sessions SET dropped=dropped+? WHERE id=?', (len(bad), session))
            return result

    def add(self, session, png, generation, max_images, max_bytes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO sessions(id,touched) VALUES (?,?)', (session, time.time()))
            current = db.execute('SELECT generation FROM sessions WHERE id=?', (session,)).fetchone()[0]
            if generation != current:
                return False
            db.execute('INSERT OR IGNORE INTO images(session,digest,png) VALUES (?,?,?)',
                       (session, hashlib.sha256(png).hexdigest(), png))
            rows = list(db.execute('SELECT id,length(png) FROM images WHERE session=? ORDER BY id', (session,)))
            removed = 0
            while len(rows) > max_images or sum(size for _, size in rows) > max_bytes:
                ident, _ = rows.pop(1 if len(rows) > 2 else 0)
                db.execute('DELETE FROM images WHERE id=?', (ident,))
                removed += 1
            db.execute('UPDATE sessions SET dropped=dropped+?,touched=? WHERE id=?',
                       (removed, time.time(), session))
            total = db.execute('SELECT COALESCE(SUM(length(png)),0) FROM images').fetchone()[0]
            if total > MAX_DISK_BYTES:
                candidates = list(db.execute('SELECT s.id,COUNT(i.id),SUM(length(i.png)) '
                    'FROM sessions s JOIN images i ON i.session=s.id WHERE s.id<>? '
                    'GROUP BY s.id ORDER BY s.touched', (session,)))
                for ident, count, size in candidates:
                    db.execute('DELETE FROM images WHERE session=?', (ident,))
                    db.execute('UPDATE sessions SET dropped=dropped+? WHERE id=?', (count, ident))
                    total -= size
                    if total <= MAX_DISK_BYTES:
                        break
            return True

    def clear(self, session):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO sessions(id,touched) VALUES (?,?)', (session, time.time()))
            db.execute('DELETE FROM images WHERE session=?', (session,))
            db.execute('UPDATE sessions SET generation=generation+1,dropped=0 WHERE id=?', (session,))

    def remove(self, session, digest):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            removed = db.execute('DELETE FROM images WHERE session=? AND digest=?', (session, digest)).rowcount
            if removed:
                db.execute('UPDATE sessions SET generation=generation+1 WHERE id=?', (session,))
            return bool(removed)
