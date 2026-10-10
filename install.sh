#!/usr/bin/env bash
# Hydra installer for Linux and macOS.
# curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash

set -euo pipefail

if ! command -v python3 >/dev/null 2>&1 && ! command -v node >/dev/null 2>&1; then
  echo "[ERROR] Hydra needs Python 3 or Node.js 18+." >&2
  exit 1
fi

if command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -qO "$2" "$1"; }
else
  echo "[ERROR] curl or wget is required." >&2
  exit 1
fi

ROOT="${HOME}/.hydra"
BIN="${HOME}/.local/bin"
REPO="https://raw.githubusercontent.com/erastudil/hydra/main"
FILES="
bin/hydra
bin/hydra.js
hydra_cli/__init__.py
hydra_cli/__main__.py
hydra_cli/_version.py
hydra_cli/agent.py
hydra_cli/agent_runners.py
hydra_cli/alice_gate.py
hydra_cli/alice_interpret.py
hydra_cli/alice_knowledge.py
hydra_cli/alice_retrieve.py
hydra_cli/alice_runner.py
hydra_cli/alice_senses.py
hydra_cli/auth.py
hydra_cli/browser.py
hydra_cli/catalog.json
hydra_cli/cli.py
hydra_cli/computer_use.py
hydra_cli/config.py
hydra_cli/context.py
hydra_cli/desktop.py
hydra_cli/dictation.py
hydra_cli/display.py
hydra_cli/hands.py
hydra_cli/mcp.py
hydra_cli/mcp_registry.py
hydra_cli/mcp_servers.default.json
hydra_cli/native_tools.py
hydra_cli/providers.py
hydra_cli/repl.py
hydra_cli/router.py
hydra_cli/sandbox.py
hydra_cli/serve.py
hydra_cli/speculative.py
hydra_cli/swarm.py
hydra_cli/tool_adapter.py
hydra_cli/tui.py
hydra_cli/ui.py
hydra_cli/voice.py
hydra_cli/win_console.py
"

echo "==> Installing Hydra into ${ROOT}"
for rel in ${FILES}; do
  dest="${ROOT}/${rel}"
  mkdir -p "$(dirname "${dest}")"
  fetch "${REPO}/${rel}" "${dest}"
done
chmod +x "${ROOT}/bin/hydra" "${ROOT}/bin/hydra.js"

mkdir -p "${BIN}"
cat > "${BIN}/hydra" << 'EOF'
#!/bin/sh
ROOT="${HOME}/.hydra"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$ROOT/bin/hydra" "$@"
fi
if command -v node >/dev/null 2>&1; then
  exec node "$ROOT/bin/hydra.js" "$@"
fi
echo "[ERROR] Hydra needs Python 3 or Node.js 18+." >&2
exit 1
EOF
chmod +x "${BIN}/hydra"

case ":${PATH}:" in
  *:"${BIN}":*) ;;
  *)
    echo "==> ${BIN} is not on PATH."
    echo "    Add: export PATH=\"\${HOME}/.local/bin:\${PATH}\""
    ;;
esac

echo "==> Hydra is installed."
echo "    ${BIN}/hydra"
echo "    Put keys in ~/.hydra/.env or the environment."
echo "    hydra setup"
