"""Verify or restore the pinned Windows runtime without using the original web tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('backend/python/', 'backend/py-libs/', 'backend/tools/ffmpeg/')
MAX_EXPANDED = 2 * 1024**3


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    if (not name or '\\' in name or ':' in name or name.startswith('/')
            or any(p in ('', '.', '..') for p in name.split('/'))
            or not any(name.startswith(prefix) for prefix in PREFIXES)
            or any(p.endswith((' ', '.')) for p in parts)
            or any(p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *('COM%d' % i for i in range(1, 10)),
                *('LPT%d' % i for i in range(1, 10))} for p in parts)):
        raise ValueError('Unsafe runtime member: ' + name)
    return name


def verify_files(root, manifest):
    for name, entry in manifest['files'].items():
        safe_name(name)
        path = root / name
        if linked_destination(path, root):
            return False
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['size']:
            return False
        if digest(path) != entry['sha256']:
            return False
    return bool(manifest['files'])


def linked_destination(path, root):
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            return True
        if parent.exists() and getattr(parent.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400:
            return True
    return False


def validate_zip(archive, manifest):
    expected = manifest['files']
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = safe_name(info.filename)
            folded = name.casefold()
            if folded in seen or name not in expected:
                raise ValueError('Unexpected or duplicate runtime member: ' + name)
            seen.add(folded)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.is_dir() or info.flag_bits & 1:
                raise ValueError('Unsupported runtime member: ' + name)
            total += info.file_size
            if info.file_size != expected[name]['size'] or total > MAX_EXPANDED:
                raise ValueError('Invalid runtime member size: ' + name)
        if seen != {name.casefold() for name in expected}:
            raise ValueError('Runtime archive is incomplete')


def install(archive, root, manifest):
    validate_zip(archive, manifest)
    cache = root / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='runtime-stage-', dir=cache) as staging:
        stage = Path(staging)
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                target = stage / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
        if not verify_files(stage, manifest):
            raise ValueError('Extracted runtime failed SHA256 verification')
        # Only replace pinned files. Never remove local files or touch user data.
        for name in manifest['files']:
            target = root / name
            if linked_destination(target, root):
                raise ValueError('Runtime destination contains a link: ' + str(target))
        for name in manifest['files']:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / name, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'runtime-manifest.json')
    parser.add_argument('--archive', type=Path, help='Use a pinned local ZIP for offline setup')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if verify_files(root, manifest) and not args.archive:
        print('Runtime is complete and verified.')
        return
    if args.verify_only:
        raise SystemExit('Runtime is missing or has changed; run prepare_runtime.py.')
    asset = manifest['asset']
    archive = args.archive
    if archive is None:
        url = asset['url']
        required = 'https://github.com/sangui0312/Infinite-Canvas-Desktop/releases/download/v0.2.0/'
        if url != required + asset['name']:
            raise ValueError('Runtime URL is not the pinned public release URL')
        cache = root / '.cache'
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / asset['name']
        if not archive.is_file() or archive.stat().st_size != asset['size'] or digest(archive) != asset['sha256']:
            temporary = archive.with_suffix('.download')
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'InfiniteCanvas-Runtime/1'})
                with urllib.request.urlopen(request, timeout=120) as src, temporary.open('wb') as dst:
                    received = 0
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > asset['size']:
                            raise ValueError('Runtime download exceeds pinned size')
                        dst.write(chunk)
                if temporary.stat().st_size != asset['size'] or digest(temporary) != asset['sha256']:
                    raise ValueError('Downloaded runtime failed size/SHA256 verification')
                os.replace(temporary, archive)
            finally:
                temporary.unlink(missing_ok=True)
    if archive.stat().st_size != asset['size'] or digest(archive) != asset['sha256']:
        raise ValueError('Runtime archive failed size/SHA256 verification')
    install(archive, root, manifest)
    print('Runtime restored and verified.')


if __name__ == '__main__':
    main()
