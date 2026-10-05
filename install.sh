#!/usr/bin/env bash
# ==============================================================================
# Hydra Installer for Linux and macOS
# Usage: curl -fsSL https://raw.githubusercontent.com/erastudil/hydra/main/install.sh | bash
# ==============================================================================

set -euo pipefail

COLOR_RESET="\033[0m"
COLOR_CYAN="\033[1;36m"
COLOR_GREEN="\033[1;32m"
COLOR_YELLOW="\033[1;33m"
COLOR_RED="\033[1;31m"

echo -e "${COLOR_CYAN}"
cat << "EOF"
  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
      Sovereign Multi-Headed AI Shell
EOF
echo -e "${COLOR_RESET}"

INSTALL_DIR="${HOME}/.local/bin"
TARGET="${INSTALL_DIR}/hydra"
REPO_RAW="https://raw.githubusercontent.com/erastudil/hydra/main"

echo -e "==> Preparing installation..."
mkdir -p "${INSTALL_DIR}"

# Download standalone runner
echo -e "==> Downloading latest Hydra bundle..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "${REPO_RAW}/bin/hydra.js" -o "${TARGET}.js"
    curl -fsSL "${REPO_RAW}/bin/hydra" -o "${TARGET}"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "${TARGET}.js" "${REPO_RAW}/bin/hydra.js"
    wget -qO "${TARGET}" "${REPO_RAW}/bin/hydra"
else
    echo -e "${COLOR_RED}Error: curl or wget required for installation.${COLOR_RESET}"
    exit 1
fi

chmod +x "${TARGET}"
chmod +x "${TARGET}.js"

# Verify executable runtime (prefer node or python3)
if command -v python3 >/dev/null 2>&1; then
    echo -e "==> Python 3 detected ($(python3 --version))"
elif command -v node >/dev/null 2>&1; then
    echo -e "==> Node.js detected ($(node --version))"
    # Link js version to hydra if python is absent
    mv "${TARGET}.js" "${TARGET}"
fi

# PATH check
case ":${PATH}:" in
    *:"${INSTALL_DIR}":*) ;;
    *)
        echo -e "${COLOR_YELLOW}==> Note: ${INSTALL_DIR} is not in your current PATH.${COLOR_RESET}"
        echo -e "    Add it by appending this line to your ~/.bashrc or ~/.zshrc:"
        echo -e "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
        ;;
esac

echo -e "${COLOR_GREEN}==> Hydra successfully installed to ${TARGET}${COLOR_RESET}"
echo -e ""
echo -e "Quickstart:"
echo -e "  export OPENROUTER_API_KEY=\"your_key_here\""
echo -e "  hydra opus 5.5 \"Explain zero-cost abstractions\""
echo -e "  hydra free \"List 3 axioms of distributed systems\""
echo -e "  hydra local \"Write a fast LRU cache in Go\""
echo -e "  hydra swarm \"Architect a high-throughput event pipeline\""
echo -e ""
