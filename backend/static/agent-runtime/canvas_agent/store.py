import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def uid():
    return uuid.uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    """Short, serialized SQLite transactions; never held over network or canvas I/O."""

    def __init__(self, data_dir):
        directory = Path(data_dir) / "agent"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "agent.sqlite3"
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,canvas_id TEXT NOT NULL,title TEXT NOT NULL,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),client_request_id TEXT NOT NULL,data TEXT NOT NULL,UNIQUE(session_id,client_request_id));
            CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL REFERENCES sessions(id),run_id TEXT,role TEXT NOT NULL,content TEXT NOT NULL,refs TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(run_id TEXT NOT NULL REFERENCES runs(id),seq INTEGER NOT NULL,type TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(run_id,seq));
            CREATE TABLE IF NOT EXISTS actions(run_id TEXT NOT NULL REFERENCES runs(id),request_id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(run_id,request_id));
            CREATE TABLE IF NOT EXISTS personas(id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def session(self, session_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            return dict(row) if row else None

    def personas(self):
        with self.connect() as db:
            return {row["id"]: {"data": json.loads(row["data"]), "revision": row["revision"]}
                    for row in db.execute("SELECT * FROM personas ORDER BY rowid")}

    def save_persona(self, persona_id, data, expected_revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM personas WHERE id=?", (persona_id,)).fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise ValueError("人设已在其他窗口修改，请重新打开编辑后保存。")
            db.execute("INSERT INTO personas VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,revision=excluded.revision",
                       (persona_id, encode(data), expected_revision + 1))

    def sessions(self, canvas_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions WHERE canvas_id=? ORDER BY created_at DESC LIMIT 100", (canvas_id,))]

    def create_session(self, canvas_id, title):
        session = dict(id=uid(), canvas_id=canvas_id, title=title[:120], created_at=time.time())
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES(:id,:canvas_id,:title,:created_at)", session)
        return session

    def message(self, session_id, run_id, role, content, refs=None):
        with self.connect() as db:
            db.execute("INSERT INTO messages(id,session_id,run_id,role,content,refs) VALUES(?,?,?,?,?,?)", (uid(), session_id, run_id, role, content[:24000], encode(refs or [])))

    def messages(self, session_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM (SELECT * FROM messages WHERE session_id=? ORDER BY seq DESC LIMIT 200) ORDER BY seq", (session_id,)).fetchall()
            return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]

    def lookup(self, session_id, request_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM runs WHERE session_id=? AND client_request_id=?", (session_id, request_id)).fetchone()
            return json.loads(row[0]) if row else None

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def all(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM runs")]

    def insert(self, run, text, refs):
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?)", (run["id"], run["session_id"], run["client_request_id"], encode(run)))
            db.execute("INSERT INTO messages(id,session_id,run_id,role,content,refs) VALUES(?,?,?,?,?,?)", (uid(), run["session_id"], run["id"], "user", text, encode(refs)))

    def save(self, run, event="run_updated", payload=None):
        run["revision"] += 1
        run["updated_at"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE runs SET data=? WHERE id=?", (encode(run), run["id"]))
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?", (run["id"],)).fetchone()[0]
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (run["id"], seq, event, encode(payload or {"status": run["status"], "revision": run["revision"]}), time.time()))

    def events(self, run_id, after):
        with self.connect() as db:
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 200", (run_id, after))]

    def action(self, run_id, request_id, fingerprint=None, result=None):
        with self.connect() as db:
            if result is not None:
                db.execute("INSERT INTO actions VALUES(?,?,?,?)", (run_id, request_id, fingerprint, encode(result)))
            row = db.execute("SELECT fingerprint,result FROM actions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
            return (row[0], json.loads(row[1])) if row else None
