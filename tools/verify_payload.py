"""Check release contents and prove the read-only Web source was preserved."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_ROOT = {'API', 'data', 'assets', 'output', 'logs', 'history.json',
                  'nas_config.json', 'media_gateway.json', 'global_config.json'}

def digest(file):
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if sys.version_info >= (3, 11) else hashlib.sha256(stream.read()).hexdigest()

def main():
    manifest_path = ROOT / 'docs/source-fork-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else None
    if manifest:
        original = Path(manifest['source'])
        changed = [item['path'] for item in manifest['files'] if not (original / item['path']).is_file() or digest(original / item['path']) != item['sha256']]
        if changed:
            raise SystemExit('Web source verification failed: ' + ', '.join(changed[:8]))
    source = ROOT / 'backend'
    payload = ROOT / 'dist/win-unpacked/resources/backend'
    if not payload.is_dir():
        raise SystemExit('Build the app first.')
    for forbidden in FORBIDDEN_ROOT:
        if (payload / forbidden).exists():
            raise SystemExit('Private/generated path found in payload: ' + forbidden)
    files = list(payload.rglob('*'))
    for file in files:
        if file.is_file() and (file.name == '.env' or '__pycache__' in file.parts or file.suffix in {'.pyc', '.pyo', '.log'}):
            raise SystemExit('Private/cache file found in payload: ' + str(file.relative_to(payload)))
    required = ['main.py', 'VERSION', 'desktop_entry.py', 'python/python.exe',
                'tools/ffmpeg/ffmpeg.exe', 'tools/ffmpeg/ffprobe.exe', 'py-libs/pytoshop/__init__.py',
                'static/index.html', 'static/js/canvas.js', 'static/js/smart-canvas.js',
                'static/js/api-settings.js', 'static/css/api-settings.css',
                'static/js/desktop-updates.js', 'static/desktop-update-notes.json']
    for rel in required:
        file = payload / rel
        if not file.is_file() or digest(file) != digest(source / rel):
            raise SystemExit('Missing or stale payload resource: ' + rel)
    if (payload / 'tools/dreamina/dreamina.exe').exists():
        raise SystemExit('Optional unbundled CLI found in payload')
    result = {'web_source_unchanged': True if manifest else None,
              'web_source_files_checked': len(manifest['files']) if manifest else 0,
              'required_resources_verified': len(required), 'private_root_paths_absent': True,
              'payload_files': sum(file.is_file() for file in files)}
    (ROOT / '.test-data').mkdir(exist_ok=True)
    (ROOT / '.test-data/payload-verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
