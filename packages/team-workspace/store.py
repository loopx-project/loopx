"""Private host metadata. Canonical work state lives exclusively in LoopX."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def uid(prefix):
    return prefix + '_' + uuid.uuid4().hex[:16]


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'workspace.sqlite', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, value TEXT, PRIMARY KEY(kind,id))')
        self.db.commit()

    def put(self, kind, value):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                            (kind, value['id'], json.dumps(value, ensure_ascii=False)))
            self.db.commit()
        return value

    def get(self, kind, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM records WHERE kind=? AND id=?', (kind, key)).fetchone()
        if row is None:
            raise ValueError(f'记录不存在: {kind}/{key}')
        return json.loads(row[0])

    def all(self, kind):
        with self.lock:
            rows = self.db.execute('SELECT value FROM records WHERE kind=? ORDER BY rowid', (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def patch(self, kind, key, **changes):
        with self.lock:
            return self.put(kind, {**self.get(kind, key), **changes})

    def event(self, goal, kind, text, **details):
        return self.put('events', dict(id=uid('event'), goal_id=goal, kind=kind,
                                      text=text, at=time.time(), **details))

    def close(self):
        self.db.close()
