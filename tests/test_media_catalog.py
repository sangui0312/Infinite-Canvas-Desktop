import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/static/media-runtime"))
from media_catalog import DAY, MediaCatalog, MediaError


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-image-content"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="media-catalog-")
        self.root = Path(self.tmp.name)
        (self.root / "assets/input").mkdir(parents=True)
        (self.root / "assets/output").mkdir()
        (self.root / "output").mkdir()
        self.now = 100 * DAY
        self.safe = True
        self.catalog = MediaCatalog(self.root, clock=lambda: self.now, safety_gate=lambda: self.safe, local_origins=["http://127.0.0.1:31300"])

    def tearDown(self):
        self.catalog.close()
        self.tmp.cleanup()

    def image(self, name="input/a.png"):
        path = self.root / "assets" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
        return self.catalog.register(path)

    def source(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def trash(self, aid, operation="delete"):
        result = self.catalog.move_to_trash([aid], operation)
        self.assertEqual(result["results"][0]["status"], "trashed", result)
        return result["results"][0]["id"]

    def test_default_observation_and_reference_resets_clock(self):
        aid = self.image()["id"]
        self.assertFalse(self.catalog.settings()["enabled"])
        self.assertEqual(self.catalog.scan()["assets"][0]["category"], "observing")
        self.now += 31 * DAY
        self.assertEqual(self.catalog.scan()["assets"][0]["category"], "eligible")
        canvas = self.source("data/canvases/a.json", {"deleted": True, "nodes": [{"url": "/assets/input/a.png"}]})
        self.assertEqual(self.catalog.scan()["assets"][0]["category"], "referenced")
        canvas.unlink()
        asset = self.catalog.scan()["assets"][0]
        self.assertEqual(asset["unreferenced_since"], self.now)
        self.assertEqual(asset["category"], "observing")
        self.assertEqual(self.catalog.references(aid)["references"], [])

    def test_svg_and_active_image26_tasks_obey_the_same_lifecycle(self):
        aid=self.image('output/result.svg')['id']
        task=self.source('data/image26-tasks/task.json',{'status':'generating','references':[{'url':'/assets/output/result.svg'}]})
        self.assertEqual(self.catalog.scan()['assets'][0]['category'],'referenced')
        self.assertEqual(self.catalog.move_to_trash([aid],'active-task')['results'][0]['code'],'asset_protected')
        task.write_text(json.dumps({'status':'completed','images':['/assets/output/result.svg']}),encoding='utf-8')
        self.assertEqual(self.catalog.scan()['assets'][0]['category'],'observing')
        journal=self.trash(aid,'svg-trash')
        result=self.catalog.restore([journal],'svg-restore')
        self.assertEqual(result['results'][0]['status'],'restored')

    def test_all_persistent_json_owners_protect(self):
        aid = self.image()["id"]
        sources = {"data/canvases/a.json": "canvas", "data/conversations/u/c.json": "conversation", "history.json": "history", "data/asset_library.json": "asset_library", "workflows/custom.json": "workflow", "data/legacy_imports/job/job.json": "import"}
        for path, kind in sources.items():
            self.source(path, {"deep": [{"url": "/assets/input/a.png?cache=1"}]})
        refs = self.catalog.references(aid)
        self.assertTrue(refs["complete"])
        self.assertEqual({r["kind"] for r in refs["references"]}, set(sources.values()))
        self.assertEqual(self.catalog.move_to_trash([aid], "blocked")["results"][0]["code"], "asset_protected")

    def test_agent_json_columns_and_prose_protect_and_schema_fails_closed(self):
        aid = self.image()["id"]
        path = self.root / "data/agent/agent.sqlite3"
        path.parent.mkdir()
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE runs(data TEXT); CREATE TABLE messages(content TEXT,refs TEXT); CREATE TABLE events(payload TEXT); CREATE TABLE actions(result TEXT);")
            db.execute("INSERT INTO messages VALUES(?,?)", ("See [file](/assets/input/a.png)", "[]"))
        db.close()
        self.assertEqual(self.catalog.references(aid)["references"][0]["kind"], "agent")
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE actions")
        db.close()
        self.assertFalse(self.catalog.scan()["complete"])
        self.assertEqual(self.catalog.move_to_trash([aid], "badagent")["results"][0]["code"], "incomplete_references")

    def test_corrupt_single_source_blocks_every_file(self):
        aid = self.image()["id"]
        path = self.source("data/canvases/bad.json", {})
        path.write_text("{broken", encoding="utf-8")
        scan = self.catalog.scan()
        self.assertFalse(scan["complete"])
        self.assertEqual(scan["assets"][0]["category"], "unknown")
        self.assertIsNone(self.catalog.resolve(aid)["unreferenced_since"])
        self.assertEqual(self.catalog.move_to_trash([aid], "bad")["failed"], 1)
        self.assertTrue((self.root / "assets/input/a.png").exists())

    def test_alias_remote_origin_encoding_and_same_basename(self):
        a = self.image()["id"]
        b = self.image("output/a.png")["id"]
        self.assertEqual(self.catalog.resolve("/assets/input/%61.png?x=y")["id"], a)
        self.assertEqual(self.catalog.resolve("http://127.0.0.1:31300/assets/input/a.png")["id"], a)
        self.assertIsNone(self.catalog.resolve("https://remote.invalid/assets/input/a.png"))
        self.assertIsNone(self.catalog.resolve("/assets/%252e%252e/output/a.png"))
        self.assertIsNone(self.catalog.resolve("/assets/../output/a.png"))
        self.assertEqual(self.catalog.resolve("/output/a.png")["id"], b)
        self.assertNotEqual(a, b)

    def test_external_and_custom_roots_and_hardlinks_excluded(self):
        aid = self.image()["id"]
        external = self.root / "external.png"
        external.write_bytes(PNG)
        with self.assertRaises(MediaError):
            self.catalog.register(external)
        with self.assertRaises(MediaError):
            MediaCatalog(self.root, roots={"custom": self.root / "external"})
        self.source("data/storage_settings.json", {"upload": str(self.root / "custom")})
        self.assertIsNone(self.catalog.resolve("/api/storage-files/upload/a.png"))
        linked = self.root / "assets/input/link.png"
        os.link(self.root / "assets/input/a.png", linked)
        result = self.catalog.move_to_trash([aid], "hardlink")
        self.assertEqual(result["results"][0]["code"], "nonexclusive_file")
        self.assertTrue(external.exists())

    def test_task_read_and_stale_session_protect_across_restart(self):
        aid = self.image()["id"]
        self.catalog.acquire_lease("read", [aid], "read")
        self.assertEqual(self.catalog.move_to_trash([aid], "readblock")["failed"], 1)
        self.catalog.release_lease("read", "read")
        self.catalog.sync_session("window", 1, {"undo": ["/assets/input/a.png"]})
        self.now += 100 * DAY
        self.catalog.close()
        self.catalog = MediaCatalog(self.root, clock=lambda: self.now, safety_gate=lambda: True)
        asset = self.catalog.scan()["assets"][0]
        self.assertEqual(asset["category"], "observing")
        self.assertEqual(asset["unreferenced_since"], self.now)
        with self.assertRaises(MediaError):
            self.catalog.sync_session("window", 1, [])
        self.catalog.sync_session("window", 2, [], closed=True)
        self.assertEqual(self.catalog.scan()["assets"][0]["category"], "observing")

    def test_trash_restore_conflict_sidecars_and_idempotency(self):
        self.image()
        path = self.root / "assets/input/a.png"
        text = self.root / "assets/input/a.txt"
        text.write_text("description", encoding="utf-8")
        aid = self.catalog.register(path)["id"]
        tid = self.trash(aid)
        self.assertFalse(path.exists())
        self.assertFalse(text.exists())
        path.write_bytes(PNG + b"new")
        restored = self.catalog.restore([tid], "restore")
        item = restored["results"][0]
        self.assertEqual(item["status"], "restored", restored)
        self.assertTrue(item["conflict"])
        self.assertEqual(path.read_bytes(), PNG + b"new")
        self.assertEqual(Path(self.catalog.resolve_alias("/assets/input/a.png")).read_bytes(), PNG)
        self.assertEqual(self.catalog.restore([tid], "restore"), restored)
        with self.assertRaises(MediaError):
            self.catalog.restore(["other"], "restore")
        self.assertTrue((self.root / "assets" / Path(item["rel"]).with_suffix(".txt")).exists())

    def test_final_reference_recheck_and_file_replacement_cancel(self):
        aid = self.image()["id"]
        scan = self.catalog.scan()
        self.source("data/canvases/new.json", {"url": "/assets/input/a.png"})
        result = self.catalog.move_to_trash([aid], "new-ref", scan_id=scan["id"])
        self.assertEqual(result["results"][0]["code"], "asset_protected")
        (self.root / "data/canvases/new.json").unlink()
        scan = self.catalog.scan()
        (self.root / "assets/input/a.png").write_bytes(PNG + b"replaced")
        result = self.catalog.move_to_trash([aid], "replace", scan_id=scan["id"])
        self.assertEqual(result["results"][0]["code"], "file_changed")

    def test_purge_rechecks_new_reference_and_confirmation(self):
        aid = self.image()["id"]
        tid = self.trash(aid)
        with self.assertRaises(MediaError):
            self.catalog.purge([tid], "notconfirmed")
        canvas = self.source("data/canvases/late.json", {"url": "/assets/input/a.png"})
        result = self.catalog.purge([tid], "protectedpurge", confirmed=True)
        self.assertEqual(result["results"][0]["code"], "asset_protected")
        canvas.unlink()
        result = self.catalog.purge([tid], "purge", confirmed=True)
        self.assertEqual(result["results"][0]["status"], "purged", result)
        self.assertEqual(self.catalog.resolve(aid)["state"], "purged")
        self.assertFalse(self.catalog.ensure_references([aid])["complete"])

    def test_failed_sidecar_move_rolls_back_and_recovers(self):
        aid = self.image()["id"]
        sidecar = self.root / "assets/input/a.png.txt"
        sidecar.write_text("sidecar")
        self.catalog.register(self.root / "assets/input/a.png")
        original = Path.rename
        def fail_second(path, target):
            if path == sidecar:
                raise OSError("simulated sharing violation")
            return original(path, target)
        with patch.object(Path, "rename", fail_second):
            result = self.catalog.move_to_trash([aid], "interrupted")
        self.assertEqual(result["failed"], 1)
        self.assertTrue((self.root / "assets/input/a.png").exists())
        self.assertTrue(sidecar.exists())
        self.assertEqual(self.catalog.resolve(aid)["state"], "active")

    def test_automatic_setting_delay_disable_and_expiry(self):
        aid = self.image()["id"]
        self.catalog.scan()
        settings = self.catalog.update_settings({"enabled": True})
        self.assertEqual(settings["not_before"], self.now + 30 * DAY)
        self.now += 31 * DAY
        self.assertEqual(self.catalog.run_automatic()["status"], "complete")
        self.assertEqual(self.catalog.resolve(aid)["state"], "trash")
        self.catalog.update_settings({"enabled": False})
        self.now += 8 * DAY
        self.assertEqual(self.catalog.run_automatic()["status"], "deferred")
        self.assertEqual(self.catalog.resolve(aid)["state"], "trash")
        self.catalog.update_settings({"enabled": True, "retention_days": 7})
        self.assertGreaterEqual(self.catalog.settings()["not_before"], self.now + DAY)

    def test_task_gate_and_second_process_fail_closed(self):
        aid = self.image()["id"]
        self.safe = False
        self.assertEqual(self.catalog.move_to_trash([aid], "busy")["results"][0]["code"], "active_tasks_or_reads")
        second = MediaCatalog(self.root, safety_gate=lambda: True)
        try:
            self.assertFalse(second.exclusive)
            self.assertEqual(second.move_to_trash([aid], "second")["results"][0]["code"], "another_process")
            self.safe = True
            self.assertEqual(self.catalog.move_to_trash([aid], "first-also-blocked")["results"][0]["code"], "another_process")
        finally:
            second.close()

    def test_ensure_undo_restores_then_session_protects(self):
        aid = self.image()["id"]
        self.trash(aid)
        result = self.catalog.ensure_references(["/assets/input/a.png"])
        self.assertTrue(result["complete"], result)
        self.assertIn(aid, result["restored"])
        self.catalog.sync_session("undo", 1, ["/assets/input/a.png"])
        self.assertEqual(self.catalog.move_to_trash([aid], "undo-block")["failed"], 1)

    def test_unknown_format_never_becomes_candidate(self):
        (self.root / "assets/input/invalid.png").write_bytes(b"not media")
        scan = self.catalog.scan()
        self.assertEqual(scan["assets"], [])
        self.assertEqual(scan["skipped"][0]["code"], "unrecognized_format")

    def test_legacy_output_alias_survives_trash_and_restart(self):
        file = self.root / "output/legacy.png"
        file.write_bytes(PNG)
        aid = self.catalog.register(file)["id"]
        self.trash(aid)
        self.catalog.close()
        self.catalog = MediaCatalog(self.root, safety_gate=lambda: True)
        self.assertEqual(self.catalog.resolve("/output/legacy.png")["id"], aid)
        result = self.catalog.ensure_references(["/output/legacy.png"])
        self.assertTrue(result["complete"], result)
        self.assertTrue(file.exists())

    def test_shared_companion_preserved_and_restored_metadata_available(self):
        a = self.image("input/a.png")
        self.image("input/b.png")
        shared = self.root / "assets/input/shared.txt"
        shared.write_text("shared")
        self.catalog.register(self.root / "assets/input/a.png", companions=[shared])
        self.catalog.register(self.root / "assets/input/b.png", companions=[shared])
        result = self.catalog.move_to_trash([a["id"]], "shared", related_entries={"item": "snapshot"})
        self.assertEqual(result["success"], 1, result)
        self.assertTrue(shared.exists())
        self.assertTrue((self.root / "assets/input/b.png").exists())
        tid = result["results"][0]["id"]
        self.catalog.restore([tid], "shared-restore")
        self.assertEqual(self.catalog.restored_related_entries()[0]["related_entries"], {"item": "snapshot"})
        self.catalog.acknowledge_related_entries([tid])
        self.assertEqual(self.catalog.restored_related_entries(), [])
        self.assertTrue(shared.exists())

    def test_move_with_sidecars_and_directory_preserves_old_aliases(self):
        a = self.image("input/a.png")
        (self.root / "assets/input/a.txt").write_text("description")
        self.catalog.register(self.root / "assets/input/a.png")
        moved = self.catalog.move_asset(a["id"], self.root / "assets/input/renamed.png")
        self.assertEqual(moved["rel"], "input/renamed.png")
        self.assertTrue((self.root / "assets/input/renamed.txt").exists())
        self.assertEqual(self.catalog.resolve("/assets/input/a.png")["id"], a["id"])
        result = self.catalog.move_directory(self.root / "assets/input", self.root / "assets/moved")
        self.assertEqual(result["updated"], 1)
        self.assertTrue((self.root / "assets/moved/renamed.txt").exists())
        self.assertEqual(Path(self.catalog.resolve_alias("/assets/input/a.png")), self.root / "assets/moved/renamed.png")

    def test_recover_journaled_move_and_incomplete_trash(self):
        a = self.image()
        source = self.root / "assets/input/a.png"
        target = self.root / "assets/input/new.png"
        identity = self.catalog._identity(source)
        with self.catalog._db() as db:
            db.execute("INSERT INTO file_moves VALUES('interrupted','prepared',?)", (json.dumps({"source": str(source), "destination": str(target), "directory": False, "identities": {"": identity}}),))
        source.rename(target)
        self.catalog.close()
        self.catalog = MediaCatalog(self.root, safety_gate=lambda: True)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())
        tid = self.trash(a["id"])
        with self.catalog._db() as db:
            db.execute("UPDATE trash_operations SET state='moving' WHERE id=?", (tid,))
            db.execute("UPDATE assets SET state='active' WHERE id=?", (a["id"],))
        self.catalog.close()
        self.catalog = MediaCatalog(self.root, safety_gate=lambda: True)
        self.assertTrue(source.exists())
        self.assertEqual(self.catalog.resolve(a["id"])["state"], "active")

    def test_auto_purge_stops_when_referenced_again(self):
        a = self.image()
        tid = self.trash(a["id"])
        self.catalog.update_settings({"enabled": True})
        self.now += 31 * DAY
        self.source("data/canvases/restored.json", {"url": "/assets/input/a.png"})
        result = self.catalog.run_automatic()
        self.assertEqual(result["purge"]["results"][0]["code"], "asset_protected")
        self.assertEqual(self.catalog.list_trash()["items"][0]["id"], tid)

    def test_session_heartbeat_does_not_invalidate_scan_and_unknown_blocks(self):
        self.image()
        self.catalog.sync_session("heartbeat", 1, ["/assets/input/a.png"])
        scan = self.catalog.scan()
        self.catalog.sync_session("heartbeat", 2, ["/assets/input/a.png"])
        self.assertEqual(self.catalog._revision, scan["version"])
        self.catalog.sync_session("unknown", 1, ["/assets/input/missing.png"])
        self.assertFalse(self.catalog.scan()["complete"])

    def test_recoverable_import_owns_copies_before_canvas_commit(self):
        aid = self.image("imports/job/partial.png")["id"]
        self.source("data/legacy_imports/job/job.json", {"phase": "ready", "id": "job"})
        self.assertEqual(self.catalog.references(aid)["references"][0]["kind"], "import")

    def test_source_symlink_fails_closed(self):
        a = self.image()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "canvas.json").write_text(json.dumps({"url": "/assets/input/a.png"}))
        try:
            os.symlink(outside, self.root / "data/canvases", target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit creating directory symlinks")
        scan = self.catalog.scan()
        self.assertFalse(scan["complete"])
        self.assertEqual(self.catalog.move_to_trash([a["id"]], "symlink")["failed"], 1)

    def test_supported_and_excluded_session_refs_and_failed_sync_marker(self):
        self.image()
        result = self.catalog.sync_session("audio", 1, ["/assets/input/sound.mp3", "/assets/input/doc.pdf"])
        self.assertFalse(result["uncertain"])
        self.assertTrue(self.catalog.ensure_references(["/assets/input/sound.mp3"])["complete"])
        self.catalog.mark_session_uncertain("broken")
        self.assertFalse(self.catalog.scan()["complete"])
        self.now += 31 * DAY
        self.assertTrue(self.catalog.scan()["complete"])

    def test_unchanged_large_video_scan_avoids_rehash(self):
        video = self.root / "assets/output/large.mp4"
        with video.open("wb") as stream:
            stream.write(b"\x00\x00\x00\x18ftypisom")
            stream.truncate(32 * 1024 * 1024)
        self.catalog.register(video)
        with patch.object(self.catalog, "_identity", side_effect=AssertionError("unchanged scan must not rehash")):
            scan = self.catalog.scan()
        self.assertTrue(scan["complete"], scan["errors"])
        self.assertEqual(scan["assets"][0]["size"], 32 * 1024 * 1024)

    def test_new_large_video_hash_cancellable_without_holding_coordinator(self):
        video = self.root / "assets/output/new-large.mp4"
        with video.open("wb") as stream:
            stream.write(b"\x00\x00\x00\x18ftypisom")
            stream.truncate(128 * 1024 * 1024)
        acquired = threading.Event()
        workers, calls = [], [0]
        def acquire():
            with self.catalog.coordinated():
                acquired.set()
        def cancel():
            calls[0] += 1
            if calls[0] == 2:
                worker = threading.Thread(target=acquire)
                worker.start()
                workers.append(worker)
                acquired.wait(0.5)
            return calls[0] >= 3
        result = self.catalog.scan(cancel=cancel)
        for worker in workers:
            worker.join(1)
        self.assertTrue(acquired.is_set(), "hashing blocked the coordinator")
        self.assertFalse(result["complete"])
        self.assertIn("cancelled", {e["code"] for e in result["errors"]})
        self.assertIsNone(self.catalog.resolve("/assets/output/new-large.mp4"))


    def test_automatic_purge_reaches_expired_entries_after_first_page(self):
        asset=self.image()
        tid=self.trash(asset['id'])
        self.catalog.update_settings({'enabled':True,'retention_days':7})
        self.now+=8*DAY
        oldest=self.catalog.list_trash()['items'][0]
        newest=[{'id':'new-'+str(i),'expires':self.now+7*DAY,'state':'trashed'} for i in range(200)]
        rows=newest+[oldest]
        def page(offset=0,limit=100):
            return {'items':rows[offset:offset+limit],'total':len(rows)}
        with patch.object(self.catalog,'list_trash',side_effect=page) as listing:
            result=self.catalog.run_automatic()
            self.assertEqual(result['purge']['results'][0]['status'],'purged')
            self.assertIn(200,[call.kwargs.get('offset') for call in listing.call_args_list])
        self.assertEqual(self.catalog.resolve(asset['id'])['state'],'purged')


if __name__ == "__main__":
    unittest.main()
