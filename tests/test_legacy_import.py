import asyncio
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("legacy_test", ROOT / "backend/legacy_import.py")
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="legacy-test-", dir=ROOT / ".test-data")
        self.root = Path(self.temporary.name)
        self.data, self.assets, self.canvases = self.root / "data", self.root / "assets", self.root / "data/canvases"
        self.canvases.mkdir(parents=True)
        self.assets.mkdir()
        self.projects = [{"id": "existing", "name": "keep"}]
        self.registry = self.data / "projects.json"
        self.registry.write_text(json.dumps({"projects": self.projects}))
        def projects():
            return json.loads(self.registry.read_text())["projects"]
        def save_canvas(canvas):
            (self.canvases / (canvas["id"] + ".json")).write_text(json.dumps(canvas))
        self.module = types.SimpleNamespace(DATA_DIR=str(self.data), ASSETS_DIR=str(self.assets), CANVAS_DIR=str(self.canvases), PROJECTS_PATH=str(self.registry), ensure_default_project=projects, normalize_canvas_kind=lambda kind: "smart" if kind == "smart" else "classic", save_canvas=save_canvas)
        self.manager = legacy.Migration(self.module)
        self.count, self.missing, self.posts = 2005, set(), 0
        owner = self
        class Old(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                owner.posts += 1
                self.send_error(405)
            def do_GET(self):
                if self.path == "/api/app-info":
                    data = {"version": "old-web"}
                elif self.path == "/api/projects":
                    data = {"projects": [{"id": "old", "name": "迁移"}]}
                elif self.path == "/api/canvases":
                    data = {"canvases": [{"id": "a", "project": "old", "updated_at": 1}, {"id": "b", "project": "old", "updated_at": 1}]}
                elif self.path.startswith("/api/canvases/"):
                    cid = self.path.rsplit("/", 1)[1]
                    data = {"canvas": {"id": cid, "project": "old", "updated_at": 1, "kind": "smart" if cid == "a" else "classic", "nodes": [{"url": f"/assets/file-{i}.png", "api_key": "do-not-copy"} for i in (range(owner.count) if cid == "a" else range(1))], "connections": []}}
                elif self.path.startswith("/assets/"):
                    if self.path in owner.missing:
                        self.send_error(404)
                        return
                    if self.headers.get("If-None-Match") == '"test"':
                        self.send_response(304)
                        self.end_headers()
                        return
                    body = b"synthetic-image-payload"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Type", "image/png")
                    self.send_header("ETag", '"test"')
                    self.end_headers()
                    self.wfile.write(body)
                    return
                else:
                    self.send_error(404)
                    return
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Old)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def prepare(self):
        detected = self.manager.probe(self.base)
        job = self.manager.start(detected["source"], ["old"])
        deadline = time.time() + 45
        while time.time() < deadline:
            current = self.manager.job(job["id"])
            if current["phase"] in {"ready", "error"}:
                self.assertEqual(current["phase"], "ready", current.get("error"))
                return current
            time.sleep(.02)
        self.fail("Migration timeout")

    def test_more_than_2000_resources_import_both_canvas_types_without_overwriting_or_credentials(self):
        job = self.prepare()
        self.assertEqual(job["total"], 2005)
        result = asyncio.run(self.manager.commit(job["id"]))
        self.assertEqual(result["result"], {"projects": 1, "canvases": 2, "resources": 2005, "missing": 0})
        self.assertEqual(len(list((self.assets / "imports" / job["id"]).iterdir())), 2005)
        imported = [json.loads(file.read_text()) for file in self.canvases.glob("*.json")]
        self.assertEqual({c["kind"] for c in imported}, {"classic", "smart"})
        self.assertTrue(all("api_key" not in node for c in imported for node in c["nodes"]))
        self.assertTrue(all(node["url"].startswith("/assets/imports/") for c in imported for node in c["nodes"]))
        self.assertEqual(self.module.ensure_default_project()[0], self.projects[0])
        self.assertEqual(asyncio.run(self.manager.commit(job["id"])), result)  # no duplicates on retry
        self.assertEqual(self.posts, 0)

    def test_missing_resources_require_explicit_partial_confirmation_and_can_retry(self):
        self.count = 3
        self.missing = {"/assets/file-1.png"}
        job = self.prepare()
        self.assertEqual(len(job["missing"]), 1)
        with self.assertRaises(ValueError):
            asyncio.run(self.manager.commit(job["id"]))
        self.assertEqual(list(self.canvases.iterdir()), [])
        self.missing.clear()
        self.manager.retry(job["id"])
        while self.manager.job(job["id"])["phase"] not in {"ready", "error"}:
            time.sleep(.02)
        self.assertEqual(self.manager.job(job["id"])["missing"], [])
        self.assertEqual(asyncio.run(self.manager.commit(job["id"]))["phase"], "completed")

    def test_partial_import_stays_local_and_unknown_addresses_are_rejected(self):
        self.count = 2
        self.missing = {"/assets/file-1.png"}
        job = self.prepare()
        result = asyncio.run(self.manager.commit(job["id"], True))
        self.assertEqual(result["result"]["missing"], 1)
        for value in ["file:///C:/", "http://user:password@host", "http://127.0.0.1:31300", "http://host/?token=secret"]:
            with self.assertRaises(ValueError):
                legacy.address(value)
        with self.assertRaises(ValueError):
            legacy.resource_path("/assets/%2e%2e/API/.env", self.base)
        self.assertIsNone(legacy.resource_path("https://other.example/assets/image.png", self.base))


if __name__ == "__main__":
    unittest.main()
