#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_LINE="source ${ROOT_DIR}/scripts/terminux_hook.bash"
OLD_HOOK_LINE="source ${ROOT_DIR}/scripts/terminus_hook.bash"
BASHRC="${HOME}/.bashrc"

if grep -Fq "${OLD_HOOK_LINE}" "${BASHRC}"; then
  tmp_file="$(mktemp)"
  grep -Fvx "${OLD_HOOK_LINE}" "${BASHRC}" > "${tmp_file}" || true
  cat "${tmp_file}" > "${BASHRC}"
  rm -f "${tmp_file}"
  echo "Removed legacy Terminus hook entry from ${BASHRC}"
fi

if grep -Fq "${HOOK_LINE}" "${BASHRC}"; then
  echo "Terminux hook already installed in ${BASHRC}"
  exit 0
fi

echo "${HOOK_LINE}" >> "${BASHRC}"
echo "Installed Terminux hook into ${BASHRC}. Restart shell to activate."
