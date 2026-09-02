#!/usr/bin/env bash
# Worked example: fetch the full build of KIM-Venture and load it into a KIM-1
# or PAL-II over the serial port.  See docs/kimventure.md.
set -euo pipefail

PORT="${KIM_PORT:-/dev/ttyUSB0}"
BAUD="${KIM_BAUD:-1200}"
START=0200
RUN=1
FORCE=0

TAPES=(Venture-ZeroPage.ptp Venture-Full.ptp)
GITHUB=https://raw.githubusercontent.com/markbush/KIM-Venture/main/tape-files
ARCHIVE=http://retro.hansotten.nl/uploads/kimventure/kimventurepapermb.zip

if command -v kimtape >/dev/null; then
    KIMTAPE=(kimtape)
else
    KIMTAPE=(python3 -m kimtape)
fi

usage() {
    cat <<'EOF'
usage: kimventure.sh [options] [-- kimtape options]

  -p, --port DEVICE       serial port (default $KIM_PORT or /dev/ttyUSB0)
  -b, --baud RATE         line rate (default 1200, the PAL-II factory setting)
  -n, --no-run            load and verify but do not start the game
  -f, --fetch             re-download the tapes even if they are present
  -h, --help              this text

Tapes are downloaded into the current directory if they are not already here.
Anything after -- goes to kimtape (e.g. --no-verify, --char-delay).
EOF
}

die() { echo "kimventure: $*" >&2; exit 1; }

fetch_from_github() {
    local tape
    for tape in "$@"; do
        curl -fsSL --retry 3 -o "$tape.part" "$GITHUB/$tape" || {
            rm -f "$tape.part"
            return 1
        }
        mv "$tape.part" "$tape"
    done
}

fetch_from_archive() {
    local zip
    zip="$(mktemp -t kimventure.XXXXXX.zip)"
    trap 'rm -f "$zip"' RETURN
    curl -fsSL --retry 3 -o "$zip" "$ARCHIVE" || return 1
    python3 - "$zip" "$@" <<'EOF'
import sys, zipfile
archive = zipfile.ZipFile(sys.argv[1])
for name in sys.argv[2:]:
    with open(name, "wb") as out:
        out.write(archive.read(name))
EOF
}

fetch() {
    local tape missing=()
    for tape in "${TAPES[@]}"; do
        [ "$FORCE" = 1 ] && rm -f "$tape"
        [ -f "$tape" ] || missing+=("$tape")
    done
    if [ ${#missing[@]} -eq 0 ]; then
        echo "tapes already present: ${TAPES[*]}"
        return
    fi
    echo "downloading ${missing[*]}"
    fetch_from_github "${missing[@]}" ||
        fetch_from_archive "${missing[@]}" ||
        die "could not download ${missing[*]} from $GITHUB or $ARCHIVE"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -p|--port) PORT="$2"; shift 2 ;;
        -b|--baud) BAUD="$2"; shift 2 ;;
        -n|--no-run) RUN=0; shift ;;
        -f|--fetch) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; break ;;
        *) usage >&2; die "unknown option $1" ;;
    esac
done

command -v curl >/dev/null || die "curl is required"
command -v python3 >/dev/null || die "python3 is required"

fetch
"${KIMTAPE[@]}" info "${TAPES[@]}"

args=(load --port "$PORT" --baud "$BAUD" "${TAPES[@]}")
[ "$RUN" = 1 ] && args+=(--go "$START")
"${KIMTAPE[@]}" "${args[@]}" "$@"

if [ "$RUN" = 1 ]; then
    cat <<'EOF'

KIM-Venture drives the LED display and keypad itself, not the terminal.
Remove the TTY jumper (application connector V to 21) now, or its shorted
matrix position reads as a stuck key.  Scoring: press ST, then AD 0600 GO;
ST again and AD 0200 GO rejoins the game.
EOF
fi
