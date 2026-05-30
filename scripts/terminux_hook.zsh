# Source this from ~/.zshrc:
#   source /path/to/terminux/scripts/terminux_hook.zsh

HOOK_DIR="$(cd "$(dirname "${(%):-%N}")" && pwd)"
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
# Socket emission — fast path avoids spawning the Rust binary per command
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
# Hooks
# ---------------------------------------------------------------------------
typeset -g __terminux_seq=0
typeset -g __terminux_current_seq=0
typeset -g __terminux_started_at=0

__terminux_preexec() {
  [[ "$1" == __terminux_* || "$1" == terminux-daemon* ]] && return
  __terminux_started_at=$(date +%s%3N)

  __terminux_seq=$((__terminux_seq + 1))
  __terminux_current_seq=$__terminux_seq

  local ts cmd cwd json
  ts=$(__terminux_iso_timestamp)
  cmd=$(__terminux_json_escape "$1")
  cwd=$(__terminux_json_escape "$PWD")

  json=$(printf '{"type":"start","seq":%s,"command":"%s","cwd":"%s","timestamp":"%s"}' \
    "$__terminux_current_seq" "$cmd" "$cwd" "$ts")
  __terminux_send "$json"
}

__terminux_precmd() {
  local exit_code=$?
  if [[ "$__terminux_current_seq" -eq 0 ]]; then
    return
  fi

  local ended_at=$(date +%s%3N)
  local duration=$((ended_at - __terminux_started_at))
  local seq=$__terminux_current_seq
  __terminux_current_seq=0

  local json
  json=$(printf '{"type":"end","seq":%s,"exit_code":%s,"duration_ms":%s}' \
    "$seq" "$exit_code" "$duration")
  __terminux_send "$json"
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec __terminux_preexec
add-zsh-hook precmd __terminux_precmd
