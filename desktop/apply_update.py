"""Immutable shell-owned patch transaction. Never execute code from the ZIP."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import uuid
import zipfile


def allowed(rel):
    p = PurePosixPath(rel)
    return isinstance(rel, str) and "\\" not in rel and ":" not in rel and not p.is_absolute() and all(x not in {"", ".", ".."} for x in rel.split("/")) and (rel in {"main.py", "legacy_import.py", "desktop-app-version.json", "VERSION"} or rel.startswith("static/"))


def target(root, rel):
    if not allowed(rel):
        raise ValueError("Unsupported program file")
    value = root / rel
    if root.resolve() not in value.resolve().parents or any(p.is_symlink() for p in [value, *value.parents] if p != root.parent and root in [p, *p.parents]):
        raise ValueError("Unsafe program path")
    return value


def digest(file):
    with file.open("rb") as stream:
        return stream_digest(stream)


def stream_digest(stream):
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def version(root):
    return json.loads((root / "desktop-app-version.json").read_text(encoding="utf-8"))["version"]


def save(file, data):
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temporary, file)


def inspect(root, archive, expected):
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 15000 or len({e.filename for e in entries}) != len(entries):
            raise ValueError("Invalid archive entries")
        if bundle.getinfo("patch.json").file_size > 8 * 1024 * 1024:
            raise ValueError("Manifest too large")
        raw = bundle.read("patch.json")
        manifest = json.loads(raw)
        if manifest["version"] != expected or manifest["fromVersion"] != version(root):
            raise ValueError("Patch version mismatch")
        base, changes, removals = manifest["baseFiles"], manifest["files"], manifest["remove"]
        if not isinstance(base, dict) or not isinstance(changes, dict) or not isinstance(removals, list):
            raise ValueError("Invalid patch manifest")
        for rel, value in base.items():
            file = target(root, rel)
            if not file.is_file() or digest(file) != value:
                raise ValueError("Base files changed; use full installer")
        actual = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file() and allowed(str(p.relative_to(root)).replace("\\", "/"))}
        if actual != set(base):
            raise ValueError("Unexpected base program files")
        if "desktop-app-version.json" not in changes or set(changes) & set(removals) or len(removals) != len(set(removals)):
            raise ValueError("Invalid patch operations")
        expected_names = {"patch.json"} | {"files/" + rel for rel in changes}
        if {entry.filename for entry in entries if not entry.is_dir()} != expected_names:
            raise ValueError("Unexpected archive payload")
        total = 0
        for rel, record in changes.items():
            destination = target(root, rel)
            if destination.exists() and not destination.is_file():
                raise ValueError("Program target is not a file")
            entry = bundle.getinfo("files/" + rel)
            if entry.file_size != record["size"] or entry.file_size > 64 * 1024 * 1024 or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Invalid patch file")
            total += entry.file_size
            with bundle.open(entry) as stream:
                if stream_digest(stream) != record["sha256"]:
                    raise ValueError("Patch checksum mismatch")
        if total > 512 * 1024 * 1024:
            raise ValueError("Patch too large")
        for rel in removals:
            target(root, rel)
            if rel not in base:
                raise ValueError("Removal outside base inventory")
        metadata = json.loads(bundle.read("files/desktop-app-version.json"))
        if metadata.get("version") != expected or metadata.get("protocol") != 1:
            raise ValueError("Invalid functional version")
        return manifest


def rollback(root, work):
    journal = work / "transaction.json"
    if not journal.is_file():
        return
    record = json.loads(journal.read_text())
    for rel, existed in record["previous"].items():
        destination = target(root, rel)
        if existed:
            previous = work / record["backup"] / rel
            if not previous.is_file() or digest(previous) != record["hashes"][rel]:
                raise ValueError("Invalid rollback backup")
    for rel, existed in record["previous"].items():
        destination = target(root, rel)
        if existed:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".patch-recover")
            shutil.copy2(work / record["backup"] / rel, temporary)
            os.replace(temporary, destination)
        else:
            destination.unlink(missing_ok=True)
    journal.unlink()


def main(args):
    command, root, work = args[:3]
    root, work = Path(root).resolve(), Path(work).resolve()
    if root == work or root in work.parents or work in root.parents or root.is_symlink() or work.is_symlink():
        raise ValueError("Program and patch workspace must be separate")
    work.mkdir(parents=True, exist_ok=True)
    journal = work / "transaction.json"
    if command == "rollback":
        rollback(root, work)
        return
    if command == "confirm":
        journal.unlink(missing_ok=True)
        return
    archive, expected = Path(args[3]), args[4]
    manifest = inspect(root, archive, expected)
    if command == "inspect":
        return
    if command != "apply" or journal.exists():
        raise ValueError("Unfinished patch transaction")
    touched = list(manifest["files"]) + manifest["remove"]
    backup = "backup-" + uuid.uuid4().hex
    previous, hashes = {}, {}
    for rel in touched:
        source = target(root, rel)
        previous[rel] = source.is_file()
        if previous[rel]:
            file = work / backup / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, file)
            hashes[rel] = digest(file)
    save(journal, {"previous": previous, "hashes": hashes, "backup": backup, "version": expected})
    try:
        with zipfile.ZipFile(archive) as bundle:
            for rel in manifest["files"]:
                destination = target(root, rel)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(destination.name + ".patch-tmp")
                with bundle.open("files/" + rel) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.replace(temporary, destination)
        for rel in manifest["remove"]:
            target(root, rel).unlink(missing_ok=True)
    except Exception:
        rollback(root, work)
        raise


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        # Paths, provider responses and user content never enter shell logs.
        sys.exit(2)
