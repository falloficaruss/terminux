#!/bin/bash
# Mock terminux-daemon to verify hook behavior
LOG=/home/falloficaruss/terminux/scratch/daemon_mock.log
echo "DAEMON CALLED: $@" >> "$LOG"
if [[ "$1" == "emit-from-file" ]]; then
    # find --output-file arg
    args=("$@")
    for i in "${!args[@]}"; do
        if [[ "${args[i]}" == "--output-file" ]]; then
            file="${args[i+1]}"
            echo "OUTPUT FILE CONTENT BEGIN" >> "$LOG"
            cat "$file" >> "$LOG"
            echo "OUTPUT FILE CONTENT END" >> "$LOG"
        fi
    done
fi
