"""Reconstruct exact published program baselines, then apply/rollback real patches."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_patch_engine", ROOT / "desktop/apply_update.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def inventory(directory):
    return {p.relative_to(directory).as_posix(): engine.digest(p)
            for p in directory.rglob("*") if p.is_file() and engine.allowed(p.relative_to(directory).as_posix())}


def main():
    manifest = json.loads((ROOT / "dist/hybrid-update.json").read_text(encoding="utf-8"))
    target = json.loads((ROOT / "dist/release-tree.json").read_text(encoding="utf-8"))
    proofs = []
    with tempfile.TemporaryDirectory(prefix="release-patches-", dir=ROOT / ".test-data") as folder:
        workspace = Path(folder)
        for patch in manifest.get("patches", []):
            version = patch["fromVersion"]
            baseline = json.loads((ROOT / f"release-baselines/release-tree-{version}.json").read_text(encoding="utf-8"))
            app, transaction = workspace / version / "app", workspace / version / "transaction"
            for rel, expected in baseline["program"].items():
                if not engine.allowed(rel):
                    raise ValueError("Unsupported baseline path")
                candidate = ROOT / "dist/win-unpacked/resources/backend" / rel
                data = candidate.read_bytes() if candidate.is_file() and engine.digest(candidate) == expected else subprocess.check_output(["git", "show", f"v{version}:backend/{rel}"], cwd=ROOT)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError(f"Published baseline content mismatch: {version}/{rel}")
                destination = app / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            personal = app / "userdata/preserve.json"
            personal.parent.mkdir()
            personal.write_text('{"personal":"preserve"}', encoding="utf-8")
            before = engine.digest(personal)
            archive = ROOT / "dist" / patch["name"]
            if engine.digest(archive) != patch["sha256"] or archive.stat().st_size != patch["size"]:
                raise ValueError("Patch asset mismatch")
            args = [str(app), str(transaction), str(archive), manifest["version"]]
            engine.main(["apply", *args])
            if inventory(app) != target["program"]:
                raise ValueError("Post-apply program differs from full installer")
            engine.main(["rollback", *args])
            if inventory(app) != baseline["program"]:
                raise ValueError("Rollback differs from published baseline")
            engine.main(["apply", *args])
            engine.main(["confirm", *args])
            if inventory(app) != target["program"] or engine.digest(personal) != before:
                raise ValueError("Confirmation or personal-data preservation failed")
            proofs.append({"from": version, "to": manifest["version"], "apply": True,
                           "rollback": True, "confirm": True, "personal_preserved": True})
    output = ROOT / ".test-data" / f"release-{manifest['version']}-patch-verification.json"
    output.write_text(json.dumps(proofs, indent=2), encoding="utf-8")
    print(json.dumps({"patches_verified": len(proofs), "proofs": proofs}))


if __name__ == "__main__":
    main()
