"""Restore the pinned Windows SVG renderer used by the desktop backend."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'resvg_py-0.5.0-cp310-abi3-win_amd64.whl'
URL = 'https://files.pythonhosted.org/packages/fe/53/aa8f92ce6eb2f97095d8b6359a1613c5a5ee0aa9b1a33434df9294362979/' + NAME
SHA256 = '1f6b8956c4143dbfe107bcd35799d0dfd778a40a8cd537893c0bf489898a6c3c'
SIZE = 1242277


def prepare(root=ROOT):
    root = Path(root).resolve()
    cache = root / '.cache'
    cache.mkdir(exist_ok=True)
    wheel = cache / NAME
    if not wheel.exists() or hashlib.sha256(wheel.read_bytes()).hexdigest() != SHA256:
        temporary = wheel.with_suffix('.download')
        try:
            with urllib.request.urlopen(URL, timeout=60) as source:
                raw = source.read(SIZE + 1)
            if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA256:
                raise ValueError('SVG runtime failed SHA256 verification')
            temporary.write_bytes(raw)
            os.replace(temporary, wheel)
        finally:
            temporary.unlink(missing_ok=True)
    target = root / 'backend/static/agent-runtime/svg_renderer'
    with zipfile.ZipFile(wheel) as archive:
        for item in archive.infolist():
            parts = PurePosixPath(item.filename).parts
            if (not parts or parts[0] not in ('resvg_py', 'resvg_py-0.5.0.dist-info') or any(p in ('..', '.') or ':' in p or '\\' in p for p in parts)):
                raise ValueError('Unexpected SVG runtime member')
            if item.is_dir():
                continue
            output = target.joinpath(*parts)
            if any(p.is_symlink() or (p.exists() and getattr(p.stat(), 'st_file_attributes', 0) & 0x400) for p in (output, *output.parents) if p != root):
                raise ValueError('Linked SVG runtime destination')
            raw = archive.read(item)
            if output.is_file() and output.read_bytes() == raw:
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + '.part')
            try:
                temporary.write_bytes(raw)
                os.replace(temporary, output)
            finally:
                temporary.unlink(missing_ok=True)
    print('SVG renderer 0.5.0 verified.')


if __name__ == '__main__':
    prepare()
