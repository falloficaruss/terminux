# Source this file from ~/.zshrc after building daemon:
# source /path/to/terminux/scripts/terminux_hook.zsh

# Append daemon binary directory to PATH so we can find terminux-daemon
HOOK_DIR="$(cd "$(dirname "${(%):-%N}")" && pwd)"
DAEMON_DIR="$(cd "${HOOK_DIR}/../daemon" && pwd)"
export PATH="${DAEMON_DIR}/target/release:${DAEMON_DIR}/target/debug:${PATH}"

# 1. Session Wrapping logic:
# If we are in an interactive shell and not already being captured,
# start a new shell session wrapped in 'script' to capture all output.
if [[ -o interactive ]] && [[ -z "$TERMINUX_CAPTURING" ]]; then
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

typeset -g __terminux_active=0
typeset -g __terminux_started_at=0
typeset -g __terminux_last_offset=0
typeset -g __terminux_cmd=""

__terminux_preexec() {
  __terminux_active=1
  __terminux_started_at=$(date +%s%3N)
  __terminux_cmd="$1"
  if [[ -n "$TERMINUX_LOG" && -f "$TERMINUX_LOG" ]]; then
    __terminux_last_offset=$(stat -c%s "$TERMINUX_LOG")
  else
    __terminux_last_offset=0
  fi
}

__terminux_precmd() {
  local exit_code=$?
  if [[ "$__terminux_active" != "1" ]]; then
    return
  fi
  __terminux_active=0

  local ended_at=$(date +%s%3N)
  local duration=$((ended_at - __terminux_started_at))

  if [[ -n "$__terminux_cmd" ]]; then
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
          --command "$__terminux_cmd" \
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
      --command "$__terminux_cmd" \
      --cwd "$PWD" \
      --exit-code "$exit_code" \
      --duration-ms "$duration" >/dev/null 2>&1 || true
  fi
}

# Register hook functions cleanly using Zsh built-in autoloads
autoload -Uz add-zsh-hook
add-zsh-hook preexec __terminux_preexec
add-zsh-hook precmd __terminux_precmd
