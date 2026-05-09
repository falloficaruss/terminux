#!/bin/bash
exec 3>&1 4>&2
OUT=$(mktemp)
exec > >(tee "$OUT") 2>&1
[ -t 1 ] && echo "Is TTY" || echo "Is NOT TTY"
exec 1>&3 2>&4
rm "$OUT"
