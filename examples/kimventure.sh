#!/usr/bin/env bash
# KIM-Venture, the worked example.  A thin wrapper over the catalogue loader;
# see docs/kimventure.md.  Options are load.py's: --port, --baud, --no-run.
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/load.py" kimventure "$@"
