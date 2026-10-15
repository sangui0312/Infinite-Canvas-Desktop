import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apply_patch_test", ROOT / "desktop/apply_update.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="patch-test-", dir=ROOT / ".test-data")
        self.root = Path(self.temp.name)
        self.app, self.work = self.root / "app", self.root / "updates"
        self.app.mkdir()
        (self.app / "static").mkdir()
        (self.app / "main.py").write_text("old-main")
        (self.app / "static/removed.js").write_text("old")
        (self.app / "desktop-app-version.json").write_text(json.dumps({"version": "1.0.0", "protocol": 1}))
        self.base = {p.relative_to(self.app).as_posix(): engine.digest(p) for p in self.app.rglob("*") if p.is_file()}
        self.payload = {"main.py": b"new-main", "static/added.js": b"added", "desktop-app-version.json": json.dumps({"version": "1.0.1", "protocol": 1}).encode()}
        self.archive = self.root / "patch.zip"
        self.write()

    def write(self, extra=None):
        files = {key: {"size": len(value), "sha256": __import__("hashlib").sha256(value).hexdigest()} for key, value in self.payload.items()}
        manifest = {"version": "1.0.1", "fromVersion": "1.0.0", "baseFiles": self.base, "files": files, "remove": ["static/removed.js"]}
        with zipfile.ZipFile(self.archive, "w") as z:
            z.writestr("patch.json", json.dumps(manifest))
            for rel, value in self.payload.items():
                z.writestr("files/" + rel, value)
            if extra:
                z.writestr(extra, "unexpected")

    def tearDown(self):
        self.temp.cleanup()

    def run_engine(self, command):
        engine.main([command, str(self.app), str(self.work), str(self.archive), "1.0.1"])

    def test_apply_and_restart_recovery_restore_add_modify_and_delete_without_personal_changes(self):
        personal = self.root / "userdata.json"
        personal.write_text("personal")
        self.run_engine("apply")
        self.assertEqual(engine.version(self.app), "1.0.1")
        self.assertFalse((self.app / "static/removed.js").exists())
        self.run_engine("rollback")
        self.assertEqual(engine.version(self.app), "1.0.0")
        self.assertEqual((self.app / "main.py").read_text(), "old-main")
        self.assertFalse((self.app / "static/added.js").exists())
        self.assertTrue((self.app / "static/removed.js").exists())
        self.assertEqual(personal.read_text(), "personal")

    def test_success_confirmation_keeps_updated_files(self):
        self.run_engine("apply")
        self.run_engine("confirm")
        self.run_engine("rollback")
        self.assertEqual(engine.version(self.app), "1.0.1")

    def test_modified_base_wrong_version_and_traversal_are_rejected(self):
        (self.app / "main.py").write_text("local modification")
        with self.assertRaises(ValueError):
            self.run_engine("inspect")
        (self.app / "main.py").write_text("old-main")
        self.write("files/../userdata/API/.env")
        with self.assertRaises(ValueError):
            self.run_engine("apply")
        self.write()
        with self.assertRaises(ValueError):
            engine.inspect(self.app, self.archive, "9.0.0")

    def test_mid_apply_io_failure_rolls_back_all_operations(self):
        replace = engine.os.replace
        failed = False
        def fail_once(source, destination):
            nonlocal failed
            if str(destination).endswith("added.js") and not failed:
                failed = True
                raise OSError("synthetic failure")
            return replace(source, destination)
        with patch.object(engine.os, "replace", fail_once):
            with self.assertRaises(OSError):
                self.run_engine("apply")
        self.assertEqual((self.app / "main.py").read_text(), "old-main")
        self.assertEqual(engine.version(self.app), "1.0.0")


if __name__ == "__main__":
    unittest.main()
