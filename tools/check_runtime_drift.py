"""Start one copy of the packaged backend and measure program-file drift.

The desktop updater only applies a file pack when every program file still
matches the published baseline. Run this after packaging to prove a fresh
runtime stays byte-identical after one real startup.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

source = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
if work.exists():
    shutil.rmtree(work)
shutil.copytree(source, work, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def allowed(rel):
    return rel in {"main.py", "legacy_import.py", "desktop-app-version.json", "VERSION"} or rel.startswith("static/")


def snapshot(root):
    result = {}
    for item in root.rglob("*"):
        if item.is_file():
            rel = item.relative_to(root).as_posix()
            if allowed(rel):
                result[rel] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


before = snapshot(work)
python = work / "python" / "python.exe"
process = subprocess.Popen(
    [str(python), "-B", "-X", "utf8", "main.py"],
    cwd=str(work),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
)

started = False
lines = []
deadline = time.time() + 150
while time.time() < deadline:
    line = process.stdout.readline()
    if line:
        lines.append(line.rstrip())
        if "Application startup complete" in line or "Uvicorn running" in line:
            started = True
            break
    elif process.poll() is not None:
        break
    else:
        time.sleep(0.1)
time.sleep(3)
subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    process.wait(timeout=15)
except Exception:
    process.kill()

after = snapshot(work)
changed = sorted(rel for rel in before if before[rel] != after.get(rel))
added = sorted(set(after) - set(before))
removed = sorted(set(before) - set(after))
print(json.dumps({
    "source": str(source),
    "work": str(work),
    "started": started,
    "changedCount": len(changed),
    "changed": changed[:20],
    "added": added[:10],
    "removed": removed[:10],
    "logTail": lines[-12:],
}, ensure_ascii=False, indent=2))
