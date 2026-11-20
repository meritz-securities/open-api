#!/usr/bin/env bash
# Developer build only. Customers run the resulting folder without Python or Docker.
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'The reviewed customer-code runner currently supports macOS only.' >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo 'Node.js/npm is required to build the bundled Studio UI.' >&2
  exit 1
fi
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if (!((major===20&&minor>=19)||(major>=22&&(major>22||minor>=12)))) { console.error("Use Node.js 20.19+ or 22.12+."); process.exit(1); }'
(cd studio && npm ci && npm run build)
UV_PROJECT_ENVIRONMENT=.strategy-build uv sync --frozen --all-extras --dev
mcp_version="$(.strategy-build/bin/python -c 'from importlib.metadata import version; print(version("mcp"))')"
uv pip install --python .strategy-build/bin/python 'pyinstaller==6.22.3' "mcp[cli]==${mcp_version}"
output_root="${STRATEGY_LAB_OUTPUT_ROOT:-local-bundle}"
work_root="${STRATEGY_LAB_WORK_ROOT:-build/strategy-lab}"
source_root="$PWD"
mkdir -p "$output_root"
mkdir -p "$work_root"
.strategy-build/bin/pyinstaller --noconfirm --onedir \
  --name meritz-strategy-lab --distpath "$output_root" --workpath "$work_root" --specpath "$work_root" \
  --collect-all fastmcp --collect-submodules mcp --collect-all pydantic \
  --collect-all meritz --collect-submodules runtime --collect-submodules analytics \
  --collect-submodules optimizer --collect-submodules reports --collect-submodules indicators \
  --collect-submodules meritz_strategy --hidden-import statistics --hidden-import decimal \
  --hidden-import fractions --paths "$source_root" --paths "$source_root/sdk" \
  --add-data "$source_root/studio/dist/index.html:studio/dist" \
  --add-data "$source_root/runtime/strategy.schema.json:runtime" \
  --add-data "$source_root/strategies:strategies" \
  --add-data "$source_root/sdk/meritz_strategy/__init__.py:sdk/meritz_strategy" \
  --add-data "$source_root/indicators/calc.py:indicators" \
  --log-level WARN "$source_root/strategy_app_entry.py"
cp LICENSE THIRD-PARTY-NOTICES.txt "$output_root/meritz-strategy-lab/"
cp meritz/ui/THIRD_PARTY_NOTICES.txt "$output_root/meritz-strategy-lab/UI-LICENSES.txt"
cat > "$output_root/meritz-strategy-lab/Start.command" <<'LAUNCH'
#!/bin/zsh
cd "${0:A:h}"
exec ./meritz-strategy-lab
LAUNCH
chmod +x "$output_root/meritz-strategy-lab/Start.command"
echo "Local unsigned review build: $output_root/meritz-strategy-lab/Start.command"
