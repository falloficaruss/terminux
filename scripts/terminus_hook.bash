# Source this file from ~/.bashrc after building daemon:
# source /path/to/terminus/scripts/terminus_hook.bash

if ! command -v terminus-daemon >/dev/null 2>&1; then
  return 0
fi

__terminus_last_cmd=""
__terminus_started_at=0

__terminus_preexec() {
  __terminus_started_at=$(date +%s%3N)
  __terminus_last_cmd=$(history 1 | sed 's/^ *[0-9]\+ *//')
}

__terminus_postexec() {
  local exit_code=$?
  local ended_at
  local duration

  ended_at=$(date +%s%3N)
  duration=$((ended_at - __terminus_started_at))

  if [ -n "$__terminus_last_cmd" ]; then
    terminus-daemon emit \
      --command "$__terminus_last_cmd" \
      --cwd "$PWD" \
      --exit-code "$exit_code" \
      --duration-ms "$duration" >/dev/null 2>&1 || true
  fi

  __terminus_last_cmd=""
}

trap '__terminus_preexec' DEBUG
PROMPT_COMMAND='__terminus_postexec'
