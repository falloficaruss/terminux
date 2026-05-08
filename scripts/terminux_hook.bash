# Source this file from ~/.bashrc after building daemon:
# source /path/to/terminux/scripts/terminux_hook.bash

if ! command -v terminux-daemon >/dev/null 2>&1; then
  return 0
fi

__terminux_last_cmd=""
__terminux_started_at=0

__terminux_preexec() {
  __terminux_started_at=$(date +%s%3N)
  __terminux_last_cmd=$(history 1 | sed 's/^ *[0-9]\+ *//')
}

__terminux_postexec() {
  local exit_code=$?
  local ended_at
  local duration

  ended_at=$(date +%s%3N)
  duration=$((ended_at - __terminux_started_at))

  if [ -n "$__terminux_last_cmd" ]; then
    terminux-daemon emit \
      --command "$__terminux_last_cmd" \
      --cwd "$PWD" \
      --exit-code "$exit_code" \
      --duration-ms "$duration" >/dev/null 2>&1 || true
  fi

  __terminux_last_cmd=""
}

trap '__terminux_preexec' DEBUG
PROMPT_COMMAND='__terminux_postexec'
