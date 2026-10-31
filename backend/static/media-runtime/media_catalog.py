"""Owned image/video lifecycle. No dependencies on application globals or credentials.

All file mutations are serialized, journaled before I/O, and revalidate authoritative
owners. An integration safety gate must certify quiescent tasks before deletion.
"""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
import urllib.parse
import uuid

from media_sources import collect_sources

DAY = 86400
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
SENSITIVE = {"api_key", "apikey", "token", "authorization", "cookie", "password", "credentials"}


class MediaError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MediaCatalog:
    def __init__(self, user_root, roots=None, clock=time.time, local_origins=(), safety_gate=None, reference_loader=None):
        self.user_root = Path(user_root).absolute()
        self.clock, self.safety_gate, self.reference_loader = clock, safety_gate, reference_loader
        self.local_origins = {str(x).rstrip("/").lower() for x in local_origins}
        self.roots = {str(k): Path(v).absolute() for k, v in (roots or {"assets": self.user_root / "assets", "output": self.user_root / "output"}).items()}
        # No runtime-configured external roots ever gain deletion authority.
        for key, path in self.roots.items():
            if path not in (self.user_root / "assets", self.user_root / "output"):
                raise MediaError("external_root")
            self._check_components(path, allow_missing=True)
        self._lock = threading.RLock()
        self._revision, self._scans, self._closed = 0, {}, False
        data = self.user_root / "data"
        self._check_components(data, allow_missing=True)
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / "media_catalog.sqlite3"
        self._process_file = (data / "media_catalog.lock").open("a+b")
        self.exclusive = self._process_lock()
        self._presence_path = data / ("media_catalog.process." + uuid.uuid4().hex + ".lock")
        self._presence_file = self._presence_path.open("x+b")
        self._lock_handle(self._presence_file)
        self._check_components(self.path, allow_missing=True)
        with self._db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise MediaError("unsupported_catalog_schema")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,root TEXT NOT NULL,rel TEXT NOT NULL,
                    kind TEXT NOT NULL,size INTEGER NOT NULL,mtime INTEGER NOT NULL,identity TEXT NOT NULL,
                    source TEXT NOT NULL,discovered REAL NOT NULL,unreferenced_since REAL,state TEXT NOT NULL,
                    keep INTEGER NOT NULL DEFAULT 0,companions TEXT NOT NULL DEFAULT '[]',UNIQUE(root,rel));
                CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY,asset_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS refs(asset_id TEXT NOT NULL,kind TEXT NOT NULL,source_id TEXT NOT NULL,
                    PRIMARY KEY(asset_id,kind,source_id));
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,version INTEGER NOT NULL,heartbeat REAL NOT NULL,
                    closed INTEGER NOT NULL,uncertain INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS leases(owner TEXT NOT NULL,asset_id TEXT NOT NULL,kind TEXT NOT NULL,
                    heartbeat REAL NOT NULL,PRIMARY KEY(owner,asset_id,kind));
                CREATE TABLE IF NOT EXISTS trash_operations(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,asset_id TEXT NOT NULL,
                    created REAL NOT NULL,state TEXT NOT NULL,manifest TEXT NOT NULL,reason TEXT NOT NULL,error TEXT);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT);
                CREATE TABLE IF NOT EXISTS file_moves(id TEXT PRIMARY KEY,state TEXT NOT NULL,manifest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS related_acks(operation_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS scan_runs(id TEXT PRIMARY KEY,created REAL NOT NULL,complete INTEGER NOT NULL,result TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
            row = db.execute("SELECT value FROM meta WHERE key='installation'").fetchone()
            self.installation = row[0] if row else uuid.uuid4().hex
            db.execute("INSERT OR IGNORE INTO meta VALUES('installation',?)", (self.installation,))
            if not db.execute("SELECT 1 FROM meta WHERE key='settings'").fetchone():
                db.execute("INSERT INTO meta VALUES('settings',?)", (encode({"enabled": False, "retention_days": 30, "trash_retention_days": 7, "not_before": 0, "last_automatic": 0}),))
        if self.exclusive:
            self.recover()

    def _process_lock(self):
        return self._lock_handle(self._process_file)

    @staticmethod
    def _lock_handle(handle):
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            return False

    def close(self):
        if not self._closed:
            self._presence_file.close()
            try:
                self._presence_path.unlink()
            except FileNotFoundError:
                pass
            self._process_file.close()
            self._closed = True

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def coordinated(self):
        with self._lock:
            yield

    def mark_dirty(self):
        with self._lock:
            self._revision += 1

    @staticmethod
    def _check_components(path, allow_missing=False):
        path = Path(path).absolute()
        for part in [path, *path.parents]:
            try:
                info = part.lstat()
            except FileNotFoundError:
                if allow_missing:
                    continue
                raise MediaError("missing_file") from None
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise MediaError("linked_path")
        return path

    def _owned(self, path, allow_missing=False, media=True):
        path = Path(os.path.abspath(path))
        self._check_components(path, allow_missing)
        for key, root in self.roots.items():
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if any(part.startswith(".media-trash") for part in rel.parts) or not rel.parts:
                raise MediaError("reserved_path")
            if any(":" in part or any(ord(c) < 32 for c in part) for part in rel.parts):
                raise MediaError("invalid_path")
            if media and path.suffix.lower() not in MEDIA_EXTS:
                raise MediaError("unsupported_type")
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise MediaError("nonexclusive_file")
            return key, rel.as_posix(), path
        raise MediaError("external_path")

    @staticmethod
    def _cancelled(cancel):
        return bool(cancel and (cancel.is_set() if hasattr(cancel, "is_set") else cancel()))

    def _identity(self, path, cancel=None):
        self._owned(path, media=False)
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                if self._cancelled(cancel):
                    raise MediaError("cancelled")
                digest.update(block)
        after = path.stat()
        a = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        b = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if a != b:
            raise MediaError("file_changed")
        return encode([*a, digest.hexdigest()])

    def _normalize(self, value):
        if not isinstance(value, str) or not value or len(value) > 8192:
            return None
        text = value.strip()
        # Absolute local filesystem paths are accepted only under owned roots.
        if os.path.isabs(text) and not text.startswith(("/assets/", "/output/", "/api/storage-files/")):
            try:
                key, rel, _ = self._owned(text, allow_missing=True)
                return key + ":" + (rel.lower() if os.name == "nt" else rel)
            except MediaError:
                return None
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme or parsed.netloc:
            origin = (parsed.scheme + "://" + parsed.netloc).lower()
            if parsed.username or parsed.password or origin not in self.local_origins:
                return None
        raw = urllib.parse.unquote(parsed.path)
        if "%" in raw or "\\" in raw or any(ord(c) < 32 for c in raw):
            return None
        parts = raw.split("/")
        if any(p in {".", ".."} for p in parts):
            return None
        if raw.startswith("/assets/"):
            key, rel = "assets", raw[len("/assets/"):]
        elif raw.startswith("/output/"):
            # Legacy /output URLs prefer assets/output, then legacy output.
            rel = raw[len("/output/"):]
            for key, candidate in (("assets", "output/" + rel), ("output", rel)):
                if key in self.roots and (self.roots[key] / candidate).exists():
                    rel = candidate
                    break
            else:
                key, rel = "assets", "output/" + rel
        elif raw.startswith("/api/storage-files/"):
            kind, _, rel = raw[len("/api/storage-files/"):].partition("/")
            folder = {"upload": "input", "generated": "output", "local": "uploads"}.get(kind)
            if not folder:
                return None
            # Custom storage dirs are never mapped onto managed defaults.
            config = self.user_root / "data/storage_settings.json"
            if config.exists():
                try:
                    settings = json.loads(config.read_text(encoding="utf-8-sig"))
                    configured = settings.get(kind)
                    if configured:
                        p = Path(os.path.expandvars(os.path.expanduser(configured)))
                        p = p if p.is_absolute() else self.user_root / p
                        if p.absolute() != self.user_root / "assets" / folder:
                            return None
                except Exception:
                    raise MediaError("storage_config_unreadable") from None
            key, rel = "assets", folder + "/" + rel
        else:
            return None
        if key not in self.roots or not rel or any(p.startswith(".media-trash") for p in rel.split("/")):
            return None
        return key + ":" + (rel.lower() if os.name == "nt" else rel)

    @staticmethod
    def _strings(value):
        if isinstance(value, str):
            yield value
            # Agent prose/Markdown can embed a local media URL.
            for match in re.finditer(r'(?:https?://[^\s"<>]+)?/(?:assets|output|api/storage-files)/[^\s"<>\]\)]+', value):
                if match.group() != value:
                    yield match.group()
        elif isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() not in SENSITIVE:
                    yield from MediaCatalog._strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from MediaCatalog._strings(item)

    def _asset(self, asset_id, db=None):
        if db is None:
            with self._db() as conn:
                return self._asset(asset_id, conn)
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        return dict(row) if row else None

    def resolve(self, value):
        if isinstance(value, dict):
            value = value.get("asset_id") or value.get("url") or value.get("path")
        with self._lock, self._db() as db:
            row = db.execute("SELECT * FROM assets WHERE id=?", (value,)).fetchone() if isinstance(value, str) else None
            if row:
                return dict(row)
            key = self._normalize(value)
            if not key:
                return None
            row = db.execute("SELECT a.* FROM aliases x JOIN assets a ON a.id=x.asset_id WHERE x.alias=?", (key,)).fetchone()
            if not row:
                # /output/ historically denotes two managed roots. Existence must
                # not erase the stable alias after a legacy file enters trash.
                parsed = urllib.parse.urlsplit(value)
                path = urllib.parse.unquote(parsed.path)
                if path.startswith("/output/"):
                    rel = path[len("/output/"):]
                    alternate = "output:" + (rel.lower() if os.name == "nt" else rel)
                    if key != alternate:
                        row = db.execute("SELECT a.* FROM aliases x JOIN assets a ON a.id=x.asset_id WHERE x.alias=?", (alternate,)).fetchone()
            return dict(row) if row else None

    def resolve_alias(self, value):
        asset = self.resolve(value)
        if not asset or asset["state"] != "active":
            return None
        path = self.roots[asset["root"]] / asset["rel"]
        try:
            self._owned(path)
            return str(path)
        except MediaError:
            return None

    def _sidecars(self, path):
        candidates = [Path(str(path) + ".txt"), Path(str(path) + ".classification.json")]
        others = [p for p in path.parent.iterdir() if p.stem == path.stem and p.suffix.lower() in MEDIA_EXTS and p != path]
        if not others:
            candidates += [path.with_suffix(".txt"), path.with_suffix(".classification.json")]
        return [str(p) for p in candidates if p.is_file()]

    @staticmethod
    def _metadata_matches(path, identity):
        info = path.stat()
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] == json.loads(identity)[:4]

    def register(self, path, source="created", companions=None, cancel=None):
        key, rel, path = self._owned(path)
        with self._lock, self._db() as db:
            previous = db.execute("SELECT * FROM assets WHERE root=? AND rel=?", (key, rel)).fetchone()
            identity = previous["identity"] if previous and previous["state"] == "active" and self._metadata_matches(path, previous["identity"]) else None
        # Existing unchanged videos require only stat + a tiny header read. New or
        # modified files are hashed outside the coordinator and can be cancelled.
        identity = identity or self._identity(path, cancel=cancel)
        with path.open("rb") as f:
            head = f.read(64)
        image = head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM")) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or (head[4:8] == b"ftyp" and head[8:12] in {b"avif", b"avis", b"mif1", b"msf1"})
        video = (head[4:8] == b"ftyp" and not image) or head.startswith(b"\x1aE\xdf\xa3") or (head[:4] == b"RIFF" and head[8:12] == b"AVI ")
        if not (image or video):
            raise MediaError("unrecognized_format")
        sidecars = []
        previous_sidecars = {(s["root"], s["rel"]): s["identity"] for s in json.loads(previous["companions"])} if previous else {}
        for sidecar in companions if companions is not None else self._sidecars(path):
            skey, srel, spath = self._owned(sidecar, media=False)
            if skey != key or spath.parent != path.parent or spath.suffix.lower() not in {".txt", ".json"}:
                raise MediaError("invalid_companion")
            old_identity = previous_sidecars.get((skey, srel))
            sid = old_identity if old_identity and self._metadata_matches(spath, old_identity) else self._identity(spath, cancel=cancel)
            sidecars.append({"root": skey, "rel": srel, "identity": sid})
        with self._lock:
            self._owned(path)
            if not self._metadata_matches(path, identity):
                raise MediaError("file_changed")
            info = path.stat()
            with self._db() as db:
                old = db.execute("SELECT * FROM assets WHERE root=? AND rel=?", (key, rel)).fetchone()
                if old and old["state"] == "trash":
                    raise MediaError("trash_path_conflict")
                aid = old["id"] if old else uuid.uuid4().hex
                changed = old and old["identity"] != identity
                if old:
                    db.execute("UPDATE assets SET size=?,mtime=?,identity=?,companions=?,unreferenced_since=CASE WHEN ? THEN NULL ELSE unreferenced_since END,state='active' WHERE id=?", (info.st_size, info.st_mtime_ns, identity, encode(sidecars), bool(changed), aid))
                else:
                    db.execute("INSERT INTO assets(id,root,rel,kind,size,mtime,identity,source,discovered,state,companions) VALUES(?,?,?,?,?,?,?,?,?,'active',?)", (aid, key, rel, "image" if image else "video", info.st_size, info.st_mtime_ns, identity, source, self.clock(), encode(sidecars)))
                alias = key + ":" + (rel.lower() if os.name == "nt" else rel)
                db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?)", (alias, aid))
            if not old or changed:
                self.mark_dirty()
            return self._asset(aid)

    def settings(self):
        with self._db() as db:
            result = json.loads(db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()[0])
            result["last_successful_scan"] = db.execute("SELECT max(created) FROM scan_runs WHERE complete=1").fetchone()[0]
        result.update({"available": self.exclusive and self.safety_gate is not None, "process_lock": self.exclusive})
        return result

    def update_settings(self, changes):
        with self._lock:
            if not isinstance(changes, dict) or set(changes) - {"enabled", "retention_days", "trash_retention_days"}:
                raise MediaError("invalid_settings")
            old = self.settings()
            new = {k: old[k] for k in ("enabled", "retention_days", "trash_retention_days", "not_before", "last_automatic")}
            if "enabled" in changes and type(changes["enabled"]) is not bool:
                raise MediaError("invalid_settings")
            for key, options in (("retention_days", {7, 30, 60, 90}), ("trash_retention_days", {7, 14, 30, 60, 90})):
                if key in changes and (type(changes[key]) is not int or changes[key] not in options):
                    raise MediaError("invalid_settings")
            new.update(changes)
            if new["enabled"] and not old["available"]:
                raise MediaError("protections_unavailable")
            if new["retention_days"] < old["retention_days"] or new["trash_retention_days"] < old["trash_retention_days"]:
                new["not_before"] = max(new["not_before"], self.clock() + DAY)
            if new["enabled"] and not old["enabled"]:
                new["not_before"] = max(new["not_before"], self.clock() + new["retention_days"] * DAY)
            with self._db() as db:
                db.execute("UPDATE meta SET value=? WHERE key='settings'", (encode(new),))
            self.mark_dirty()
            return self.settings()

    def _gather(self):
        gathered = collect_sources(self.user_root)
        if self.reference_loader:
            try:
                extra = self.reference_loader()
                if isinstance(extra, dict):
                    gathered["sources"].extend(extra.get("sources", []))
                    gathered["errors"].extend(extra.get("errors", []))
                else:
                    gathered["sources"].extend(extra)
            except Exception:
                gathered["errors"].append({"source": "integration", "code": "source_unreadable"})
        return gathered

    def _refresh_refs(self, gathered=None):
        gathered = self._gather() if gathered is None else gathered
        refs = set()
        for kind, source_id, data in gathered["sources"]:
            for value in self._strings(data):
                try:
                    asset = self.resolve(value)
                    if asset:
                        refs.add((asset["id"], str(kind), str(source_id)))
                    elif self._normalize(value):
                        # Missing/unknown owned local media means coverage is uncertain.
                        parsed = urllib.parse.urlsplit(value)
                        if Path(urllib.parse.unquote(parsed.path)).suffix.lower() in MEDIA_EXTS:
                            gathered["errors"].append({"source": str(kind), "id": str(source_id), "code": "unresolved_media"})
                except Exception:
                    gathered["errors"].append({"source": str(kind), "id": str(source_id), "code": "invalid_media_reference"})
        with self._db() as db:
            if not gathered["errors"]:
                db.execute("DELETE FROM refs")
                db.executemany("INSERT OR IGNORE INTO refs VALUES(?,?,?)", sorted(refs))
                db.execute("UPDATE assets SET unreferenced_since=NULL WHERE id IN (SELECT asset_id FROM refs)")
        return gathered

    def _expire_abandoned_sessions(self):
        """Only called after a complete authoritative read, never on a failed scan.

        Expiry releases the stale snapshot but starts a NEW observation period.
        It can therefore never turn an expired heartbeat into immediate deletion.
        """
        cutoff = self.clock() - self.settings()["retention_days"] * DAY
        with self._db() as db:
            stale = [r[0] for r in db.execute("SELECT id FROM sessions WHERE closed=0 AND heartbeat<?", (cutoff,))]
            for sid in stale:
                db.execute("UPDATE assets SET unreferenced_since=NULL WHERE id IN (SELECT asset_id FROM leases WHERE owner=? AND kind='session')", (sid,))
                db.execute("DELETE FROM leases WHERE owner=? AND kind='session'", (sid,))
                db.execute("UPDATE sessions SET closed=1,uncertain=0 WHERE id=?", (sid,))
        if stale:
            self.mark_dirty()

    def references(self, asset_id):
        with self._lock:
            gathered = self._refresh_refs()
            with self._db() as db:
                refs = [dict(r) for r in db.execute("SELECT kind,source_id FROM refs WHERE asset_id=?", (asset_id,))]
                leases = [dict(r) for r in db.execute("SELECT owner,kind,heartbeat FROM leases WHERE asset_id=?", (asset_id,))]
            return {"asset_id": asset_id, "references": refs, "leases": leases, "complete": not gathered["errors"], "errors": gathered["errors"]}

    def scan(self, cancel=None):
        with nullcontext():
            errors, skipped = [], []
            for key, root in self.roots.items():
                if not root.exists():
                    # An absent never-created root is normal; disappearance of an
                    # already catalogued root indicates an offline data directory.
                    with self._db() as db:
                        if db.execute("SELECT 1 FROM assets WHERE root=? LIMIT 1", (key,)).fetchone():
                            errors.append({"source": key, "code": "root_offline"})
                    continue
                try:
                    self._check_components(root)
                    def walk_error(exc):
                        raise exc
                    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
                        dirs[:] = [d for d in dirs if not d.startswith(".media-trash")]
                        for d in list(dirs):
                            try:
                                self._check_components(Path(current) / d)
                            except MediaError:
                                dirs.remove(d)
                                skipped.append({"source": key, "code": "linked_path"})
                        for name in files:
                            if self._cancelled(cancel):
                                errors.append({"code": "cancelled"})
                                break
                            path = Path(current) / name
                            if path.suffix.lower() in MEDIA_EXTS:
                                try:
                                    self.register(path, source="discovered", cancel=cancel)
                                except MediaError as exc:
                                    if str(exc) == "cancelled":
                                        errors.append({"code": "cancelled"})
                                        break
                                    skipped.append({"source": key, "name": name, "code": str(exc)})
                                except Exception:
                                    errors.append({"source": key, "code": "file_unreadable"})
                        if errors and errors[-1].get("code") == "cancelled":
                            break
                except Exception:
                    errors.append({"source": key, "code": "root_unreadable"})
        gathered = self._gather()
        return self._complete_scan(gathered, errors, skipped)

    def _complete_scan(self, gathered, errors, skipped):
        with self._lock:
            gathered = self._refresh_refs(gathered)
            errors.extend(gathered["errors"])
            if not errors:
                self._expire_abandoned_sessions()
            now = self.clock()
            with self._db() as db:
                if db.execute("SELECT 1 FROM sessions WHERE closed=0 AND uncertain=1 LIMIT 1").fetchone():
                    errors.append({"source": "session", "code": "unresolved_session"})
                rows = [dict(r) for r in db.execute("SELECT * FROM assets WHERE state='active'")]
                result_assets = []
                settings = self.settings()
                for asset in rows:
                    referenced = db.execute("SELECT 1 FROM refs WHERE asset_id=?", (asset["id"],)).fetchone()
                    protected = db.execute("SELECT 1 FROM leases WHERE asset_id=?", (asset["id"],)).fetchone()
                    category = "unknown" if errors else "referenced" if referenced else "kept" if asset["keep"] else "protected" if protected else "observing"
                    if referenced or protected or asset["keep"]:
                        db.execute("UPDATE assets SET unreferenced_since=NULL WHERE id=?", (asset["id"],))
                        asset["unreferenced_since"] = None
                    elif not errors:
                        if asset["unreferenced_since"] is None:
                            asset["unreferenced_since"] = now
                            db.execute("UPDATE assets SET unreferenced_since=? WHERE id=?", (now, asset["id"]))
                        if now - asset["unreferenced_since"] >= settings["retention_days"] * DAY:
                            category = "eligible"
                    asset.update({"category": category, "name": Path(asset["rel"]).name, "asset_id": asset["id"], "url": "/" + asset["root"] + "/" + urllib.parse.quote(asset["rel"], safe="/")})
                    result_assets.append(asset)
            scan_id = uuid.uuid4().hex
            summary = {k: sum(1 for a in result_assets if a["category"] == k) for k in ("eligible", "referenced", "kept", "protected", "unknown", "observing")}
            summary["trash_bytes"] = sum(a["size"] for a in result_assets if a["category"] == "eligible")
            summary["reclaim_bytes"] = 0
            result = {"id": scan_id, "version": self._revision, "created": now, "complete": not errors, "errors": errors, "skipped": skipped, "assets": result_assets, "summary": summary}
            self._scans[scan_id] = result
            with self._db() as db:
                db.execute("INSERT INTO scan_runs VALUES(?,?,?,?)", (scan_id, now, not errors, encode(result)))
            return result

    def get_scan(self, scan_id):
        with self._db() as db:
            row = db.execute("SELECT result FROM scan_runs WHERE id=?", (scan_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def sync_session(self, session_id, version, references, closed=False):
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 200 or type(version) is not int or version < 0:
            raise MediaError("invalid_session")
        if type(closed) is not bool or not isinstance(references, (dict, list)) or len(encode(references)) > 8 * 1024 * 1024:
            raise MediaError("invalid_session_references")
        with self._lock, self._db() as db:
            old = db.execute("SELECT version FROM sessions WHERE id=?", (session_id,)).fetchone()
            if old and version <= old[0]:
                raise MediaError("stale_session_version")
            ids, uncertain = set(), False
            for value in self._strings(references):
                asset = self.resolve(value)
                if asset:
                    if asset["state"] == "trash":
                        with self._db() as conn:
                            ops = [r[0] for r in conn.execute("SELECT id FROM trash_operations WHERE asset_id=? AND state='trashed'", (asset["id"],))]
                        restored = self.restore(ops, "session-" + uuid.uuid4().hex)
                        if not all(r["status"] == "restored" for r in restored["results"]):
                            raise MediaError("session_restore_failed")
                    if asset["state"] == "purged":
                        raise MediaError("asset_permanently_deleted")
                    ids.add(asset["id"])
                elif self._normalize(value) and Path(urllib.parse.unquote(urllib.parse.urlsplit(value).path)).suffix.lower() in MEDIA_EXTS:
                    uncertain = True
            previous_ids = {r[0] for r in db.execute("SELECT asset_id FROM leases WHERE owner=? AND kind='session'", (session_id,))}
            previous = db.execute("SELECT closed,uncertain FROM sessions WHERE id=?", (session_id,)).fetchone()
            protection_changed = not previous or previous_ids != (set() if closed else ids) or bool(previous[0]) != bool(closed) or bool(previous[1]) != uncertain
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,heartbeat=excluded.heartbeat,closed=excluded.closed,uncertain=excluded.uncertain", (session_id, version, self.clock(), bool(closed), uncertain))
            db.execute("DELETE FROM leases WHERE owner=? AND kind='session'", (session_id,))
            if not closed:
                db.executemany("INSERT INTO leases VALUES(?,?,?,?)", [(session_id, aid, "session", self.clock()) for aid in ids])
                db.executemany("UPDATE assets SET unreferenced_since=NULL WHERE id=?", [(aid,) for aid in ids])
            if protection_changed:
                self.mark_dirty()
            return {"id": session_id, "version": version, "protected": len(ids), "closed": bool(closed), "uncertain": uncertain}

    session_refs = sync_session

    def mark_session_uncertain(self, session_id):
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 200:
            raise MediaError("invalid_session")
        with self._lock, self._db() as db:
            db.execute("INSERT INTO sessions VALUES(?,0,?,0,1) ON CONFLICT(id) DO UPDATE SET closed=0,uncertain=1", (session_id, self.clock()))
            self.mark_dirty()

    def acquire_lease(self, owner, values, kind="task"):
        with self._lock:
            ids = set()
            for value in self._strings(values):
                asset = self.resolve(value)
                if asset:
                    if asset["state"] != "active":
                        raise MediaError("asset_unavailable")
                    ids.add(asset["id"])
                elif self._normalize(value) and Path(urllib.parse.unquote(urllib.parse.urlsplit(value).path)).suffix.lower() in MEDIA_EXTS:
                    raise MediaError("unresolved_lease")
            with self._db() as db:
                db.executemany("INSERT INTO leases VALUES(?,?,?,?) ON CONFLICT(owner,asset_id,kind) DO UPDATE SET heartbeat=excluded.heartbeat", [(str(owner), aid, kind, self.clock()) for aid in ids])
                db.executemany("UPDATE assets SET unreferenced_since=NULL WHERE id=?", [(aid,) for aid in ids])
            self.mark_dirty()
            return sorted(ids)

    def release_lease(self, owner, kind="task"):
        with self._lock, self._db() as db:
            db.execute("DELETE FROM leases WHERE owner=? AND kind=?", (str(owner), kind))
            self.mark_dirty()

    @contextmanager
    def lease(self, owner, values, kind="task"):
        self.acquire_lease(owner, values, kind)
        try:
            yield
        finally:
            self.release_lease(owner, kind)

    def _gate(self, ignore_operation=None):
        if not self.exclusive:
            raise MediaError("another_process")
        # A second catalog holder must disable deletion in BOTH processes, not
        # merely in the process that lost the primary coordinator lock.
        for marker in self.path.parent.glob("media_catalog.process.*.lock"):
            if marker == self._presence_path:
                continue
            try:
                self._check_components(marker)
                with marker.open("r+b") as handle:
                    if not self._lock_handle(handle):
                        raise MediaError("another_process")
                marker.unlink()
            except FileNotFoundError:
                continue
        if self.safety_gate is None:
            raise MediaError("protections_unavailable")
        try:
            result = self.safety_gate()
            okay = result[0] if isinstance(result, tuple) else result
        except Exception:
            okay = False
        if not okay:
            raise MediaError("active_tasks_or_reads")
        with self._db() as db:
            if db.execute("SELECT 1 FROM sessions WHERE closed=0 AND uncertain=1 LIMIT 1").fetchone():
                raise MediaError("unresolved_session")
            if db.execute("SELECT 1 FROM trash_operations WHERE state IN ('moving','restoring','recovery_required','purging') AND id<>? LIMIT 1", (ignore_operation or "",)).fetchone() or db.execute("SELECT 1 FROM file_moves WHERE state='prepared' LIMIT 1").fetchone():
                raise MediaError("recovery_required")
        gathered = self._refresh_refs()
        if gathered["errors"]:
            raise MediaError("incomplete_references")

    def _protected(self, aid):
        with self._db() as db:
            asset = self._asset(aid, db)
            if not asset:
                raise MediaError("unknown_asset")
            if asset["keep"] or db.execute("SELECT 1 FROM refs WHERE asset_id=?", (aid,)).fetchone() or db.execute("SELECT 1 FROM leases WHERE asset_id=?", (aid,)).fetchone():
                raise MediaError("asset_protected")
            return asset

    def _request(self, operation_id, kind, values):
        if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200:
            raise MediaError("invalid_operation_id")
        fingerprint = hashlib.sha256(encode(values).encode()).hexdigest()
        with self._db() as db:
            old = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if old:
                if old["kind"] != kind or old["fingerprint"] != fingerprint:
                    raise MediaError("operation_id_conflict")
                if old["result"]:
                    return json.loads(old["result"])
            else:
                db.execute("INSERT INTO operations VALUES(?,?,?,NULL)", (operation_id, kind, fingerprint))
        return None

    def _finish(self, operation_id, results):
        result = {"operation_id": operation_id, "results": results, "success": sum(r["status"] in {"trashed", "restored", "purged"} for r in results), "failed": sum(r["status"] == "failed" for r in results)}
        with self._db() as db:
            db.execute("UPDATE operations SET result=? WHERE id=?", (encode(result), operation_id))
        self.mark_dirty()
        return result

    def _trash_dir(self, root_id):
        directory = self.roots[root_id] / (".media-trash-" + self.installation)
        self._check_components(directory, allow_missing=True)
        directory.mkdir(exist_ok=True)
        marker = directory / "owner.json"
        if marker.exists():
            self._check_components(marker)
            if json.loads(marker.read_text()) != {"installation": self.installation}:
                raise MediaError("foreign_trash")
        else:
            with marker.open("x", encoding="utf-8") as f:
                json.dump({"installation": self.installation}, f)
                f.flush()
                os.fsync(f.fileno())
        return directory

    def _validate_trash(self, path, root_id):
        base = self._trash_dir(root_id)
        path = Path(path)
        if path.parent.parent != base or not re.fullmatch("[0-9a-f]{32}", path.parent.name):
            raise MediaError("invalid_trash_path")
        self._check_components(path, allow_missing=True)
        if path.exists() and path.stat().st_nlink != 1:
            raise MediaError("nonexclusive_file")
        return path

    def _manifest_write(self, directory, manifest):
        temp = directory / "manifest.json.tmp"
        self._check_components(temp, allow_missing=True)
        with temp.open("w", encoding="utf-8") as f:
            f.write(encode(manifest))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, directory / "manifest.json")

    def move_to_trash(self, asset_ids, operation_id, scan_id=None, automatic=False, reason="manual", related_entries=None):
        if not isinstance(asset_ids, list) or not 1 <= len(asset_ids) <= 200 or any(not isinstance(x, str) for x in asset_ids):
            raise MediaError("invalid_asset_ids")
        with self._lock:
            old = self._request(operation_id, "trash", [asset_ids, scan_id, automatic, related_entries])
            if old:
                return old
            preview = self.get_scan(scan_id) if scan_id else None
            if scan_id and (not preview or not preview["complete"] or preview["version"] != self._revision):
                raise MediaError("stale_scan")
            results = []
            for aid in dict.fromkeys(asset_ids):
                try:
                    self._gate()
                    asset = self._protected(aid)
                    if automatic:
                        settings = self.settings()
                        if not settings["enabled"] or self.clock() < settings["not_before"] or asset["unreferenced_since"] is None or self.clock() - asset["unreferenced_since"] < settings["retention_days"] * DAY:
                            raise MediaError("observation_period")
                    if asset["state"] != "active":
                        raise MediaError("asset_unavailable")
                    path = self.roots[asset["root"]] / asset["rel"]
                    if self._identity(path) != asset["identity"]:
                        raise MediaError("file_changed")
                    if preview:
                        item = next((a for a in preview["assets"] if a["id"] == aid), None)
                        if not item or item["identity"] != asset["identity"]:
                            raise MediaError("file_changed")
                    tid = uuid.uuid4().hex
                    directory = self._trash_dir(asset["root"]) / tid
                    directory.mkdir()
                    sidecars, shared = [], []
                    with self._db() as db:
                        other_companions = [json.loads(r[0]) for r in db.execute("SELECT companions FROM assets WHERE id<>? AND state IN ('active','trash')", (aid,))]
                    for companion in json.loads(asset["companions"]):
                        if any(any(c["root"] == companion["root"] and c["rel"] == companion["rel"] for c in group) for group in other_companions):
                            shared.append(companion)
                        else:
                            sidecars.append(companion)
                    files = [{"root": asset["root"], "rel": asset["rel"], "identity": asset["identity"]}, *sidecars]
                    for index, item in enumerate(files):
                        original = self.roots[item["root"]] / item["rel"]
                        if self._identity(original) != item["identity"]:
                            raise MediaError("companion_changed")
                        item["trash"] = str(directory / (str(index) + "-" + original.name))
                    # Hashing a large video may take time; refresh task and source
                    # protections again after that work, immediately before intent.
                    self._gate()
                    self._protected(aid)
                    manifest = {"installation": self.installation, "asset_id": aid, "files": files, "preserved_companions": shared, "related_entries": related_entries, "phase": "prepared"}
                    self._manifest_write(directory, manifest)
                    with self._db() as db:
                        db.execute("INSERT INTO trash_operations VALUES(?,?,?,?,?,?,?,NULL)", (tid, operation_id, aid, self.clock(), "moving", encode(manifest), str(reason)[:80]))
                    try:
                        for item in files:
                            original = self.roots[item["root"]] / item["rel"]
                            self._owned(original, media=False)
                            if self._identity(original) != item["identity"]:
                                raise MediaError("file_changed")
                            destination = self._validate_trash(item["trash"], item["root"])
                            if destination.exists():
                                raise MediaError("trash_conflict")
                            original.rename(destination)
                        manifest["phase"] = "trashed"
                        self._manifest_write(directory, manifest)
                        with self._db() as db:
                            db.execute("UPDATE assets SET state='trash',unreferenced_since=NULL WHERE id=?", (aid,))
                            db.execute("UPDATE trash_operations SET state='trashed',manifest=? WHERE id=?", (encode(manifest), tid))
                    except Exception:
                        self._rollback_move(tid, manifest)
                        raise
                    results.append({"asset_id": aid, "id": tid, "status": "trashed", "size": asset["size"]})
                except Exception as exc:
                    results.append({"asset_id": aid, "status": "failed", "code": str(exc) if isinstance(exc, MediaError) else "file_operation_failed"})
            return self._finish(operation_id, results)

    actions = move_to_trash

    def _rollback_move(self, tid, manifest):
        okay = True
        for item in reversed(manifest["files"]):
            try:
                original = self.roots[item["root"]] / item["rel"]
                self._owned(original, allow_missing=True, media=False)
                target = self._validate_trash(item["trash"], item["root"])
                if target.exists():
                    if original.exists():
                        raise MediaError("rollback_conflict")
                    target.rename(original)
                elif not original.exists():
                    raise MediaError("missing_file")
            except Exception:
                okay = False
        with self._db() as db:
            db.execute("UPDATE trash_operations SET state=?,error=? WHERE id=?", ("rolled_back" if okay else "recovery_required", None if okay else "rollback_incomplete", tid))
        return okay

    def list_trash(self, offset=0, limit=100):
        offset, limit = max(0, int(offset)), min(200, max(1, int(limit)))
        with self._db() as db:
            rows = db.execute("SELECT t.*,a.rel,a.root,a.size,a.kind FROM trash_operations t JOIN assets a ON a.id=t.asset_id WHERE t.state IN ('trashed','recovery_required','restoring','purging') ORDER BY t.created DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            total = db.execute("SELECT count(*) FROM trash_operations WHERE state IN ('trashed','recovery_required','restoring','purging')").fetchone()[0]
        retention = self.settings()["trash_retention_days"] * DAY
        return {"items": [{**{k: r[k] for k in ("id", "asset_id", "created", "state", "reason", "rel", "root", "size", "kind")}, "name": Path(r["rel"]).name, "expires": r["created"] + retention} for r in rows], "total": total, "offset": offset, "limit": limit}

    trash = list_trash

    def restored_related_entries(self):
        with self._lock, self._db() as db:
            results = []
            for row in db.execute("SELECT id,asset_id,manifest FROM trash_operations WHERE state='restored' AND id NOT IN (SELECT operation_id FROM related_acks)"):
                entries = json.loads(row["manifest"]).get("related_entries")
                if entries is not None:
                    results.append({"id": row["id"], "asset_id": row["asset_id"], "related_entries": entries})
            return results

    pending_restored_related_entries = restored_related_entries

    def ack_related_entries(self, operation_ids):
        if not isinstance(operation_ids, list) or len(operation_ids) > 200 or any(not isinstance(x, str) for x in operation_ids):
            raise MediaError("invalid_operation_ids")
        with self._lock, self._db() as db:
            db.executemany("INSERT OR IGNORE INTO related_acks VALUES(?)", [(tid,) for tid in operation_ids])
        return {"acknowledged": len(operation_ids)}

    acknowledge_related_entries = ack_related_entries

    def ensure_references(self, values):
        """Restore before undo/save. The caller must not commit a missing snapshot."""
        with self._lock:
            restored, missing, available = [], [], []
            for value in dict.fromkeys(self._strings(values)):
                asset = self.resolve(value)
                if asset and asset["state"] == "trash":
                    with self._db() as db:
                        rows = db.execute("SELECT id FROM trash_operations WHERE asset_id=? AND state='trashed'", (asset["id"],)).fetchall()
                    for row in rows:
                        result = self.restore([row[0]], "ensure-" + uuid.uuid4().hex)
                        restored.extend(r["asset_id"] for r in result["results"] if r["status"] == "restored")
                    asset = self.resolve(value)
                if asset:
                    if asset["state"] == "active" and self.resolve_alias(value):
                        available.append(asset["id"])
                    else:
                        missing.append({"asset_id": asset["id"], "code": "asset_unavailable"})
                elif self._normalize(value) and Path(urllib.parse.unquote(urllib.parse.urlsplit(value).path)).suffix.lower() in MEDIA_EXTS:
                    missing.append({"code": "unknown_asset"})
            return {"complete": not missing, "available": available, "restored": restored, "missing": missing}

    def restore(self, operation_ids, operation_id):
        if not isinstance(operation_ids, list) or not 1 <= len(operation_ids) <= 200:
            raise MediaError("invalid_operation_ids")
        with self._lock:
            old = self._request(operation_id, "restore", operation_ids)
            if old:
                return old
            if not self.exclusive:
                raise MediaError("another_process")
            results = []
            for tid in dict.fromkeys(operation_ids):
                try:
                    with self._db() as db:
                        row = db.execute("SELECT * FROM trash_operations WHERE id=?", (tid,)).fetchone()
                    if not row or row["state"] not in {"trashed", "restoring", "restored"}:
                        raise MediaError("trash_unavailable")
                    manifest = json.loads(row["manifest"])
                    if row["state"] == "restored":
                        results.append({"id": tid, "asset_id": row["asset_id"], "status": "restored", "related_entries": manifest.get("related_entries")})
                        continue
                    # Persist exact conflict destinations before moving any file.
                    if row["state"] != "restoring":
                        conflict = any((self.roots[i["root"]] / i["rel"]).exists() for i in manifest["files"])
                        suffix = ".restored-" + uuid.uuid4().hex[:8] if conflict else ""
                        original_main = Path(manifest["files"][0]["rel"])
                        new_main = original_main.with_name(original_main.stem + suffix + original_main.suffix)
                        for index, item in enumerate(manifest["files"]):
                            rel = Path(item["rel"])
                            if index == 0:
                                target = new_main
                            elif rel.name.startswith(original_main.name):
                                target = rel.with_name(new_main.name + rel.name[len(original_main.name):])
                            else:
                                target = rel.with_name(new_main.stem + rel.name[len(original_main.stem):])
                            item["restore_rel"] = target.as_posix()
                            self._owned(self.roots[item["root"]] / target, allow_missing=True, media=False)
                        with self._db() as db:
                            db.execute("UPDATE trash_operations SET state='restoring',manifest=? WHERE id=?", (encode(manifest), tid))
                    for item in manifest["files"]:
                        target = self.roots[item["root"]] / item["restore_rel"]
                        self._owned(target, allow_missing=True, media=False)
                        source = self._validate_trash(item["trash"], item["root"])
                        if source.exists():
                            if target.exists():
                                raise MediaError("restore_conflict")
                            target.parent.mkdir(parents=True, exist_ok=True)
                            source.rename(target)
                        elif not target.exists():
                            raise MediaError("missing_file")
                        # A recovery destination must still contain the original data.
                        if json.loads(self._identity(target))[-1] != json.loads(item["identity"])[-1]:
                            raise MediaError("restore_identity_changed")
                    main = manifest["files"][0]
                    path = self.roots[main["root"]] / main["restore_rel"]
                    with self._db() as db:
                        companions = [{"root": i["root"], "rel": i["restore_rel"], "identity": self._identity(self.roots[i["root"]] / i["restore_rel"])} for i in manifest["files"][1:]] + manifest.get("preserved_companions", [])
                        db.execute("UPDATE assets SET rel=?,state='active',identity=?,unreferenced_since=NULL,companions=? WHERE id=?", (main["restore_rel"], self._identity(path), encode(companions), row["asset_id"]))
                        alias = main["root"] + ":" + (main["restore_rel"].lower() if os.name == "nt" else main["restore_rel"])
                        db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?)", (alias, row["asset_id"]))
                        db.execute("UPDATE trash_operations SET state='restored',manifest=? WHERE id=?", (encode(manifest), tid))
                    results.append({"id": tid, "asset_id": row["asset_id"], "status": "restored", "rel": main["restore_rel"], "conflict": main["rel"] != main["restore_rel"], "related_entries": manifest.get("related_entries")})
                except Exception as exc:
                    results.append({"id": tid, "status": "failed", "code": str(exc) if isinstance(exc, MediaError) else "restore_failed"})
            return self._finish(operation_id, results)

    def purge(self, operation_ids, operation_id, confirmed=False, automatic=False):
        if not isinstance(operation_ids, list) or not 1 <= len(operation_ids) <= 200:
            raise MediaError("invalid_operation_ids")
        if not automatic and confirmed is not True:
            raise MediaError("confirmation_required")
        with self._lock:
            old = self._request(operation_id, "purge", [operation_ids, automatic])
            if old:
                return old
            results = []
            for tid in dict.fromkeys(operation_ids):
                try:
                    self._gate(ignore_operation=tid)
                    with self._db() as db:
                        row = db.execute("SELECT * FROM trash_operations WHERE id=?", (tid,)).fetchone()
                    if not row or row["state"] not in {"trashed", "purging"}:
                        raise MediaError("trash_unavailable")
                    self._protected(row["asset_id"])
                    if automatic:
                        settings = self.settings()
                        if not settings["enabled"] or self.clock() < settings["not_before"] or self.clock() - row["created"] < settings["trash_retention_days"] * DAY:
                            raise MediaError("trash_retention")
                    manifest = json.loads(row["manifest"])
                    for item in manifest["files"]:
                        path = self._validate_trash(item["trash"], item["root"])
                        if path.exists():
                            # Identity includes inode/mtime, which stay stable on same-disk rename.
                            info = path.stat()
                            original = json.loads(item["identity"])
                            if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] != original[:4]:
                                raise MediaError("trash_identity_changed")
                            hasher = hashlib.sha256()
                            with path.open("rb") as stream:
                                for block in iter(lambda: stream.read(1024 * 1024), b""):
                                    hasher.update(block)
                            digest = hasher.hexdigest()
                            if digest != original[-1]:
                                raise MediaError("trash_identity_changed")
                        elif row["state"] != "purging":
                            raise MediaError("missing_trash_file")
                    self._gate(ignore_operation=tid)
                    self._protected(row["asset_id"])
                    with self._db() as db:
                        db.execute("UPDATE trash_operations SET state='purging' WHERE id=?", (tid,))
                    for item in manifest["files"]:
                        path = self._validate_trash(item["trash"], item["root"])
                        if path.exists():
                            path.unlink()
                    with self._db() as db:
                        db.execute("UPDATE trash_operations SET state='purged' WHERE id=?", (tid,))
                        db.execute("UPDATE assets SET state='purged',unreferenced_since=NULL WHERE id=?", (row["asset_id"],))
                    results.append({"id": tid, "asset_id": row["asset_id"], "status": "purged"})
                except Exception as exc:
                    results.append({"id": tid, "status": "failed", "code": str(exc) if isinstance(exc, MediaError) else "purge_failed"})
            return self._finish(operation_id, results)

    def recover(self):
        """Never purge on startup. Roll back moves; resume non-destructive restores."""
        with self._lock, self._db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM trash_operations WHERE state IN ('moving','restoring','recovery_required')")]
        results = []
        for row in rows:
            try:
                manifest = json.loads(row["manifest"])
                if manifest["installation"] != self.installation:
                    raise MediaError("foreign_trash")
                if row["state"] == "restoring":
                    results.append(self.restore([row["id"]], "recovery-" + uuid.uuid4().hex))
                else:
                    results.append({"id": row["id"], "rolled_back": self._rollback_move(row["id"], manifest)})
            except Exception:
                results.append({"id": row["id"], "error": "recovery_required"})
        with self._db() as db:
            moves = [dict(r) for r in db.execute("SELECT * FROM file_moves WHERE state='prepared'")]
        for row in moves:
            try:
                manifest = json.loads(row["manifest"])
                source, destination = Path(manifest["source"]), Path(manifest["destination"])
                self._validate_move_endpoint(source, directory=manifest["directory"], allow_missing=True)
                self._validate_move_endpoint(destination, directory=manifest["directory"], allow_missing=True)
                if destination.exists() and not source.exists():
                    for relative, identity in manifest.get("identities", {}).items():
                        file = destination / relative if relative else destination
                        if self._identity(file) != identity:
                            raise MediaError("move_recovery_identity_changed")
                    destination.rename(source)
                elif destination.exists() or not source.exists():
                    raise MediaError("move_recovery_conflict")
                with self._db() as db:
                    db.execute("UPDATE file_moves SET state='rolled_back' WHERE id=?", (row["id"],))
                results.append({"id": row["id"], "rolled_back": True})
            except Exception:
                results.append({"id": row["id"], "error": "move_recovery_required"})
        return results

    def move_asset(self, asset_id, destination):
        """Move an owned file while preserving all historical URL aliases."""
        with self._lock:
            self._gate()
            asset = self._asset(asset_id)
            if not asset or asset["state"] != "active":
                raise MediaError("asset_unavailable")
            key, rel, target = self._owned(destination, allow_missing=True)
            source = self.roots[asset["root"]] / asset["rel"]
            if key != asset["root"] or target.exists():
                raise MediaError("move_requires_same_root")
            if self._identity(source) != asset["identity"]:
                raise MediaError("file_changed")
            # A temporary same-root directory provides a single atomic rename for
            # each file, with per-file durable intents and rollback as a group.
            companions = json.loads(asset["companions"])
            mappings = [(source, target)]
            for item in companions:
                old = self.roots[item["root"]] / item["rel"]
                if self._identity(old) != item["identity"]:
                    raise MediaError("companion_changed")
                tail = old.name[len(source.name):] if old.name.startswith(source.name) else old.name[len(source.stem):]
                new = target.with_name((target.name if old.name.startswith(source.name) else target.stem) + tail)
                self._owned(new, allow_missing=True, media=False)
                if new.exists():
                    raise MediaError("move_conflict")
                mappings.append((old, new))
            self._execute_moves(mappings, [asset], directory=False)
            self.mark_dirty()
            return self._asset(asset_id)

    def _validate_move_endpoint(self, path, directory=False, allow_missing=False):
        if not directory:
            return self._owned(path, allow_missing=allow_missing, media=False)
        path = Path(path).absolute()
        self._check_components(path, allow_missing=allow_missing)
        for key, root in self.roots.items():
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if not rel.parts or any(p.startswith(".media-trash") for p in rel.parts):
                raise MediaError("reserved_path")
            return key, rel.as_posix(), path
        raise MediaError("external_path")

    def _execute_moves(self, mappings, assets, directory):
        intents = []
        for source, target in mappings:
            mid = uuid.uuid4().hex
            if directory:
                identities = {str(p.relative_to(source)): self._identity(p) for p in source.rglob("*") if p.is_file()}
            else:
                identities = {"": self._identity(source)}
            manifest = {"source": str(source), "destination": str(target), "directory": directory, "identities": identities}
            intents.append((mid, manifest))
        with self._db() as db:
            db.executemany("INSERT INTO file_moves VALUES(?,'prepared',?)", [(mid, encode(manifest)) for mid, manifest in intents])
        moved = []
        try:
            for source, target in mappings:
                self._validate_move_endpoint(source, directory=directory)
                self._validate_move_endpoint(target, directory=directory, allow_missing=True)
                if target.exists():
                    raise MediaError("move_conflict")
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                moved.append((source, target))
            def translated(path):
                for source, target in mappings:
                    if directory:
                        try:
                            return target / path.relative_to(source)
                        except ValueError:
                            continue
                    elif path == source:
                        return target
                return path
            with self._db() as db:
                for asset in assets:
                    new = translated(self.roots[asset["root"]] / asset["rel"])
                    key, rel, _ = self._owned(new)
                    sidecars = []
                    for companion in json.loads(asset["companions"]):
                        new_sidecar = translated(self.roots[companion["root"]] / companion["rel"])
                        skey, srel, _ = self._owned(new_sidecar, media=False)
                        sidecars.append({"root": skey, "rel": srel, "identity": self._identity(new_sidecar)})
                    db.execute("UPDATE assets SET rel=?,identity=?,companions=? WHERE id=?", (rel, self._identity(new), encode(sidecars), asset["id"]))
                    db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?)", (key + ":" + (rel.lower() if os.name == "nt" else rel), asset["id"]))
                db.executemany("UPDATE file_moves SET state='completed' WHERE id=?", [(mid,) for mid, _ in intents])
        except Exception:
            okay = True
            for source, target in reversed(moved):
                try:
                    if source.exists():
                        raise MediaError("move_rollback_conflict")
                    target.rename(source)
                except Exception:
                    okay = False
            if okay:
                with self._db() as db:
                    db.executemany("UPDATE file_moves SET state='rolled_back' WHERE id=?", [(mid,) for mid, _ in intents])
            raise

    def move_directory(self, source, destination):
        with self._lock:
            self._gate()
            key, rel, source = self._validate_move_endpoint(source, directory=True)
            dkey, drel, destination = self._validate_move_endpoint(destination, directory=True, allow_missing=True)
            if key != dkey or destination.exists() or source in destination.parents:
                raise MediaError("invalid_directory_move")
            for current, dirs, files in os.walk(source, followlinks=False):
                for name in dirs:
                    self._check_components(Path(current) / name)
                for name in files:
                    self._owned(Path(current) / name, media=False)
            with self._db() as db:
                assets = [dict(r) for r in db.execute("SELECT * FROM assets WHERE root=? AND state='active'", (key,)) if r["rel"].startswith(rel + "/")]
            for asset in assets:
                if self._identity(self.roots[key] / asset["rel"]) != asset["identity"]:
                    raise MediaError("file_changed")
            self._gate()
            self._execute_moves([(source, destination)], assets, directory=True)
            self.mark_dirty()
            return {"source": rel, "destination": drel, "updated": len(assets)}

    def set_keep(self, asset_id, keep):
        if type(keep) is not bool:
            raise MediaError("invalid_keep")
        with self._lock, self._db() as db:
            if not self._asset(asset_id, db):
                raise MediaError("unknown_asset")
            db.execute("UPDATE assets SET keep=?,unreferenced_since=NULL WHERE id=?", (keep, asset_id))
            self.mark_dirty()
        return self._asset(asset_id)

    def run_automatic(self):
        # Each destructive batch acquires the coordinator itself; scanning must
        # not keep an outer reentrant acquisition while hashing new large files.
        with nullcontext():
            settings = self.settings()
            now = self.clock()
            if not settings["enabled"] or now < settings["not_before"] or now - settings["last_automatic"] < DAY:
                return {"status": "deferred"}
            self._gate()
            scan = self.scan()
            if not scan["complete"]:
                return {"status": "incomplete", "scan_id": scan["id"]}
            ids = [a["id"] for a in scan["assets"] if a["category"] == "eligible"]
            batches = []
            for offset in range(0, len(ids), 200):
                batches.append(self.move_to_trash(ids[offset:offset + 200], "auto-" + uuid.uuid4().hex, automatic=True, reason="unreferenced"))
            trash = []
            while True:
                page = self.list_trash(offset=len(trash), limit=200)
                trash.extend(page["items"])
                if not page["items"] or len(trash) >= page["total"]:
                    break
            expired = [t["id"] for t in trash if now >= t["expires"] and t["state"] == "trashed"]
            purge_batches = [self.purge(expired[offset:offset + 200], "auto-purge-" + uuid.uuid4().hex, automatic=True) for offset in range(0, len(expired), 200)]
            purged = {"results": [item for batch in purge_batches for item in batch["results"]]} if purge_batches else None
            with self._lock, self._db() as db:
                settings = self.settings()
                settings.pop("available", None)
                settings.pop("process_lock", None)
                settings["last_automatic"] = now
                db.execute("UPDATE meta SET value=? WHERE key='settings'", (encode(settings),))
            return {"status": "complete", "scan_id": scan["id"], "batches": batches, "purge": purged}


MediaRuntime = MediaCatalog
