"""Trusted desktop launcher; deliberately independent of updatable main.py."""
import argparse
import asyncio
import ctypes
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from http.cookies import SimpleCookie

PROTOCOL = 1
PORT = 31300


def contained(root, candidate):
    root, candidate = Path(root).resolve(), Path(candidate).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Desktop program path is outside its allowed root")
    return candidate


def allowed_program_file(rel):
    path = str(rel).replace("\\", "/")
    if path.startswith("/") or ":" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    return path in {"main.py", "VERSION"} or path.startswith(("static/", "py-libs/", "tools/ffmpeg/", "tools/chrome-local-asset-importer/", "tools/photoshop-asset-connector/")) or path == "tools/dreamina/dreamina.exe"


def recover_latest(app_dir, data_dir):
    """Validate an entire restore point before changing code; never import main."""
    backup_root = Path(data_dir) / "data" / "update_backups"
    candidates = []
    if backup_root.is_dir():
        for directory in backup_root.iterdir():
            try:
                contained(backup_root, directory)
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                if manifest.get("state") == "ready" and manifest.get("desktop_protocol") == PROTOCOL:
                    candidates.append((float(manifest.get("created_at", 0)), directory, manifest))
            except (OSError, ValueError, TypeError):
                continue
    failures = []
    for _, directory, manifest in sorted(candidates, key=lambda item: item[0], reverse=True):
        try:
            hashes = manifest.get("file_sha256")
            if not isinstance(hashes, dict) or not hashes or "main.py" not in hashes or "VERSION" not in hashes:
                raise ValueError("Backup lacks desktop file integrity metadata")
            files = []
            for rel, digest in hashes.items():
                if not allowed_program_file(rel):
                    raise ValueError("Backup contains a non-program path")
                source = contained(directory, directory / rel)
                target = contained(app_dir, Path(app_dir) / rel)
                if source.is_symlink() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    raise ValueError("Backup integrity check failed")
                files.append((source, target))
            source = (directory / "main.py").read_bytes()
            compile(source, "backup/main.py", "exec")
            if b"DESKTOP_PROTOCOL_VERSION = 1" not in source or b"IC_DESKTOP_DATA_DIR" not in source:
                raise ValueError("Backup is not desktop compatible")
            # Removing only the program static snapshot avoids stale update assets.
            static_target = contained(app_dir, Path(app_dir) / "static")
            if static_target.exists():
                shutil.rmtree(static_target)
            for source, target in files:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".recover_tmp")
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            for rel, info in manifest.get("root_files", {}).items():
                if allowed_program_file(rel) and not info.get("existed", True):
                    contained(app_dir, Path(app_dir) / rel).unlink(missing_ok=True)
            (Path(data_dir) / "data" / "desktop_pending_update.json").unlink(missing_ok=True)
            return {"ok": True, "protocol": PROTOCOL, "backup": directory.name, "version": manifest.get("from_version", "")}
        except (OSError, ValueError, TypeError, SyntaxError) as exc:
            failures.append(str(exc))
    raise RuntimeError("No validated desktop backup is available" + (": " + failures[0] if failures else ""))


def apply_deferred(app_dir, data_dir):
    pending = Path(data_dir) / "data" / "desktop_pending_update.json"
    if not pending.exists():
        return
    payload = json.loads(pending.read_text(encoding="utf-8"))
    deferred_root = Path(data_dir) / "data" / "update_deferred"
    source = contained(deferred_root, payload["path"]) / "py-libs"
    contained(deferred_root, source)
    if not source.is_dir():
        raise ValueError("Deferred libraries are missing")
    copies = []
    for item in source.rglob("*"):
        contained(deferred_root, item)
        if item.is_symlink():
            raise ValueError("Deferred libraries contain a link")
        if item.is_file():
            target = contained(app_dir, Path(app_dir) / "py-libs" / item.relative_to(source))
            copies.append((item, target))
    removals = []
    for rel in payload.get("remove_files", []):
        if not allowed_program_file(rel) or not rel.startswith("py-libs/"):
            raise ValueError("Deferred removal is not a library file")
        target = contained(app_dir, Path(app_dir) / rel)
        if target.exists() and not target.is_file():
            raise ValueError("Deferred removal must be a file")
        removals.append(target)
    # Partial dependency packages replace only their listed files. Validate the
    # complete operation first, then atomically replace each file after exit.
    for item, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".desktop_update_tmp")
        contained(app_dir, temporary)
        shutil.copy2(item, temporary)
        os.replace(temporary, target)
    for target in removals:
        target.unlink(missing_ok=True)
    pending.unlink()
    shutil.rmtree(source.parent)


class DesktopGuard:
    """ASGI guard wraps the complete application, including static and WS."""
    def __init__(self, app, token, instance, module, stop):
        self.app, self.token, self.instance, self.module, self.stop = app, token, instance, module, stop
        self.inflight_generation = 0

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {}
        for key, value in scope.get("headers", []):
            lowered = key.decode("latin-1").lower()
            # Duplicate boundary headers have ambiguous semantics; reject them.
            if lowered in headers and lowered in {"host", "origin", "x-desktop-token", "cookie"}:
                return await self.reject(kind, send)
            headers[lowered] = value.decode("latin-1")
        host = headers.get("host", "").lower()
        origin = headers.get("origin")
        if host not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"} or (origin is not None and origin not in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}):
            return await self.reject(kind, send)
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
            cookie_token = cookie["ic_desktop_session"].value if "ic_desktop_session" in cookie else ""
        except Exception:
            cookie_token = ""
        given = headers.get("x-desktop-token", "") or cookie_token
        if not given or not hmac.compare_digest(given.encode(), self.token.encode()):
            return await self.reject(kind, send)
        if kind == "http" and scope["path"] == "/desktop/health" and scope["method"] == "GET":
            tasks = getattr(self.module, "CANVAS_TASKS", {})
            running = sum(1 for task in list(tasks.values()) if str(task.get("status", "")).lower() in {"running", "queued", "pending", "processing"})
            running += len(getattr(self.module, "QUEUE", []))
            # Queue/background registries and a running HTTP request can describe
            # the same job; use the larger count to avoid counting it twice.
            running = max(running, self.inflight_generation)
            running += getattr(getattr(self.module, "DESKTOP_IMPORT_MANAGER", None), "active", 0)
            payload = {"ok": True, "protocol": PROTOCOL, "instance": self.instance, "pid": os.getpid(), "version": self.module.current_app_version(), "active_tasks": running}
            base = getattr(self.module, "BASE_DIR", "")
            if base and (Path(base) / "desktop-app-version.json").is_file():
                payload["desktop_version"] = json.loads((Path(base) / "desktop-app-version.json").read_text(encoding="utf-8"))["version"]
            return await self.respond(send, 200, payload)
        if kind == "http" and scope["path"] == "/desktop/shutdown" and scope["method"] == "POST":
            await self.respond(send, 200, {"ok": True})
            self.stop()
            return
        generation = kind == "http" and scope.get("method") == "POST" and (
            scope["path"] in {"/generate", "/api/generate", "/api/ms/generate", "/api/angle/generate", "/api/online-image", "/api/canvas-video", "/api/chat", "/api/chat/agent", "/api/chat/stream", "/api/runninghub/submit", "/api/runninghub/workflow-submit"}
            or (scope["path"].startswith("/api/workflows/") and scope["path"].endswith("/run")))
        if generation:
            self.inflight_generation += 1
        try:
            return await self.app(scope, receive, send)
        finally:
            if generation:
                self.inflight_generation -= 1

    async def reject(self, kind, send):
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await self.respond(send, 403, {"error": "Desktop session denied"})

    async def respond(self, send, status, payload):
        body = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})


def windows_lifecycle(parent_pid):
    """Hold the exact parent process and a kill-on-close job for descendants."""
    if os.name != "nt":
        return None, None
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    parent = kernel.OpenProcess(0x00100000, False, parent_pid) if parent_pid else None
    if parent_pid and not parent:
        raise RuntimeError("Electron parent process is no longer available")
    job = kernel.CreateJobObjectW(None, None)
    # JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on Windows x64.
    limits = ctypes.create_string_buffer(144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)
    ctypes.c_uint32.from_buffer(limits, 16).value = 0x00002000
    if not job or not kernel.SetInformationJobObject(job, 9, limits, ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise RuntimeError("Unable to establish desktop process cleanup job")
    # Keep handles open for process lifetime; Windows closes the job on exit.
    return kernel, (parent, job)


async def serve(module, token, instance, parent_pid, lifecycle):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(module.app, host="127.0.0.1", port=PORT, access_log=False, ws_ping_interval=None, ws_ping_timeout=None, timeout_graceful_shutdown=5))
    server.config.app = DesktopGuard(module.app, token, instance, module, lambda: setattr(server, "should_exit", True))

    async def monitor_parent():
        if not parent_pid:
            return
        kernel, handles = lifecycle
        while not server.should_exit:
            await asyncio.sleep(1)
            alive = kernel.WaitForSingleObject(handles[0], 0) == 258 if kernel else _parent_alive(parent_pid)
            if not alive:
                server.should_exit = True
                await asyncio.sleep(8)
                os._exit(0)
    monitor = asyncio.create_task(monitor_parent())
    try:
        await server.serve()
    finally:
        monitor.cancel()


def _parent_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recover-latest", action="store_true")
    args = parser.parse_args()
    app_dir = Path(os.environ["IC_DESKTOP_APP_DIR"]).resolve()
    data_dir = Path(os.environ["IC_DESKTOP_DATA_DIR"]).resolve()
    if app_dir == data_dir or app_dir in data_dir.parents or data_dir in app_dir.parents:
        raise ValueError("Desktop code and userdata roots must be separate")
    if args.recover_latest:
        print(json.dumps(recover_latest(app_dir, data_dir)), flush=True)
        return
    if os.getenv("IC_DESKTOP_PORT", str(PORT)) != str(PORT):
        raise ValueError("Desktop port must remain 31300")
    token = os.environ["IC_DESKTOP_TOKEN"]
    if len(token) < 32:
        raise ValueError("Desktop session token is too short")
    instance = os.environ["IC_DESKTOP_INSTANCE"]
    parent_pid = int(os.getenv("IC_DESKTOP_PARENT_PID", "0"))
    lifecycle = windows_lifecycle(parent_pid)
    apply_deferred(app_dir, data_dir)
    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))
    spec = importlib.util.spec_from_file_location("ic_desktop_backend", app_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    migration_file = app_dir / "legacy_import.py"
    if migration_file.is_file():
        migration_spec = importlib.util.spec_from_file_location("ic_legacy_import", migration_file)
        migration = importlib.util.module_from_spec(migration_spec)
        sys.modules[migration_spec.name] = migration
        migration_spec.loader.exec_module(migration)
        module.DESKTOP_IMPORT_MANAGER = migration.install(module)
    if getattr(module, "DESKTOP_PROTOCOL_VERSION", None) != PROTOCOL:
        raise RuntimeError("Backend is not compatible with this desktop shell")
    asyncio.run(serve(module, token, instance, parent_pid, lifecycle))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr, flush=True)
        sys.exit(1)
