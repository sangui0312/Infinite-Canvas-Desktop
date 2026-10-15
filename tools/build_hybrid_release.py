"""Build a GitHub hybrid manifest and optional exact-base program-only delta."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("patch_policy", ROOT / "desktop/apply_update.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def digest(file):
    return policy.digest(file)


def generate(backend, desktop, package, output, baseline=None):
    output.mkdir(parents=True, exist_ok=True)
    version = package["version"]
    metadata = json.loads((backend / "desktop-app-version.json").read_text(encoding="utf-8"))
    if metadata["version"] != version:
        raise ValueError("Embedded functional version mismatch")
    program, foundation = {}, {}
    for file in sorted(backend.rglob("*")):
        if not file.is_file():
            continue
        rel = file.relative_to(backend).as_posix()
        if "__pycache__" in file.parts or file.suffix in {".pyc", ".pyo"}:
            continue
        if rel.split("/")[0] in {"API", "data", "assets", "output", "logs"} or file.name == ".env":
            raise ValueError("Private data in release inventory")
        (program if policy.allowed(rel) else foundation)[rel] = digest(file)
    for file in sorted(desktop.rglob("*")):
        if file.is_file():
            foundation["desktop/" + file.relative_to(desktop).as_posix()] = digest(file)
    config = {key: value for key, value in package.items() if key != "version"}
    foundation["package-config"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    foundation["installer.nsh"] = digest(ROOT / "build/installer.nsh")
    tree = {"version": version, "foundation": foundation, "program": program}
    manifest = {"schema": 1, "version": version, "minimumShell": metadata["minimumShell"], "mode": "installer", "notes": json.loads((backend / "static/desktop-update-notes.json").read_text(encoding="utf-8"))["notes"]}
    if baseline and baseline["foundation"] == foundation and policy.version(backend) == version:
        files = {rel: {"size": (backend / rel).stat().st_size, "sha256": value} for rel, value in program.items() if baseline["program"].get(rel) != value}
        if "desktop-app-version.json" not in files:
            raise ValueError("Functional version must advance for file updates")
        patch = {"schema": 1, "fromVersion": baseline["version"], "version": version, "baseFiles": baseline["program"], "files": files, "remove": sorted(set(baseline["program"]) - set(program))}
        name = f"InfiniteCanvas-Files-{baseline['version']}-to-{version}.zip"
        archive = output / name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
            bundle.writestr("patch.json", json.dumps(patch, ensure_ascii=False))
            for rel in files:
                bundle.write(backend / rel, "files/" + rel)
        manifest.update(mode="files", patch={"name": name, "url": f"https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v{version}/{name}", "fromVersion": baseline["version"], "size": archive.stat().st_size, "sha256": digest(archive)})
    (output / "hybrid-update.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "release-tree.json").write_text(json.dumps(tree, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    manifest = generate(ROOT / "dist/win-unpacked/resources/backend", ROOT / "desktop", json.loads((ROOT / "package.json").read_text()), ROOT / "dist", baseline)
    print(json.dumps({"version": manifest["version"], "mode": manifest["mode"], "patch": manifest.get("patch", {}).get("name")}))
