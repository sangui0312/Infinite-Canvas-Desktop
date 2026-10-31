"""Isolated integration checks: never open or remove personal data."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class MediaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="media-integration-", dir=ROOT / "tests")
        base = Path(self.temp.name)
        self.program, self.user = base / "app", base / "userdata"
        (self.program / "static").mkdir(parents=True)
        (self.program / "workflows").mkdir()
        shutil.copy2(ROOT / "backend/main.py", self.program / "main.py")
        for runtime in ("agent-runtime", "media-runtime"):
            shutil.copytree(ROOT / "backend/static" / runtime, self.program / "static" / runtime, ignore=shutil.ignore_patterns("__pycache__"))
        self.env = patch.dict(os.environ, {"IC_DESKTOP_DATA_DIR": str(self.user)})
        self.env.start()
        name = "media_integration_" + base.name.replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, self.program / "main.py")
        self.m = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.m
        spec.loader.exec_module(self.m)
        self.client = TestClient(self.m.app)
        self.headers = {"origin": "http://testserver"}

    def tearDown(self):
        self.client.close()
        if self.m.MEDIA_CATALOG:
            self.m.MEDIA_CATALOG.close()
        self.env.stop()
        self.temp.cleanup()

    def image(self, name="sample.png"):
        path = Path(self.m.LOCAL_UPLOAD_DIR) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (3, 3), "blue").save(path)
        return path, "/assets/uploads/" + name

    def test_history_removes_relation_without_touching_canvas_media(self):
        path, url = self.image()
        self.m.save_canvas({"id": "shared", "nodes": [{"image": url}]})
        self.m.save_to_history({"timestamp": 1, "images": [url]})
        response = self.client.post("/api/history/delete", json={"timestamp": 1})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["files_retained"])
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(Path(self.m.HISTORY_FILE).read_text()), [])
        scan = self.m.MEDIA_CATALOG.scan()
        self.assertTrue(scan["complete"], scan["errors"])
        self.assertEqual(scan["assets"][0]["category"], "referenced")

    def test_local_delete_preserves_shared_file(self):
        path, url = self.image()
        self.m.save_canvas({"id": "shared", "nodes": [{"url": url}]})
        response = self.client.post("/api/local-assets/delete", headers=self.headers, json={"names": [path.name]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted"], [])
        self.assertTrue(path.exists())

    def test_local_delete_and_restore_include_sidecars(self):
        path, url = self.image()
        caption = path.with_suffix(".txt")
        caption.write_text("caption", encoding="utf-8")
        response = self.client.post("/api/local-assets/delete", headers=self.headers, json={"names": [path.name]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deleted"], [path.name])
        self.assertFalse(path.exists())
        self.assertFalse(caption.exists())
        trash = self.client.get("/api/media-trash").json()["items"]
        restored = self.client.post("/api/media-trash/restore", headers=self.headers, json={"operation_id": "restore-operation-1", "operation_ids": [trash[0]["id"]]})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["success"], 1)
        self.assertTrue(path.exists())
        self.assertEqual(caption.read_text(), "caption")

    def test_library_category_retains_unknown_and_referenced_files(self):
        folder = Path(self.m.ASSET_LIBRARY_DIR) / "group"
        folder.mkdir(parents=True)
        path = folder / "sample.png"
        Image.new("RGB", (2, 2)).save(path)
        unknown = folder / "personal.bin"
        unknown.write_bytes(b"preserve")
        url = "/assets/library/group/sample.png"
        lib = self.m.load_asset_library()
        library = lib["libraries"][0]
        library["categories"].append({"id": "test-cat", "name": "test", "type": "image", "dir": "group", "items": [{"id": "test-item", "url": url, "kind": "image"}]})
        self.m.save_asset_library(lib)
        self.m.save_canvas({"id": "shared", "nodes": [{"url": url}]})
        response = self.client.delete("/api/asset-library/categories/test-cat")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(path.exists())
        self.assertEqual(unknown.read_bytes(), b"preserve")
        self.assertEqual(response.json()["results"][0]["status"], "failed")
        self.assertTrue(list((self.user / "data/media_entry_operations").glob("*.json")))

    def test_library_restore_recovers_only_removed_entry(self):
        path, url = self.image()
        lib = self.m.load_asset_library()
        library = lib["libraries"][0]
        category = next(cat for cat in library["categories"] if cat.get("type") == "image")
        _, item = self.m.make_asset_library_item(str(path), "copied", subdir="")
        category["items"].append(item)
        self.m.save_asset_library(lib)
        response = self.client.delete("/api/asset-library/items/" + item["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"][0]["status"], "trashed")
        current = self.m.load_asset_library()
        current["libraries"][0]["name"] = "later edit"
        self.m.save_asset_library(current)
        trash = self.m.MEDIA_CATALOG.list_trash()["items"][0]
        response = self.client.post("/api/media-trash/restore", headers=self.headers, json={"operation_id": "restore-library-item", "operation_ids": [trash["id"]]})
        self.assertEqual(response.status_code, 200, response.text)
        current = self.m.load_asset_library()
        self.assertEqual(current["libraries"][0]["name"], "later edit")
        self.assertTrue(any(value["id"] == item["id"] for cat in current["libraries"][0]["categories"] for value in cat.get("items", [])))

    def test_rename_preserves_old_static_address(self):
        path, url = self.image()
        response = self.client.patch("/api/local-assets/items", headers=self.headers, json={"path": path.name, "name": "renamed"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(self.m.output_file_from_url(url).endswith("renamed.png"))

    def test_mutating_routes_require_origin_operation_and_purge_confirmation(self):
        response = self.client.put("/api/media-cleanup/settings", json={"enabled": True, "operation_id": "setting-operation"})
        self.assertEqual(response.status_code, 403)
        response = self.client.put("/api/media-cleanup/settings", headers=self.headers, json={"enabled": True})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/media-trash/purge", headers=self.headers, json={"operation_id": "purge-operation", "operation_ids": ["some-id"]})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.m.MEDIA_CATALOG.settings()["enabled"])

    def test_interrupted_entry_removal_recovers_without_overwriting_newer_edit(self):
        path, url = self.image()
        lib = self.m.load_asset_library()
        category = next(cat for cat in lib["libraries"][0]["categories"] if cat.get("type") == "image")
        item = {"id": "recover-entry", "url": url, "kind": "image"}
        category["items"].append(item)
        self.m.save_asset_library(lib)
        category["items"] = []
        journal_path = self.m._media_commit_library_removal(lib)
        current = self.m.load_asset_library()
        current["libraries"][0]["name"] = "newer name"
        self.m.save_asset_library(current)
        self.m._media_recover_entry_operations()
        recovered = self.m.load_asset_library()
        self.assertEqual(recovered["libraries"][0]["name"], "newer name")
        self.assertTrue(any(value["id"] == item["id"] for cat in recovered["libraries"][0]["categories"] for value in cat.get("items", [])))
        self.assertEqual(json.loads(Path(journal_path).read_text())["state"], "recovered")

    def test_prepared_entry_journal_replays_safely(self):
        path, url = self.image()
        lib = self.m.load_asset_library()
        category = next(cat for cat in lib["libraries"][0]["categories"] if cat.get("type") == "image")
        category["items"].append({"id": "prepared-entry", "url": url, "kind": "image"})
        self.m.save_asset_library(lib)
        before = json.loads(Path(self.m.ASSET_LIBRARY_PATH).read_text())
        category["items"] = []
        after = lib
        manifest = self.user / "data/media_entry_operations/prepared.json"
        self.m._media_atomic_json(str(manifest), {"kind": "asset-library", "state": "prepared", "before": before, "after": after})
        self.m.save_asset_library(after)
        self.m._media_recover_entry_operations()
        restored = self.m.load_asset_library()
        self.assertTrue(any(value["id"] == "prepared-entry" for cat in restored["libraries"][0]["categories"] for value in cat.get("items", [])))
        self.assertTrue(path.exists())

    def test_file_restore_reports_pending_if_entry_save_fails(self):
        path, url = self.image()
        lib = self.m.load_asset_library()
        category = next(cat for cat in lib["libraries"][0]["categories"] if cat.get("type") == "image")
        _, item = self.m.make_asset_library_item(str(path), "restore-failure")
        category["items"].append(item)
        self.m.save_asset_library(lib)
        deleted = self.client.delete("/api/asset-library/items/" + item["id"])
        self.assertEqual(deleted.status_code, 200)
        trash_id = deleted.json()["results"][0]["id"]
        with patch.object(self.m, "save_asset_library", side_effect=OSError("simulated index failure")):
            response = self.client.post("/api/media-trash/restore", headers=self.headers, json={"operation_id": "restore-entry-failure", "operation_ids": [trash_id]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"][0]["status"], "file_restored_entry_pending")
        self.assertEqual(response.json()["success"], 0)
        self.assertTrue(self.m.output_file_from_url(item["url"]))
        self.m._media_recover_entry_operations()
        restored = self.m.load_asset_library()
        self.assertTrue(any(value["id"] == item["id"] for cat in restored["libraries"][0]["categories"] for value in cat.get("items", [])))


    def test_keep_is_explicit_and_mutation_authorized(self):
        path, url = self.image()
        asset = self.m.MEDIA_CATALOG.register(path)
        response = self.client.put("/api/media-assets/" + asset["id"] + "/keep", headers=self.headers, json={"operation_id": "keep-operation", "keep": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.m.MEDIA_CATALOG.resolve(url)["keep"])


    def test_active_stream_barrier_blocks_manual_disposal(self):
        path, url = self.image()
        catalog = self.m.MEDIA_CATALOG
        asset = catalog.register(path)
        async def run():
            entered, release = asyncio.Event(), asyncio.Event()
            async def downstream(scope, receive, send):
                entered.set()
                await release.wait()
            middleware = self.m.MediaActivityMiddleware(downstream)
            task = asyncio.create_task(middleware({"type": "http", "method": "GET", "path": url}, None, None))
            await entered.wait()
            self.assertEqual(self.m.MEDIA_ACTIVE_REQUESTS, 1)
            result = catalog.move_to_trash([asset["id"]], "blocked-stream-operation")
            self.assertEqual(result["results"][0]["status"], "failed")
            self.assertTrue(path.exists())
            release.set()
            await task
            self.assertEqual(self.m.MEDIA_ACTIVE_REQUESTS, 0)
        asyncio.run(run())

    def test_shared_library_entry_recycle_restore_and_purge_never_deletes_file(self):
        path, url = self.image()
        lib = self.m.load_asset_library()
        category = next(cat for cat in lib['libraries'][0]['categories'] if cat.get('type') == 'image')
        _, item = self.m.make_asset_library_item(str(path), 'shared-entry', subdir='')
        category['items'].append(item)
        self.m.save_asset_library(lib)
        self.m.save_canvas({'id':'shared-entry-canvas','nodes':[{'url':item['url']}]})
        target = Path(self.m.output_file_from_url(item['url']))
        for restore in (True,False):
            response = self.client.delete('/api/asset-library/items/'+item['id'])
            self.assertEqual(response.status_code,200,response.text)
            rows=self.client.get('/api/media-trash').json()['items']
            row=next(row for row in rows if row.get('metadata_only'))
            self.assertTrue(target.exists())
            route='restore' if restore else 'purge'
            payload={'operation_id':'entry-'+route,'operation_ids':[row['id']],'confirmed':True}
            response=self.client.post('/api/media-trash/'+route,headers=self.headers,json=payload)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['results'][0]['status'],'restored' if restore else 'purged')
            self.assertTrue(target.exists())
            self.assertEqual(self.m.load_canvas('shared-entry-canvas')['nodes'][0]['url'],item['url'])
            entries=[entry for library in self.m.load_asset_library()['libraries'] for cat in library['categories'] for entry in cat['items']]
            self.assertEqual(any(entry['id']==item['id'] for entry in entries),restore)
            again=self.client.post('/api/media-trash/'+route,headers=self.headers,json=payload)
            self.assertEqual(again.status_code,200,again.text)


    def test_batch_duplicate_urls_keep_independent_recoverable_entries(self):
        path, url = self.image()
        lib=self.m.load_asset_library()
        category=next(cat for cat in lib['libraries'][0]['categories'] if cat.get('type')=='image')
        _, first=self.m.make_asset_library_item(str(path),'duplicate',subdir='')
        second={**first,'id':'second-reference','name':'Second entry'}
        category['items'].extend([first,second]);self.m.save_asset_library(lib)
        target=Path(self.m.output_file_from_url(first['url']))
        response=self.client.post('/api/asset-library/items/delete',json={'ids':[first['id'],second['id']]})
        self.assertEqual(response.status_code,200,response.text)
        rows=self.client.get('/api/media-trash').json()['items']
        self.assertEqual(len(rows),2);self.assertTrue(all(row.get('metadata_only') for row in rows))
        self.assertTrue(target.exists())
        response=self.client.post('/api/media-trash/restore',headers=self.headers,json={'operation_id':'duplicate-restore','operation_ids':[rows[0]['id']]})
        self.assertEqual(response.status_code,200,response.text)
        items=[item for library in self.m.load_asset_library()['libraries'] for cat in library['categories'] for item in cat['items']]
        self.assertEqual(len(items),1)
        response=self.client.post('/api/media-trash/purge',headers=self.headers,json={'operation_id':'duplicate-purge','operation_ids':[rows[1]['id']],'confirmed':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
