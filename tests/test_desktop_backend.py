import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_entry_test", ROOT / "backend" / "desktop_entry.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
TOKEN = "test-session-nonce-01234567890123456789"


class GuardTests(unittest.TestCase):
    def request(self, kind="http", path="/static/index.html", headers=None, query=b""):
        results = []
        async def app(scope, receive, send):
            await send({"type": "websocket.accept"} if kind == "websocket" else {"type": "http.response.start", "status": 204})
        async def send(payload):
            results.append(payload)
        module = types.SimpleNamespace(CANVAS_TASKS={"1": {"status": "running"}, "2": {"status": "completed"}}, QUEUE=[{}], current_app_version=lambda: "test")
        guard = entry.DesktopGuard(app, TOKEN, "instance", module, lambda: None)
        actual_headers = {"host": "127.0.0.1:31300", **(headers or {})}
        scope = {"type": kind, "path": path, "method": "GET", "query_string": query, "headers": [(key.encode(), value.encode()) for key, value in actual_headers.items()]}
        asyncio.run(guard(scope, lambda: None, send))
        return results

    def test_static_and_api_require_session(self):
        for path in ("/", "/static/index.html", "/assets/test.png", "/api/canvases", "/desktop/health"):
            self.assertEqual(self.request(path=path)[0]["status"], 403)
        self.assertEqual(self.request(query=("token=" + TOKEN).encode())[0]["status"], 403)
        self.assertEqual(self.request(headers={"cookie": "ic_desktop_session=" + TOKEN})[0]["status"], 204)

    def test_host_origin_and_websocket_boundary(self):
        for headers in ({"host": "evil.example:31300"}, {"origin": "https://evil.example"}, {"origin": "null"}, {"host": "127.0.0.1:3000"}):
            headers["x-desktop-token"] = TOKEN
            self.assertEqual(self.request(headers=headers)[0]["status"], 403)
            self.assertEqual(self.request(kind="websocket", path="/ws/stats", headers=headers)[0]["code"], 1008)
        self.assertEqual(self.request(kind="websocket")[0]["code"], 1008)
        self.assertEqual(self.request(kind="websocket", headers={"cookie": "ic_desktop_session=" + TOKEN, "origin": "http://127.0.0.1:31300"})[0]["type"], "websocket.accept")

    def test_health_identity_and_task_count(self):
        result = self.request(path="/desktop/health", headers={"x-desktop-token": TOKEN})
        payload = json.loads(result[-1]["body"])
        self.assertEqual(payload, {"ok": True, "protocol": 1, "instance": "instance", "pid": os.getpid(), "version": "test", "active_tasks": 2})


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="backend-test-", dir=ROOT / "tests")
        cls.root = Path(cls.temporary.name)
        cls.app_dir, cls.data_dir = cls.root / "app", cls.root / "userdata"
        (cls.app_dir / "static" / "runninghub").mkdir(parents=True)
        (cls.app_dir / "workflows").mkdir()
        (cls.app_dir / "static" / "index.html").write_text("old-page", encoding="utf-8")
        (cls.app_dir / "static" / "runninghub" / "api_providers.json").write_text("[]", encoding="utf-8")
        (cls.app_dir / "workflows" / "default.json").write_text("{}", encoding="utf-8")
        (cls.app_dir / "VERSION").write_text("2026.10.08", encoding="utf-8")
        shutil.copy2(ROOT / "backend" / "main.py", cls.app_dir / "main.py")
        cls.env = patch.dict(os.environ, {"IC_DESKTOP_DATA_DIR": str(cls.data_dir), "IC_DESKTOP_INSTANCE": "test-instance", "IC_DESKTOP_SHELL_VERSION": "0.1.0"})
        cls.env.start()
        spec = importlib.util.spec_from_file_location("isolated_desktop_backend", cls.app_dir / "main.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temporary.cleanup()

    def test_data_root_and_no_nas_startup(self):
        m = self.module
        for name in ("OUTPUT_DIR", "ASSETS_DIR", "HISTORY_FILE", "API_ENV_FILE", "DATA_DIR", "GLOBAL_CONFIG_FILE", "WORKFLOW_DIR", "STATIC_RUNNINGHUB_API_PROVIDERS_FILE", "STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE"):
            self.assertIn(self.data_dir, Path(getattr(m, name)).parents)
        self.assertEqual(Path(m.STATIC_DIR), self.app_dir / "static")
        self.assertEqual(m.NAS_CONFIG_ROOT, "")
        self.assertTrue((self.data_dir / "workflows" / "default.json").is_file())
        with patch.object(m, "sync_media_gateway_config_from_nas", side_effect=AssertionError("NAS must not run")):
            asyncio.run(m.startup_event())
        self.assertFalse((self.app_dir / "API").exists())
        self.assertFalse((self.app_dir / "data").exists())
        self.assertFalse((self.app_dir / "assets").exists())

    def test_feed_compatibility_and_shell_version(self):
        m = self.module
        for manifest in ({}, {"desktop_compatible": True, "desktop_protocol": 1, "min_shell_version": "9.0.0"}, {"desktop_compatible": True, "desktop_protocol": 2, "min_shell_version": "0.1.0"}):
            with self.assertRaises(RuntimeError):
                m.require_desktop_update_manifest(manifest)
        m.require_desktop_update_manifest({"desktop_compatible": True, "desktop_protocol": 1, "min_shell_version": "0.1.0"})

    def test_synthetic_update_backup_restart_recover_preserves_userdata(self):
        m = self.module
        original = (self.app_dir / "main.py").read_bytes()
        user_file = self.data_dir / "data" / "canvases" / "personal.json"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text('{"personal":true}', encoding="utf-8")
        api_file = Path(m.API_ENV_FILE)
        api_file.parent.mkdir(parents=True, exist_ok=True)
        api_file.write_text("TEST_ONLY_KEY=retained", encoding="utf-8")
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("main.py", original + b"\nSYNTHETIC_DESKTOP_UPDATE = True\n")
            archive.writestr("VERSION", "2026.10.15")
            archive.writestr("static/index.html", "new-page")
        archive_data = archive_buffer.getvalue()
        url = m.UPDATE_SERVER_BASE_URL + "/desktop-test.zip"
        package = {"url": url, "size": len(archive_data), "sha256": hashlib.sha256(archive_data).hexdigest()}
        manifest = {"version": "2026.10.15", "desktop_compatible": True, "desktop_protocol": 1, "min_shell_version": "0.1.0", "update_package": package, "full_package": package}
        class Response:
            status_code = 200
            headers = {}
            def __init__(self, url, body):
                self.url, self.body = url, body
            def iter_content(self, size):
                for offset in range(0, len(self.body), size):
                    yield self.body[offset:offset + size]
            def close(self):
                pass
        def get(requested_url, **kwargs):
            return Response(requested_url, json.dumps(manifest).encode() if requested_url.endswith("manifest.json") else archive_data)
        output = io.StringIO()
        with patch.object(m.requests, "get", side_effect=get), patch.object(m.subprocess, "Popen", side_effect=AssertionError("Desktop restart must not launch BAT/browser")), contextlib.redirect_stdout(output):
            result = m.update_from_server(m.UpdateRequest(auto_restart=True, restart_delay=1))
        self.assertTrue(result["restart_scheduled"])
        self.assertIn('"type": "restart"', output.getvalue())
        self.assertIn('"instance": "test-instance"', output.getvalue())
        self.assertEqual((self.app_dir / "VERSION").read_text().strip(), "2026.10.15")
        self.assertEqual((self.app_dir / "static" / "index.html").read_text(), "new-page")
        # Even a syntactically broken current module must be recoverable without import.
        (self.app_dir / "main.py").write_text("this is deliberately broken python !!!", encoding="utf-8")
        with patch.object(entry.importlib.util, "spec_from_file_location", side_effect=AssertionError("Recovery cannot import broken main")):
            recovered = entry.recover_latest(self.app_dir, self.data_dir)
        self.assertTrue(recovered["ok"])
        self.assertEqual((self.app_dir / "main.py").read_bytes(), original)
        self.assertEqual((self.app_dir / "static" / "index.html").read_text(), "old-page")
        self.assertEqual((self.app_dir / "VERSION").read_text().strip(), "2026.10.08")
        self.assertEqual(user_file.read_text(), '{"personal":true}')
        self.assertEqual(api_file.read_text(), "TEST_ONLY_KEY=retained")

    def test_recovery_rejects_path_escape_and_corrupt_snapshot(self):
        with self.assertRaises(ValueError):
            entry.contained(self.app_dir, self.app_dir / ".." / "userdata")
        self.assertFalse(entry.allowed_program_file("../userdata/API/.env"))
        self.assertFalse(entry.allowed_program_file("data/canvases/x.json"))
        backup = self.module.next_update_backup_dir("corrupt-")
        self.module.create_update_backup(backup, ["main.py", "VERSION"], ["static/index.html"], kind="test")
        (Path(backup) / "main.py").write_text("corrupted", encoding="utf-8")
        # Remove other candidates so rejection cannot fall back to a valid snapshot.
        for candidate in Path(self.module.update_backup_root()).iterdir():
            if candidate != Path(backup):
                shutil.rmtree(candidate)
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            entry.recover_latest(self.app_dir, self.data_dir)

    def test_deferred_libraries_apply_before_import_and_reject_escape(self):
        pending = self.data_dir / "data" / "desktop_pending_update.json"
        payload = self.data_dir / "data" / "update_deferred" / "pending-1"
        (payload / "py-libs").mkdir(parents=True)
        (payload / "py-libs" / "library.txt").write_text("new dependency", encoding="utf-8")
        (self.app_dir / "py-libs").mkdir(exist_ok=True)
        unchanged = self.app_dir / "py-libs" / "unchanged.pyd"
        unchanged.write_bytes(b"keep unlisted original dependency")
        pending.write_text(json.dumps({"path": str(payload)}), encoding="utf-8")
        entry.apply_deferred(self.app_dir, self.data_dir)
        self.assertEqual((self.app_dir / "py-libs" / "library.txt").read_text(), "new dependency")
        self.assertEqual(unchanged.read_bytes(), b"keep unlisted original dependency")
        self.assertFalse(pending.exists())
        pending.write_text(json.dumps({"path": str(self.root)}), encoding="utf-8")
        with self.assertRaises(ValueError):
            entry.apply_deferred(self.app_dir, self.data_dir)
        pending.unlink()

    def test_online_rollback_defers_native_files_and_removals_until_exit(self):
        m = self.module
        libraries = self.app_dir / "py-libs"
        libraries.mkdir(exist_ok=True)
        old_lib, unchanged_lib, new_lib = libraries / "locked.pyd", libraries / "not-in-package.pyd", libraries / "added.pyd"
        old_lib.write_bytes(b"old native")
        unchanged_lib.write_bytes(b"unlisted native")
        new_lib.unlink(missing_ok=True)
        backup = m.next_update_backup_dir("native-")
        m.create_update_backup(backup, ["main.py", "VERSION", "py-libs/locked.pyd", "py-libs/added.pyd"], ["static/index.html"], kind="test")
        old_lib.write_bytes(b"new native")
        new_lib.write_bytes(b"newly added native")
        real_replace = m.replace_update_target
        def replace_without_native_writes(source, target):
            self.assertNotIn("py-libs", Path(target).parts, "Online rollback must not replace a loaded native library")
            return real_replace(source, target)
        with self.assertRaises(m.HTTPException) as rejected:
            m.rollback_update(m.RollbackRequest(name=Path(backup).name, auto_restart=False))
        self.assertEqual(rejected.exception.status_code, 409)
        with patch.object(m, "replace_update_target", side_effect=replace_without_native_writes), contextlib.redirect_stdout(io.StringIO()):
            result = m.rollback_update(m.RollbackRequest(name=Path(backup).name, auto_restart=True, restart_delay=1))
        self.assertTrue(result["restart_scheduled"])
        self.assertEqual(set(result["deferred_files"]), {"py-libs/locked.pyd", "py-libs/added.pyd"})
        self.assertEqual(old_lib.read_bytes(), b"new native")
        self.assertTrue(new_lib.exists())
        entry.apply_deferred(self.app_dir, self.data_dir)
        self.assertEqual(old_lib.read_bytes(), b"old native")
        self.assertFalse(new_lib.exists())
        self.assertEqual(unchanged_lib.read_bytes(), b"unlisted native")


if __name__ == "__main__":
    unittest.main()
