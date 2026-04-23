#!/usr/bin/env bash
# Sync this project to the server and (optionally) run install.sh.
# Usage: ./deploy.sh user@host [--install]
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 user@host [--install]" >&2
  exit 1
fi

TARGET="$1"
shift || true
DO_INSTALL=0
[[ "${1:-}" == "--install" ]] && DO_INSTALL=1

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="/opt/wireguard-helper/src"

echo "==> Rsyncing to ${TARGET}:${REMOTE_DIR}"
ssh "${TARGET}" "sudo mkdir -p ${REMOTE_DIR} && sudo chown \$USER ${REMOTE_DIR}"
rsync -az --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '*.egg-info' --exclude '.venv' \
  "${PROJECT_DIR}/" "${TARGET}:${REMOTE_DIR}/"

if [[ ${DO_INSTALL} -eq 1 ]]; then
  echo "==> Running install.sh on ${TARGET}"
  ssh -t "${TARGET}" "sudo bash ${REMOTE_DIR}/install.sh"
fi

echo "Done."
