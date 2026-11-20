"""Packaging-only checks for the pinned tzdata fallback, independent of OS tzdb."""
import datetime as dt
import hashlib
import importlib.metadata
import importlib.resources
import json
import os
from pathlib import Path
import zoneinfo

TZDATA_VERSION = '2026.6'
PYINSTALLER_ARGS = ['--collect-all', 'tzdata', '--copy-metadata', 'tzdata']
RESOURCE = 'tzdata/zoneinfo/America/New_York'
LICENSES = ('licenses/LICENSE', 'licenses/licenses/LICENSE_APACHE')


def probe():
    version = importlib.metadata.version('tzdata')
    if version != TZDATA_VERSION:
        raise SystemExit('tzdata differs from the pinned packaging version')
    incoming_tzpath = list(zoneinfo.TZPATH)
    incoming_env = os.environ.get('PYTHONTZPATH')
    zoneinfo.reset_tzpath(())
    zoneinfo.ZoneInfo.clear_cache()
    ny = zoneinfo.ZoneInfo('America/New_York')
    cases = {'2025-01-15T12:00:00': -18000, '2025-07-15T12:00:00': -14400,
             '2025-03-09T06:59:00': -18000, '2025-03-09T07:00:00': -14400,
             '2025-11-02T05:59:00': -14400, '2025-11-02T06:00:00': -18000}
    for instant, expected in cases.items():
        offset = dt.datetime.fromisoformat(instant).replace(tzinfo=dt.timezone.utc).astimezone(ny).utcoffset().total_seconds()
        if offset != expected:
            raise SystemExit('New York offset or DST boundary check failed')
    resource = importlib.resources.files('tzdata.zoneinfo.America').joinpath('New_York').read_bytes()
    dist = importlib.metadata.distribution('tzdata')
    files = {RESOURCE: hashlib.sha256(resource).hexdigest()}
    for name in LICENSES:
        relative = f'tzdata-{version}.dist-info/{name}'
        files[relative] = hashlib.sha256(Path(dist.locate_file(relative)).read_bytes()).hexdigest()
    return {'version': version, 'zone': ny.key, 'osTzdbDisabled': not zoneinfo.TZPATH,
            'incomingTZPATH': incoming_tzpath, 'incomingPYTHONTZPATH': incoming_env,
            'incomingOsTzdbDisabled': not incoming_tzpath, 'resetTZPATH': list(zoneinfo.TZPATH),
            'offsetCases': len(cases), 'files': files}


def validate_bundle(bundle, evidence):
    # PyInstaller 6 onedir layout on both supported native targets.
    for relative, expected in evidence['files'].items():
        file = Path(bundle) / '_internal' / relative
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise SystemExit(f'Bundled timezone data/license missing or changed: {relative}')


if __name__ == '__main__':
    print(json.dumps(probe(), indent=2))
