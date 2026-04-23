#!/usr/bin/env bash
# Run on the Ubuntu server after syncing the project directory.
# Creates a venv, installs deps, drops `wgh` into /usr/local/bin.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo $0)" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="/opt/wireguard-helper/venv"
BIN_LINK="/usr/local/bin/wgh"

echo "==> Ensuring python3 + venv"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip

echo "==> Creating venv at ${VENV_DIR}"
mkdir -p "$(dirname "${VENV_DIR}")"
if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

echo "==> Installing package"
"${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
"${VENV_DIR}/bin/pip" install "${PROJECT_DIR}"

echo "==> Linking ${BIN_LINK}"
ln -sf "${VENV_DIR}/bin/wgh" "${BIN_LINK}"

echo
echo "Installed. Next steps:"
echo "  sudo wgh bootstrap       # one-time server setup"
echo "  sudo wgh add             # create a peer"
echo "  sudo wgh list            # see peers"
