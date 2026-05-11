#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_LINE="source ${ROOT_DIR}/scripts/terminux_hook.bash"
BASHRC="${HOME}/.bashrc"


if grep -Fq "${HOOK_LINE}" "${BASHRC}"; then
  echo "Terminux hook already installed in ${BASHRC}"
  exit 0
fi

echo "${HOOK_LINE}" >> "${BASHRC}"
echo "Installed Terminux hook into ${BASHRC}. Restart shell to activate."
