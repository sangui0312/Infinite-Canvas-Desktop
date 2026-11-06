"""Read-only, fail-closed adapters for the desktop's persistent media owners."""
import json
import os
from pathlib import Path
import sqlite3
import zipfile


def collect_sources(user_root):
    root = Path(user_root)
    sources, errors, stamps = [], [], []

    def check_path(path):
        for parent in [path, *path.parents]:
            info = parent.lstat()
            if parent.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("linked_source")

    def read(path, kind):
        try:
            check_path(path)
            before = path.stat()
            if before.st_size > 128 * 1024 * 1024:
                raise ValueError("oversized_source")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, (dict, list)):
                raise ValueError("unknown_structure")
            after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise ValueError("source_changed")
            if kind != "image-task" or data.get("status") not in ("completed", "failed"):
                sources.append((kind, str(path.relative_to(root)), data))
            if kind == "import" and path.name == "job.json" and data.get("phase") != "completed":
                # Interrupted prepare_commit can leave final copies before its
                # canvas records are durable. Recoverable imports own those copies.
                namespace = root / "assets/imports" / path.parent.name
                if namespace.exists():
                    for media in namespace.rglob("*"):
                        if media.is_file():
                            sources.append(("import", str(path.relative_to(root)), "/assets/" + media.relative_to(root / "assets").as_posix()))
            stamps.append((str(path), after.st_mtime_ns, after.st_size))
        except Exception:
            errors.append({"source": kind, "id": str(path.relative_to(root)), "code": "source_unreadable"})

    for directory, kind in ((root / "data/image26-tasks", "image-task"),
                            (root / "data/canvases", "canvas"),
                            (root / "data/conversations", "conversation"),
                            (root / "workflows", "workflow"),
                            (root / "data/legacy_imports", "import")):
        if not directory.exists():
            continue
        try:
            check_path(directory)
            def walk_error(error):
                raise error
            for current, dirs, files in os.walk(directory, onerror=walk_error, followlinks=False):
                for name in list(dirs):
                    child = Path(current) / name
                    if child.is_symlink() or getattr(child.lstat(), "st_file_attributes", 0) & 0x400:
                        raise ValueError("linked_source_directory")
                for name in sorted(files):
                    if name.lower().endswith(".json"):
                        read(Path(current) / name, kind)
        except Exception:
            errors.append({"source": kind, "code": "source_directory_unreadable"})
    for path, kind in ((root / "history.json", "history"), (root / "data/asset_library.json", "asset_library")):
        if path.exists():
            read(path, kind)
    entry_directory = root / "data/media_entry_operations"
    if entry_directory.exists():
        try:
            check_path(entry_directory)
            for path in entry_directory.glob("*.json"):
                check_path(path)
                value = json.loads(path.read_text(encoding="utf-8"))
                refs = [entry["related"] for entry in value.get("entry_trash", []) if entry.get("state") in {"trashed", "restoring"}]
                if refs:
                    sources.append(("entry-trash", path.name, refs))
        except Exception:
            errors.append({"source":"entry-trash","code":"entry_journal_unreadable"})
    # Saved library workflows can contain local references even though the ZIP itself
    # is not an automatically managed image/video.
    library = root / "assets/library"
    if library.exists():
        try:
            check_path(library)
            for path in library.rglob("*"):
                check_path(path)
                if path.suffix.lower() == ".json" and not path.name.endswith(".classification.json"):
                    read(path, "workflow")
                elif path.suffix.lower() == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        names = [n for n in archive.namelist() if n.endswith("workflow.json")]
                        if not names:
                            raise ValueError("unknown_workflow_zip")
                        for name in names:
                            info = archive.getinfo(name)
                            if info.file_size > 128 * 1024 * 1024:
                                raise ValueError("oversized_workflow")
                            sources.append(("workflow", str(path.relative_to(root)), json.loads(archive.read(name))))
        except Exception:
            errors.append({"source": "workflow", "code": "workflow_unreadable"})
    agent = root / "data/agent/agent.sqlite3"
    if agent.exists():
        db = None
        try:
            check_path(agent)
            db = sqlite3.connect(agent.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {"runs": ("data",), "messages": ("content", "refs"),
                        "events": ("payload",), "actions": ("result",)}
            if not set(expected).issubset(tables):
                raise ValueError("unknown_agent_schema")
            if tables - {"runs", "messages", "events", "actions", "sessions", "personas", "schema_version", "sqlite_sequence"}:
                raise ValueError("unknown_agent_schema")
            if "personas" in tables:
                expected["personas"] = ("data",)
            for table, columns in expected.items():
                actual = {r[1] for r in db.execute('PRAGMA table_info("' + table + '")')}
                if not set(columns).issubset(actual):
                    raise ValueError("unknown_agent_schema")
                for index, row in enumerate(db.execute("SELECT " + ",".join(columns) + " FROM " + table)):
                    values = []
                    for column, raw in zip(columns, row):
                        if not isinstance(raw, str):
                            raise ValueError("invalid_agent_value")
                        # Message text may be plain text; other columns are JSON.
                        if column == "content":
                            try:
                                values.append(json.loads(raw))
                            except json.JSONDecodeError:
                                values.append(raw)
                        else:
                            values.append(json.loads(raw))
                    sources.append(("agent", table + ":" + str(index), values))
            db.rollback()
        except Exception:
            errors.append({"source": "agent", "code": "agent_unreadable"})
        finally:
            if db:
                db.close()
    return {"sources": sources, "errors": errors, "stamps": stamps}
