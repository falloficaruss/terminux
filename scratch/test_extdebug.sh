#!/bin/bash
shopt -s extdebug

__terminux_preexec() {
    local cmd="$BASH_COMMAND"
    # Avoid recursion
    if [[ "$cmd" == terminux-daemon* || "$cmd" == __terminux* ]]; then return 0; fi
    
    echo "Preexec: $cmd"
    local first_word=$(echo "$cmd" | awk '{print $1}')
    local type=$(type -t "$first_word")
    
    if [[ "$type" == "file" ]]; then
        echo "Wrapping external command: $cmd"
        __terminux_out_file=$(mktemp)
        script -q -c "$cmd" "$__terminux_out_file" > /dev/null
        __terminux_last_exit_code=$?
        __terminux_captured=1
        return 1 # Skip original command
    fi
    return 0
}

__terminux_postexec() {
    local exit_code=$?
    if [[ "$__terminux_captured" == "1" ]]; then
        echo "Postexec: Captured output from $__terminux_out_file"
        # cat "$__terminux_out_file"
        rm "$__terminux_out_file"
        __terminux_captured=0
    else
        echo "Postexec: Normal command, exit code $exit_code"
    fi
}

trap '__terminux_preexec' DEBUG
PROMPT_COMMAND='__terminux_postexec'

# Trigger a command
ls -d /tmp
cd /tmp
pwd
