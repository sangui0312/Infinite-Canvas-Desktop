"""Create the deterministic, credential-free release runtime and pinned manifest."""
import argparse
import email.parser
import json
from pathlib import Path
import zipfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prepare_runtime import ROOT, PREFIXES, digest, safe_name, validate_zip

ASSET = 'InfiniteCanvas-Runtime-windows-x64-v1.zip'
EXCLUDED = {'__pycache__', 'logs', 'log', 'credentials', 'home', '.git', '.cache'}


def included(path):
    return (not any(p.lower() in EXCLUDED or p.startswith('.') for p in path.parts)
            and path.suffix.lower() not in {'.pyc', '.pyo', '.log'}
            and not any(p.lower() in {'token.json', 'cookies.json', 'credentials.json'} for p in path.parts))


def build(root, output, manifest_path):
    files = {}
    sources = {}
    for prefix in PREFIXES:
        base = root / prefix
        if not base.is_dir():
            raise ValueError('Missing runtime directory: ' + prefix)
        for path in sorted(base.rglob('*')):
            if path.is_symlink() or getattr(path.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Runtime source contains a symbolic link: ' + str(path))
            relative = path.relative_to(root)
            if path.is_file() and included(relative):
                name = safe_name(relative.as_posix())
                files[name] = {'size': path.stat().st_size, 'sha256': digest(path)}
                sources[name] = path
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.zip.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with sources[name].open('rb') as src, bundle.open(info, 'w', force_zip64=True) as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)
        manifest = {'schema': 1, 'platform': 'windows-x64', 'runtime_version': 1,
            'asset': {'name': ASSET, 'url': 'https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v0.2.0/' + ASSET,
                      'size': temporary.stat().st_size, 'sha256': digest(temporary)},
            'excluded_optional': ['backend/tools/dreamina/dreamina.exe'], 'files': files}
        validate_zip(temporary, manifest)
        temporary.replace(output)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps({'asset': manifest['asset'], 'file_count': len(files)}, indent=2))
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ASSET)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'runtime-manifest.json')
    args = parser.parse_args()
    build(args.root.resolve(), args.output, args.manifest)
