"""Export sanitized painter snapshots or restore missing painters without overwriting."""

import argparse
import hashlib
import json
from pathlib import Path

from paintai.api import create_app
from paintai.config import Settings
from paintai.storage import now
from paintai.transfer import Transfer

ROOT = Path(__file__).resolve().parents[1]


def run(data_dir, snapshot_dir, export=False):
    app = create_app(Settings(data_dir=data_dir), start_worker=False)
    store = app.state.store
    transfer = Transfer(store)
    if export:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for painter in store.painters():
            pid = painter['painter_id']
            raw = transfer.export(pid)
            manifest, _, _ = transfer.read(raw)
            if len(raw) >= 90 * 1024 * 1024:
                raise ValueError('Snapshot exceeds Git file budget; do not commit: ' + pid)
            filename = pid + '.zip'
            (snapshot_dir / filename).write_bytes(raw)
            items.append({'painter_id': pid, 'name': painter['name'], 'file': filename,
                          'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                          'creations': len(manifest['tables']['creations']),
                          'images': len(manifest['files'])})
        index = {'version': 1, 'exported_at': now(), 'painters': items}
        (snapshot_dir / 'index.json').write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(index, ensure_ascii=False, indent=2))
        return
    index_path = snapshot_dir / 'index.json'
    if not index_path.exists():
        print('No repository data snapshot; nothing to restore.')
        return
    index = json.loads(index_path.read_text(encoding='utf-8'))
    if index['version'] != 1:
        raise ValueError('Unsupported repository snapshot version')
    existing = {p['painter_id'] for p in store.painters()}
    for item in index['painters']:
        if item['painter_id'] in existing:
            print('Keep existing painter: ' + item['painter_id'])
            continue
        path = (snapshot_dir / item['file']).resolve()
        if path.parent != snapshot_dir.resolve() or path.suffix != '.zip':
            raise ValueError('Invalid snapshot path')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Snapshot checksum mismatch: ' + item['file'])
        manifest, _, _ = transfer.read(raw)
        if manifest['painter_id'] != item['painter_id']:
            raise ValueError('Snapshot painter mismatch')
        restored = transfer.restore(raw)
        print('Restored ' + item['painter_id'] + ': ' + json.dumps(restored['counts']))
        existing.add(item['painter_id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--snapshot-dir', type=Path, default=ROOT / 'snapshots' / 'studio')
    parser.add_argument('--export', action='store_true')
    args = parser.parse_args()
    run(args.data_dir, args.snapshot_dir, args.export)
