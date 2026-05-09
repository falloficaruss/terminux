#!/bin/bash
export PATH="/home/falloficaruss/terminux/scratch:$PATH"
ln -sf /home/falloficaruss/terminux/scratch/mock_daemon.sh /home/falloficaruss/terminux/scratch/terminux-daemon
chmod +x /home/falloficaruss/terminux/scratch/terminux-daemon

rm -f /home/falloficaruss/terminux/scratch/daemon_mock.log

cat > /home/falloficaruss/terminux/scratch/temp_bashrc <<EOF
# Minimal bashrc for testing
PS1='> '
source /home/falloficaruss/terminux/scripts/terminux_hook.bash
EOF

echo "Testing output capture..."
# We use 'expect' or just pipe input to bash. 
# But 'script' might consume the pipe.
# Let's try to just run a command that we know will have output.
export SHELL="/bin/bash"
# We need to force interactive mode and rcfile
# script -c "bash --rcfile /home/falloficaruss/terminux/scratch/temp_bashrc -i" ...
# Actually, the hook does 'exec script', so we just need to start the first bash.

# We'll use a timeout because script might hang if not exited
echo "echo 'HELLO WORLD'; exit" | bash --rcfile /home/falloficaruss/terminux/scratch/temp_bashrc -i

echo "Checking mock daemon log..."
cat /home/falloficaruss/terminux/scratch/daemon_mock.log
