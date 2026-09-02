#!/usr/bin/env bash
# Exercise examples/kimventure.sh end to end against the fake monitor.
# Synthetic tapes stand in for the game, which is not ours to redistribute and
# which CI has no business downloading.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(dirname "$here")"
work="$(mktemp -d)"
holder=""
fake=""

cleanup() {
    [ -n "$holder" ] && kill "$holder" 2>/dev/null
    [ -n "$fake" ] && kill "$fake" 2>/dev/null
    rm -rf "$work"
}
trap cleanup EXIT

python3 - "$work" <<'EOF'
import os
import sys

from kimtape import Tape

work = sys.argv[1]
for name, base, size in (
    ("Venture-ZeroPage.ptp", 0x0000, 0xEF),
    ("Venture-Full.ptp", 0x0200, 600),
):
    mem = {base + i: (i * 7 + 3) & 0xFF for i in range(size)}
    with open(os.path.join(work, name), "wb") as out:
        out.write(Tape.from_memory(name, mem).text())
EOF

# the fake board serves until its stdin closes, so hold that open explicitly
mkfifo "$work/hold"
sleep 300 > "$work/hold" &
holder=$!
python3 "$here/fake_kim.py" 0 > "$work/dev" < "$work/hold" &
fake=$!

for _ in $(seq 100); do
    [ -s "$work/dev" ] && break
    sleep 0.1
done
dev="$(head -1 "$work/dev")"
if [ -z "$dev" ] || [ ! -e "$dev" ]; then
    echo "fake board did not start" >&2
    exit 1
fi

cd "$work"
"$root/examples/kimventure.sh" --port "$dev" --baud 115200
echo "example ok"
