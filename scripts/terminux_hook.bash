# Source this file from ~/.bashrc after building daemon:
# source /path/to/terminux/scripts/terminux_hook.bash

# 1. Session Wrapping logic:
# If we are in an interactive shell and not already being captured,
# start a new shell session wrapped in 'script' to capture all output.
if [[ $- == *i* ]] && [[ -z "$TERMINUX_CAPTURING" ]]; then
  if command -v terminux-daemon >/dev/null 2>&1 && command -v script >/dev/null 2>&1; then
    export TERMINUX_CAPTURING=1
    export TERMINUX_LOG=$(mktemp /tmp/terminux.$(id -u).XXXXXX.log)
    exec script -q -f "$TERMINUX_LOG"
  fi
fi

# Ensure log is cleaned up on exit (set in the captured session)
if [[ -n "$TERMINUX_LOG" ]]; then
  trap 'rm -f "$TERMINUX_LOG"' EXIT
fi

if ! command -v terminux-daemon >/dev/null 2>&1; then
  return 0
fi

__terminux_active=0
__terminux_started_at=0
__terminux_last_offset=0

__terminux_preexec() {
  __terminux_active=1
  __terminux_started_at=$(date +%s%3N)
  if [[ -n "$TERMINUX_LOG" && -f "$TERMINUX_LOG" ]]; then
    __terminux_last_offset=$(stat -c%s "$TERMINUX_LOG")
  else
    __terminux_last_offset=0
  fi
}

__terminux_postexec() {
  local exit_code=$?
  if [[ "$__terminux_active" != "1" ]]; then
    return
  fi
  __terminux_active=0

  local ended_at=$(date +%s%3N)
  local duration=$((ended_at - __terminux_started_at))
  # Use history 1 to get the expanded command (including aliases)
  local last_cmd=$(history 1 | sed 's/^ *[0-9]\+ *//')

  if [[ -n "$last_cmd" ]]; then
    if [[ -n "$TERMINUX_LOG" && -f "$TERMINUX_LOG" ]]; then
      local current_offset=$(stat -c%s "$TERMINUX_LOG")
      local len=$((current_offset - __terminux_last_offset))
      if [[ $len -gt 0 ]]; then
        local delta_file=$(mktemp /tmp/terminux_delta.XXXXXX)
        # Extract the delta from the log file using tail starting from the last recorded offset
        tail -c +$((__terminux_last_offset + 1)) "$TERMINUX_LOG" > "$delta_file"
        # Strip ANSI escape sequences to provide clean text for the daemon
        # This handles colors, cursor movements, and other terminal codes
        sed -i 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$delta_file"
        # Also strip some common script(1) artifacts if they appear in the delta
        sed -i '/^Script \(started\|done\) on/d' "$delta_file"
        
        terminux-daemon emit-from-file \
          --command "$last_cmd" \
          --cwd "$PWD" \
          --exit-code "$exit_code" \
          --duration-ms "$duration" \
          --output-file "$delta_file" >/dev/null 2>&1 || true
        rm -f "$delta_file"
        return
      fi
    fi

    # Fallback if no log file is available
    terminux-daemon emit \
      --command "$last_cmd" \
      --cwd "$PWD" \
      --exit-code "$exit_code" \
      --duration-ms "$duration" >/dev/null 2>&1 || true
  fi
}

trap '__terminux_preexec' DEBUG
# Append to existing PROMPT_COMMAND to preserve other hooks (like zoxide, direnv)
if [[ "$PROMPT_COMMAND" != *"__terminux_postexec"* ]]; then
  PROMPT_COMMAND="__terminux_postexec${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
fi

