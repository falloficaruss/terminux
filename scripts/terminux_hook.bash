# Source this from ~/.bashrc:
#   source /path/to/terminux/scripts/terminux_hook.bash

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_DIR="$(cd "${HOOK_DIR}/../daemon" && pwd)"
export PATH="${DAEMON_DIR}/target/release:${DAEMON_DIR}/target/debug:${PATH}"

if ! command -v terminux-daemon >/dev/null 2>&1; then
  return 0
fi

# Auto-start the background daemon if not already running
__terminux_pidfile="${HOME}/.terminux/daemon.pid"
__terminux_start_daemon() {
  if [[ -f "$__terminux_pidfile" ]] && kill -0 "$(cat "$__terminux_pidfile" 2>/dev/null)" 2>/dev/null; then
    return 0
  fi
  mkdir -p "${HOME}/.terminux"
  nohup terminux-daemon daemon >/dev/null 2>&1 &
  echo $! > "$__terminux_pidfile"
  disown 2>/dev/null
}
__terminux_start_daemon

# ---------------------------------------------------------------------------
# Socket emission
# ---------------------------------------------------------------------------
__terminux_json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}

__terminux_iso_timestamp() {
  date -u +%Y-%m-%dT%H:%M:%S.%3NZ
}

__terminux_send_socket() {
  local json="$1"
  local sock="${TERMINUX_SOCK:-${HOME}/.terminux/terminux.sock}"
  [[ -S "$sock" ]] || return 1

  if printf '%s\n' "$json" | nc -U -w1 "$sock" 2>/dev/null; then
    return 0
  fi
  if printf '%s\n' "$json" | socat - "UNIX-CONNECT:$sock" 2>/dev/null; then
    return 0
  fi
  return 1
}

__terminux_send() {
  local json="$1"

  __terminux_send_socket "$json" 2>/dev/null && return 0

  printf '%s\n' "$json" | terminux-daemon send >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Hooks — single complete event per command (bash DEBUG trap reentrancy
# makes a two-message protocol unreliable; zsh gets start/end instead)
# ---------------------------------------------------------------------------
__terminux_started_at=0
__terminux_last_cmd=""

__terminux_preexec() {
  case "${BASH_COMMAND:-}" in
    __terminux_*|terminux-daemon*) return 0 ;;
  esac
  __terminux_started_at=$(date +%s%3N)
  __terminux_last_cmd="${BASH_COMMAND:-}"
}

__terminux_postexec() {
  local exit_code=$?
  if [[ -z "$__terminux_last_cmd" ]]; then
    return
  fi

  local cmd="$__terminux_last_cmd"
  __terminux_last_cmd=

  local ended_at=$(date +%s%3N)
  local duration=$((ended_at - __terminux_started_at))
  local cwd ts cmd_esc cwd_esc json

  ts=$(__terminux_iso_timestamp)
  cmd_esc=$(__terminux_json_escape "$cmd")
  cwd_esc=$(__terminux_json_escape "$PWD")

  json=$(printf '{"command":"%s","cwd":"%s","output":"","exit_code":%s,"duration_ms":%s,"timestamp":"%s"}' \
    "$cmd_esc" "$cwd_esc" "$exit_code" "$duration" "$ts")
  __terminux_send "$json"
}

trap '__terminux_preexec' DEBUG
if [[ "$PROMPT_COMMAND" != *"__terminux_postexec"* ]]; then
  PROMPT_COMMAND="__terminux_postexec${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
fi
