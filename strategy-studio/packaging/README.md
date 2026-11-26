# Native local review bundle

Run `build_native.py` on each target operating system using a Python environment
with the project's locked dependencies and PyInstaller 6.22.3 already installed.
`macos-arm64-build-requirements.txt` pins the complete tested Python 3.11 macOS
build environment. The native app bundles MCP server/client modules directly and
does not need the optional `mcp` console CLI extras.
Regenerate it from `uv.lock` with `python packaging/export_macos_pins.py`; use
`--check` to confirm that it matches the lock file. Create a separate environment
and install that file; do not update the customer
installation in place. This macOS snapshot is not a Windows lockfile. `uv.lock`
contains the project dependency resolution and platform-specific markers.
Both files must be audited again when dependencies change.
The script does not install software or change existing installations. Build the
single-file Studio page with the Studio project's `npm run build` first.

macOS:

```sh
/path/to/build-env/bin/python packaging/build_native.py --source-root /path/to/source --output-root /private/tmp/new-platform-build
```

Windows PowerShell (native Windows build required):

```powershell
& C:\build-env\Scripts\python.exe packaging\build_native.py --source-root C:\source --output-root C:\builds\new-platform-build
```

The output is `dist/meritz-strategy-platform/`. Keep the entire directory together.
`Start.command` / `Start.cmd` launches Studio. On macOS,
`Connect-to-Claude.command` adds a strategy-only MCP entry to Claude Desktop's
local configuration, preserving other settings and creating a timestamped
backup. It uses `--strategy-mcp`, which exposes strategy and backtest tools but
no account, order, or FX tools. Fully quit and reopen Claude Desktop afterward.
The executable also accepts `--mcp`, `--cli ACTION --arguments JSON --root DATA_DIRECTORY`, and the internal
`--strategy-worker` entry. The worker dispatch remains before application imports.
Bundled source data includes the trusted SDK and indicator code required by the
isolated worker. The macOS worker uses the bundled Python runtime.

This is an **unsigned portable bundle, not a signed production installer**. During the beta,
`.github/workflows/release-studio.yml` builds it on GitHub Actions (macOS arm64 with the pinned
environment, macOS x64 and Windows x64 from `uv.lock`), runs the automated checks and uploads the
`.dmg`/`.zip` to a `studio-v*` prerelease with the OS first-open steps in the release notes.
For the signed post-beta installers, macOS signing/notarization, Windows Authenticode signing of
the NSIS installer, upgrade/uninstall behavior, clean-machine installation and Windows functional
tests remain release gates; the beta prerelease ships without them and says so.
The Windows x64 build packages Wasmtime and CPython/WASI. Network access to PyPI
and GitHub is needed during packaging. The installed app runs its worker offline.
The native smoke test checks a Python backtest through the CLI and Studio HTTP.

`build-manifest.json` records build input hashes and rejects changes to tracked
inputs during compilation. Keep this development manifest outside customer
artifacts: its command contains local build paths. Bundled third-party notices are
existing notices, not a completed dependency-license audit of the new Studio.
No credentials, settings or customer data are intentionally copied. Complete
artifact contents and dependency/license review remain required before release.

For a native macOS developer-preview app and DMG, wrap the verified onedir:

```sh
python3 packaging/wrap_macos.py --bundle /path/to/dist/meritz-strategy-platform --output /path/to/new-app-output
```

The app retains the onedir below `Contents/MacOS/runtime` and launches it through
`Contents/MacOS/launch`. Do not place the frozen binary directly inside `MacOS`:
the PyInstaller app-layout detection changes its runtime root and can break the
worker sandbox. Validate SDK execution and all sandbox probes from the final app
path, not only from the original build folder. `wrap_macos.py` checks CLI templates
and the DMG checksum; those checks alone are not SDK/sandbox or install acceptance.

Security fixes are pinned in `uv.lock` and the native-build requirements snapshot.
A plain `pip install .` into an existing environment does **not** enforce every
transitive security fix. Use the locked environment for testing and packaging;
do not treat a successful unlocked install as equivalent to the audited build.
The macOS arm64 builder rejects an installed distribution set that differs from
the snapshot and records dependencies and lockfile hashes in its manifest.
The snapshot pins versions, not downloaded artifact hashes; build-only packages
not covered by `uv.lock` still need artifact-integrity controls before release.
Advisory scans do not cover vendored copies or native libraries automatically.

Windows installer preparation is separate from the native builder.
`packaging/windows/prepare_installer.py` validates a real Windows AMD64 bundle
and emits an immutable payload snapshot plus NSIS 3 source with status
`PREPARED_UNTESTED`. It does not compile an installer or approve dependencies.
`packaging/windows/verify_native.py` is a Windows-only synthetic smoke runner for
the original native bundle, not an installation/uninstall acceptance test.
See [Windows preparation and verification gaps](../docs/release/windows-installation.md).


## Packaged New York timezone fallback

`tzdata==2026.6` is a direct project dependency on all platforms, with wheel and
source archive hashes in `uv.lock`. The macOS arm64 snapshot currently contains **91**
packages. Updating dependencies requires updating the lock and build snapshot, then
repeating the audit and native probe. Updating tzdata also requires changing the
`timezone_support.py` version and
`TZDATA-NOTICES.txt`, then repeating the audit and native probe.

The builder runs `timezone_support.py` with `PYTHONTZPATH=''` before compilation.
The probe also clears ZoneInfo caches and resets its search path, checking New
York winter/summer offsets and both DST transition boundaries (six cases).
PyInstaller receives `--collect-all tzdata --copy-metadata tzdata`. After building,
the builder requires byte-identical New York TZif data and both distribution
license files below `_internal`, and records those hashes in `timezoneSupport`.
All copied resources are also covered by the existing full bundle hash manifest.
Missing or altered timezone data/licenses fail the build; OS tzdb cannot make
these resource checks pass. The manifest alone is not a target-machine test.

The wheel metadata declares Apache-2.0. `TZDATA-NOTICES.txt` retains both original
license texts, and the original dist-info license files are included too. This
checks the added distribution's license materials, not all dependencies' legal
compliance. Upstream: https://pypi.org/project/tzdata/2026.6/ and
https://github.com/python/tzdata .

A scoped frozen probe can be built in a separate output directory using the same
collection flags:

```sh
/path/to/pinned-env/bin/python -m PyInstaller --onedir --name timezone-probe \
  --distpath /path/to/new-probe/dist --workpath /path/to/new-probe/work \
  --specpath /path/to/new-probe --collect-all tzdata --copy-metadata tzdata \
  packaging/timezone_support.py
PYTHONTZPATH='' /path/to/new-probe/dist/timezone-probe/timezone-probe
```

On Windows, set `$env:PYTHONTZPATH=''` and execute `timezone-probe.exe` from a
native Windows build. The macOS probe does not certify Windows or the full
application. Before release, rebuild the complete target bundle, run the existing
build-provenance audit gate, and test the exact target executable without OS tzdb.
No Windows installation/upgrade/uninstall acceptance has been completed here.
