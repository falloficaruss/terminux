#!/bin/bash
exec 3>&1 4>&2
OUT=$(mktemp)
echo "Start"
exec > >(tee "$OUT") 2>&1
ls -la /tmp
exec 1>&3 2>&4
echo "End"
echo "Captured output:"
cat "$OUT"
rm "$OUT"
