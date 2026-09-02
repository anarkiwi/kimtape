#!/usr/bin/env bash
# Exercise examples/load.py end to end against the fake monitor, using a
# catalogue of synthetic tapes served over file:// URLs.  Real programs are
# not ours to redistribute and CI has no business downloading them.
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
tapes = []
for name, base, size in (("zeropage.ptp", 0x0000, 0xEF), ("game.ptp", 0x0200, 600)):
    mem = {base + i: (i * 7 + 3) & 0xFF for i in range(size)}
    path = os.path.join(work, name)
    with open(path, "wb") as out:
        out.write(Tape.from_memory(name, mem).text())
    tapes.append('{ url = "file://%s" }' % path)

# a raw binary too, so the conversion path is exercised
binary = os.path.join(work, "extra.bin")
with open(binary, "wb") as out:
    out.write(bytes(range(64)))
tapes.append('{ url = "file://%s", at = "0500" }' % binary)

with open(os.path.join(work, "programs.toml"), "w", encoding="ascii") as out:
    out.write(
        "[demo]\n"
        'title = "Demo"\nauthor = "Tests"\ndescription = "Synthetic"\n'
        'io = "keypad"\nstart = "0200"\nsource = "https://example.invalid"\n'
        'deposits = [{ addr = "00F1", bytes = "DAFDFF" }]\n'
        "tapes = [\n  %s,\n]\n" % ",\n  ".join(tapes)
    )
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

"$root/examples/load.py" --list
"$root/examples/load.py" demo \
    --catalogue "$work/programs.toml" --dir "$work/cache" \
    --port "$dev" --baud 115200
echo "example ok"
