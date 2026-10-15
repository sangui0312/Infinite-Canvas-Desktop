"""Build the GitHub hybrid update manifest and per-baseline program-only deltas.

Policy (2026-10-15): non-major releases carry patches for every known baseline
so each client picks the pack matching its own version; major-version jumps
(1.x -> 2.x) always use the full installer, and shell-changing builds fall back
to the installer unless drifting foundations are explicitly allowed.
"""
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


def version_key(value):
    return tuple(int(chunk) if str(chunk).isdigit() else 0 for chunk in str(value or "").split("."))


def major_of(value):
    return str(value or "").split(".")[0]


def build_patch(backend, output, version, program, baseline):
    files = {
        rel: {"size": (backend / rel).stat().st_size, "sha256": value}
        for rel, value in program.items()
        if baseline.get("program", {}).get(rel) != value
    }
    if not files:
        return None
    if "desktop-app-version.json" not in files:
        raise ValueError("Functional version must advance for file updates")
    patch = {
        "schema": 1,
        "fromVersion": baseline["version"],
        "version": version,
        "baseFiles": baseline["program"],
        "files": files,
        "remove": sorted(set(baseline.get("program", {})) - set(program)),
    }
    name = f"InfiniteCanvas-Files-{baseline['version']}-to-{version}.zip"
    archive = output / name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
        bundle.writestr("patch.json", json.dumps(patch, ensure_ascii=False))
        for rel in files:
            bundle.write(backend / rel, "files/" + rel)
    return {
        "name": name,
        "url": f"https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v{version}/{name}",
        "fromVersion": baseline["version"],
        "size": archive.stat().st_size,
        "sha256": digest(archive),
    }


def generate(backend, desktop, package, output, baselines=None, primary="", require_foundation=False):
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
    manifest = {
        "schema": 1,
        "version": version,
        "minimumShell": metadata["minimumShell"],
        "mode": "installer",
        "notes": json.loads((backend / "static/desktop-update-notes.json").read_text(encoding="utf-8"))["notes"],
    }
    patches = []
    for baseline in baselines or []:
        if not isinstance(baseline, dict) or "version" not in baseline or "program" not in baseline:
            continue
        from_version = str(baseline["version"])
        if major_of(from_version) != major_of(version) or version_key(from_version) >= version_key(version):
            continue
        if require_foundation and baseline.get("foundation") != foundation:
            continue
        built = build_patch(backend, output, version, program, baseline)
        if built:
            patches.append(built)
    patches.sort(key=lambda item: version_key(item["fromVersion"]))
    if patches:
        chosen = next((item for item in patches if item["fromVersion"] == str(primary or "")), None) or patches[-1]
        manifest.update(mode="files", patches=patches, patch=chosen)
    (output / "hybrid-update.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "release-tree.json").write_text(json.dumps(tree, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, action="append", default=[])
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--primary", type=str, default="")
    parser.add_argument("--require-foundation", action="store_true")
    args = parser.parse_args()
    baselines = []
    if args.baseline_dir and args.baseline_dir.is_dir():
        for file in sorted(args.baseline_dir.glob("release-tree-*.json")):
            baselines.append(json.loads(file.read_text(encoding="utf-8")))
    for file in args.baseline or []:
        baselines.append(json.loads(file.read_text(encoding="utf-8")))
    manifest = generate(
        ROOT / "dist/win-unpacked/resources/backend",
        ROOT / "desktop",
        json.loads((ROOT / "package.json").read_text()),
        ROOT / "dist",
        baselines,
        args.primary,
        args.require_foundation,
    )
    print(json.dumps({
        "version": manifest["version"],
        "mode": manifest["mode"],
        "patches": [item["name"] for item in manifest.get("patches", [])],
        "primary": manifest.get("patch", {}).get("name"),
    }))
