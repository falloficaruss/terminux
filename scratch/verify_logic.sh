#!/bin/bash
export PATH="/home/falloficaruss/terminux/scratch:$PATH"
ln -sf /home/falloficaruss/terminux/scratch/mock_daemon.sh /home/falloficaruss/terminux/scratch/terminux-daemon
chmod +x /home/falloficaruss/terminux/scratch/terminux-daemon

rm -f /home/falloficaruss/terminux/scratch/daemon_mock.log

# We skip the session wrapping in this test by setting TERMINUX_CAPTURING
export TERMINUX_CAPTURING=1
export TERMINUX_LOG=/home/falloficaruss/terminux/scratch/test_session.log
echo -n "" > "$TERMINUX_LOG"

source /home/falloficaruss/terminux/scripts/terminux_hook.bash

echo "Simulating command: ls"
__terminux_preexec
echo "file1 file2" >> "$TERMINUX_LOG"
# We need to make sure history has something
history -s "ls -l"
__terminux_postexec

echo "Checking mock daemon log..."
cat /home/falloficaruss/terminux/scratch/daemon_mock.log
