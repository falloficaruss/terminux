# Source this from ~/.zshrc:
#   source /path/to/terminux/scripts/terminux_hook.zsh

HOOK_DIR="$(cd "$(dirname "${(%):-%N}")" && pwd)"
DAEMON_DIR="$(cd "${HOOK_DIR}/../daemon" && pwd)"
export PATH="${DAEMON_DIR}/target/release:${DAEMON_DIR}/target/debug:${PATH}"

if ! command -v terminux-daemon >/dev/null 2>&1; then
  return 0
fi

# ---------------------------------------------------------------------------
# Auto-start the background daemon
# ---------------------------------------------------------------------------
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
# script-based output capture
# ---------------------------------------------------------------------------
__terminux_tty=$(tty 2>/dev/null | tr '/' '_' || echo "unknown")
__terminux_session_log="${HOME}/.terminux/log${__terminux_tty}"

__terminux_ensure_script_session() {
  [[ "${TERMINUX_SCRIPT:-0}" == "1" ]] && return 0
  command -v script >/dev/null 2>&1 || return 0

  mkdir -p "${HOME}/.terminux"
  exec script -q -f "$__terminux_session_log" -c "TERMINUX_SCRIPT=1 zsh -i"
}
__terminux_ensure_script_session

# ---------------------------------------------------------------------------
# ANSI escape cleanup
# ---------------------------------------------------------------------------
__terminux_clean_output() {
  local ESC
  ESC=$(printf '\x1b')
  sed -E "
    s/${ESC}\[[0-9;]*[a-zA-Z]//g
    s/${ESC}\][^\x07]*\x07//g
    s/${ESC}\][^\x1b\\\\]*\x1b\\\\//g
    s/${ESC}[PX^_].*?${ESC}\\\\//g
    s/\r//g
    s/\x07//g
  "
}

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
# Output capture
# ---------------------------------------------------------------------------
__terminux_output_offset=0

__terminux_capture_output() {
  local logfile="$__terminux_session_log"
  local max_bytes=4096

  [[ "${TERMINUX_SCRIPT:-0}" != "1" ]] && return 0
  [[ -f "$logfile" ]] || return 0

  local filesize
  filesize=$(stat -c%s "$logfile" 2>/dev/null || echo 0)
  [[ "$filesize" -le "$__terminux_output_offset" ]] && return 0

  local raw
  raw=$(dd if="$logfile" bs=1 skip="$__terminux_output_offset" count="$max_bytes" 2>/dev/null)

  __terminux_output_offset=$filesize

  if [[ "$filesize" -gt 1048576 ]]; then
    tail -c 102400 "$logfile" > "${logfile}.tmp" 2>/dev/null && mv "${logfile}.tmp" "$logfile"
    __terminux_output_offset=0
  fi

  local cleaned
  cleaned=$(printf '%s' "$raw" | __terminux_clean_output | tr -s '\n' '\n' | sed '/^[[:space:]]*$/d')
  cleaned=$(printf '%s' "$cleaned" | sed '1d')

  printf '%s' "$cleaned"
}

# ---------------------------------------------------------------------------
# Hooks — two-message protocol (start/end) for zsh
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

  # Record file offset after the prompt, before command runs
  if [[ "${TERMINUX_SCRIPT:-0}" == "1" ]] && [[ -f "$__terminux_session_log" ]]; then
    __terminux_output_offset=$(stat -c%s "$__terminux_session_log" 2>/dev/null || echo 0)
  fi
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

  local output
  output=$(__terminux_capture_output)
  local output_esc
  output_esc=$(__terminux_json_escape "$output")

  local json
  json=$(printf '{"type":"end","seq":%s,"exit_code":%s,"duration_ms":%s,"output":"%s"}' \
    "$seq" "$exit_code" "$duration" "$output_esc")
  __terminux_send "$json"
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec __terminux_preexec
add-zsh-hook precmd __terminux_precmd
