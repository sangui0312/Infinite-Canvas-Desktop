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
from contextlib import closing
from unittest.mock import patch, AsyncMock
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
        (cls.app_dir / "static").mkdir(parents=True)
        (cls.app_dir / "workflows").mkdir()
        (cls.app_dir / "static" / "index.html").write_text("old-page", encoding="utf-8")
        (cls.app_dir / "workflows" / "default.json").write_text("{}", encoding="utf-8")
        (cls.app_dir / "VERSION").write_text("2026.10.08", encoding="utf-8")
        shutil.copy2(ROOT / "backend" / "main.py", cls.app_dir / "main.py")
        shutil.copytree(ROOT / "backend/static/agent-runtime", cls.app_dir / "static/agent-runtime", ignore=shutil.ignore_patterns("__pycache__"))
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
        for name in ("OUTPUT_DIR", "ASSETS_DIR", "HISTORY_FILE", "API_ENV_FILE", "DATA_DIR", "GLOBAL_CONFIG_FILE", "WORKFLOW_DIR"):
            self.assertIn(self.data_dir, Path(getattr(m, name)).parents)
        self.assertEqual(Path(m.STATIC_DIR), self.app_dir / "static")
        self.assertFalse(hasattr(m, "NAS_CONFIG_ROOT"))
        self.assertTrue((self.data_dir / "workflows" / "default.json").is_file())
        self.assertFalse(hasattr(m, "sync_media_gateway_config_from_nas"))
        asyncio.run(m.startup_event())
        self.assertFalse((self.app_dir / "API").exists())
        self.assertFalse((self.app_dir / "data").exists())
        self.assertFalse((self.app_dir / "assets").exists())

    def test_retired_provider_records_are_not_injected_or_exposed(self):
        m = self.module
        target = self.root / 'retired-provider-fixture.json'
        active = {'id': 'apimart', 'name': 'APIMART', 'protocol': 'apimart', 'base_url': 'https://api.apimart.ai', 'image_models': ['gpt-image-2.5-ext'], 'chat_models': ['chat'], 'video_models': ['seedance2.0']}
        records = [active, {'id': 'runninghub'}, {'id': 'volcengine'}, {'id': 'legacy-custom', 'protocol': 'volcengine'}, {'id': 'legacy-host', 'base_url': 'https://www.runninghub.ai'}, {'id': 'legacy-ark', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3'}]
        original = json.dumps(records).encode('utf-8')
        target.write_bytes(original)
        with patch.object(m, 'API_PROVIDERS_FILE', str(target)):
            self.assertEqual(m.default_api_providers(), [])
            self.assertEqual([p['id'] for p in m.load_api_providers()], ['apimart'])
            self.assertEqual([p['id'] for p in m.public_api_providers()], ['apimart'])
            self.assertEqual(m.get_api_provider('comfly')['id'], 'apimart')
            self.assertEqual(target.read_bytes(), original)
            for old in ('runninghub', 'volcengine', 'rh', 'legacy-custom', 'missing'):
                with self.assertRaises(m.HTTPException):
                    m.get_api_provider(old)
            target.write_text('[]', encoding='utf-8')
            self.assertEqual(m.load_api_providers(), [])

    def test_last_custom_provider_can_be_deleted_and_stays_empty_after_reload(self):
        m = self.module
        from fastapi.testclient import TestClient
        target = self.root / 'empty-provider-fixture.json'
        target.write_text('[]', encoding='utf-8')
        with patch.object(m, 'API_PROVIDERS_FILE', str(target)), patch.object(m, 'update_env_values') as write_keys, patch.object(m.httpx, 'AsyncClient', side_effect=AssertionError('must not contact upstream')):
            with closing(TestClient(m.app)) as client:
                created = client.put('/api/providers', json=[{'id': 'custom-api', 'name': 'Custom', 'protocol': 'openai'}])
                self.assertEqual(created.status_code, 200)
                self.assertEqual([p['id'] for p in created.json()['providers']], ['custom-api'])
                deleted = client.put('/api/providers', json=[])
                self.assertEqual(deleted.status_code, 200)
                self.assertEqual(deleted.json()['providers'], [])
                self.assertEqual(client.get('/api/providers').json()['providers'], [])
                self.assertEqual(json.loads(target.read_text(encoding='utf-8')), [])
                write_keys.assert_not_called()

    def test_retired_provider_requests_are_blocked_before_network_or_writes(self):
        m = self.module
        from fastapi.testclient import TestClient
        with patch.object(m.httpx, 'AsyncClient', side_effect=AssertionError('must not contact upstream')), patch.object(m, 'update_env_values') as write_keys, patch.object(m, 'save_api_providers') as write_config:
            with closing(TestClient(m.app)) as client:
                for old in ('runninghub', 'volcengine'):
                    for url in ('/api/providers/test-connection', '/api/providers/probe-async', '/api/providers/fetch-models'):
                        response = client.post(url, json={'provider_id': old, 'protocol': old, 'base_url': 'https://invalid.example'})
                        self.assertEqual(response.status_code, 410, (url, response.text))
                    for url in ('/api/online-image', '/api/canvas-video'):
                        response = client.post(url, json={'provider_id': old, 'prompt': 'offline regression'})
                        self.assertEqual(response.status_code, 410, (url, response.text))
                    response = client.put('/api/providers', json=[{'id': old, 'name': old, 'protocol': old}])
                    self.assertEqual(response.status_code, 410, response.text)
            write_keys.assert_not_called()
            write_config.assert_not_called()
        paths = {route.path for route in m.app.routes}
        self.assertFalse(any('/runninghub/' in path for path in paths))
        self.assertFalse(any(name.startswith(('runninghub_', 'volcengine_', 'generate_runninghub_', 'generate_volcengine_')) for name in vars(m)))
        self.assertFalse((ROOT / 'backend/static/runninghub/api_providers.json').exists())

    def test_agent_routes_actual_host_delivery_and_undo(self):
        from fastapi.testclient import TestClient
        import time
        m = self.module
        canvas = {"id":"agent-integration", "kind":"smart", "title":"Agent test", "nodes":[], "connections":[], "project":"default"}
        m.save_canvas(canvas)
        service = m.CANVAS_AGENT_SERVICE
        plan = {"summary":"制作一张图片", "steps":[{"id":"one","title":"图片","kind":"image","prompt":"test image","provider_id":"apimart","model":"gpt-image-2","references":[],"settings":{"ratio":"1:1","resolution":"1k"}}]}
        providers = [{"id":"apimart","name":"APIMART","base_url":"https://api.apimart.ai","enabled":True,"protocol":"openai","chat_models":["test-llm"],"image_models":["gpt-image-2"],"video_models":[]}]
        async def generate(payload):
            target = m.output_path_for("agent-integration.png")
            m.Image.new("RGB",(64,64),(30,40,50)).save(target)
            return {"images":[m.output_url_for("agent-integration.png")],"image_items":[],"task_id":"test-immediate"}
        with patch.object(m,'load_api_providers',return_value=providers), patch.object(m,'build_online_image_result',side_effect=generate) as submit, patch.dict(service.host,{"llm":AsyncMock(return_value={"text":json.dumps(plan,ensure_ascii=False)})}):
            with TestClient(m.app) as client:
                session = client.post('/api/canvas-agent/sessions',json={"canvas_id":canvas['id']}).json()['session']
                body={"client_request_id":"integration-message", "text":"制作一张图片", "context":{"selected_node_ids":[],"scope":"selection","canvas_revision":canvas['revision']},"llm":{"provider_id":"apimart","model":"test-llm"},"generation":{"provider_id":"apimart","model":"gpt-image-2","apiKind":"image","ratio":"square","resolution":"1k"}}
                response=client.post(f"/api/canvas-agent/sessions/{session['id']}/messages",json=body)
                self.assertEqual(response.status_code,202,response.text)
                run_id=response.json()['run_id']
                def await_state(states):
                    for _ in range(100):
                        run=client.get(f'/api/canvas-agent/runs/{run_id}').json()['run']
                        if run['status'] in states:return run
                        time.sleep(.02)
                    self.fail('Agent did not settle: '+str(run))
                run=await_state({'awaiting_approval','needs_attention'})
                self.assertEqual(run['status'],'awaiting_approval',run)
                action={"request_id":"integration-approve","action":"approve","expected_revision":run['revision'],"plan_revision":run['plan']['revision']}
                approved=client.post(f'/api/canvas-agent/runs/{run_id}/actions',json=action)
                self.assertEqual(approved.status_code,200,approved.text)
                done=await_state({'completed','partial','needs_attention','failed'})
                self.assertEqual(done['status'],'completed',done)
                self.assertEqual(submit.call_count,1)
                self.assertEqual(len(m.load_canvas(canvas['id'])['nodes']),1)
                # Replaying the client request must not start a second generation.
                duplicate=client.post(f"/api/canvas-agent/sessions/{session['id']}/messages",json=body)
                self.assertEqual(duplicate.json()['run_id'],run_id)
                self.assertEqual(submit.call_count,1)
                undone=client.post(f'/api/canvas-agent/runs/{run_id}/undo',json={'request_id':'integration-undo'})
                self.assertEqual(undone.status_code,200,undone.text)
                self.assertEqual(m.load_canvas(canvas['id'])['nodes'],[])

    def test_removed_legacy_update_routes(self):
        routes = {route.path for route in self.module.app.routes}
        for route in ('/api/check-update', '/api/update', '/api/update-from-github', '/api/full-install-download', '/api/update-connectivity', '/api/update-connectivity/probe'):
            self.assertNotIn(route, routes)
        self.assertIn('github.com/sangui0312', self.module.app_info()['repo_url'])

    def test_synthetic_update_backup_restart_recover_preserves_userdata(self):
        m = self.module
        original = (self.app_dir / "main.py").read_bytes()
        user_file = self.data_dir / "data" / "canvases" / "personal.json"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text('{"personal":true}', encoding="utf-8")
        api_file = Path(m.API_ENV_FILE)
        api_file.parent.mkdir(parents=True, exist_ok=True)
        api_file.write_text("TEST_ONLY_KEY=retained", encoding="utf-8")
        backup = m.next_update_backup_dir('recovery-')
        m.create_update_backup(backup, ['main.py', 'VERSION'], ['static/index.html'], kind='test')
        (self.app_dir / 'VERSION').write_text('2026.10.15', encoding='utf-8')
        (self.app_dir / 'static/index.html').write_text('new-page', encoding='utf-8')
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

    def test_upload_fallback_order_and_secret_redaction(self):
        m = self.module
        secret = 'private-test-marker'
        for success_at in range(4):
            calls = []
            def uploader(index):
                async def upload(*args):
                    calls.append(index)
                    if index == success_at:
                        return {'url': 'https://example.test/video.mp4', 'service': str(index)}
                    raise m.HTTPException(status_code=503, detail=secret)
                return upload
            with patch.dict(os.environ, {'CLOUD_VIDEO_UPLOAD_SERVICE': 'selfhosted'}), patch.object(m, 'local_media_path_for_cloud_upload', return_value='test.mp4'), patch.object(m, 'validate_video_file', return_value={}), patch.object(m, 'upload_video_to_selfhosted', side_effect=uploader(0)), patch.object(m, 'upload_video_to_litterbox', side_effect=uploader(1)), patch.object(m, 'upload_video_to_temp_sh', side_effect=uploader(2)):
                if success_at < 3:
                    result = asyncio.run(m.upload_local_video_to_cloud('/assets/test.mp4'))
                    self.assertEqual(result['service'], str(success_at))
                else:
                    with self.assertRaises(m.HTTPException) as failed:
                        asyncio.run(m.upload_local_video_to_cloud('/assets/test.mp4'))
                    self.assertNotIn(secret, failed.exception.detail)
                    self.assertIn('selfhosted', failed.exception.detail)
                    self.assertIn('litterbox', failed.exception.detail)
                    self.assertIn('temp.sh', failed.exception.detail)
            self.assertEqual(calls, list(range(min(success_at + 1, 3))))

    def test_upload_transport_failure_and_explicit_service(self):
        m = self.module
        with patch.dict(os.environ, {'CLOUD_VIDEO_UPLOAD_SERVICE': 'selfhosted'}), patch.object(m, 'local_media_path_for_cloud_upload', return_value='test.mp4'), patch.object(m, 'validate_video_file', return_value={}), patch.object(m, 'upload_video_to_selfhosted', new_callable=AsyncMock) as gateway, patch.object(m, 'upload_video_to_litterbox', new_callable=AsyncMock) as litterbox, patch.object(m, 'upload_video_to_temp_sh', new_callable=AsyncMock) as temp:
            gateway.side_effect = RuntimeError('private-test-marker')
            litterbox.side_effect = TimeoutError('private-test-marker')
            temp.return_value = {'service': 'temp.sh'}
            self.assertEqual(asyncio.run(m.upload_local_video_to_cloud('/assets/test.mp4'))['service'], 'temp.sh')
            gateway.reset_mock()
            litterbox.reset_mock()
            temp.reset_mock()
            litterbox.side_effect = None
            litterbox.return_value = {'service': 'litterbox'}
            self.assertEqual(asyncio.run(m.upload_local_video_to_cloud('/assets/test.mp4', 'litterbox'))['service'], 'litterbox')
            gateway.assert_not_awaited()
            temp.assert_not_awaited()
            litterbox.assert_awaited_once()

    def test_invalid_video_never_reaches_any_upload_service(self):
        m = self.module
        with patch.object(m, 'local_media_path_for_cloud_upload', return_value='test.mp4'), patch.object(m, 'validate_video_file', side_effect=ValueError('invalid')), patch.object(m, 'upload_video_to_selfhosted', new_callable=AsyncMock) as gateway, patch.object(m, 'upload_video_to_litterbox', new_callable=AsyncMock) as litterbox, patch.object(m, 'upload_video_to_temp_sh', new_callable=AsyncMock) as temp:
            with self.assertRaises(m.HTTPException) as failed:
                asyncio.run(m.upload_local_video_to_cloud('/assets/test.mp4'))
            self.assertEqual(failed.exception.status_code, 400)
            for upload in (gateway, litterbox, temp):
                upload.assert_not_awaited()

    def test_gateway_config_failure_does_not_log_values_or_replace_existing_config(self):
        m = self.module
        config = Path(m.local_media_gateway_config_path())
        previous = config.read_bytes() if config.exists() else None
        config.write_text('{"private-test-marker":', encoding='utf-8')
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), patch.object(m, 'update_env_values') as update:
                self.assertEqual(m.sync_media_gateway_config_from_local_file()['status'], 'failed')
                update.assert_not_called()
            self.assertNotIn('private-test-marker', output.getvalue())
        finally:
            if previous is None:
                config.unlink()
            else:
                config.write_bytes(previous)

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
