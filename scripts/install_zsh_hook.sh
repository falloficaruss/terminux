#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_LINE="[[ -f ${ROOT_DIR}/scripts/terminux_hook.zsh ]] && source ${ROOT_DIR}/scripts/terminux_hook.zsh"
ZSHRC="${HOME}/.zshrc"

if grep -Fq "${ROOT_DIR}/scripts/terminux_hook.zsh" "${ZSHRC}" 2>/dev/null; then
  echo "Terminux hook already installed in ${ZSHRC}"
  exit 0
fi

echo "${HOOK_LINE}" >> "${ZSHRC}"
echo "Installed Terminux hook into ${ZSHRC}. Restart shell to activate."
