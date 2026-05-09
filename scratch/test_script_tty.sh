#!/bin/bash
OUT=$(mktemp)
script -q -c "[ -t 1 ] && echo 'Is TTY' || echo 'Is NOT TTY'" "$OUT"
echo "Captured output:"
cat "$OUT"
rm "$OUT"
