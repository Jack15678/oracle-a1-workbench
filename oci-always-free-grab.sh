#!/usr/bin/env bash
# WSL entry point shared with the local workbench.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec python3 oracle_grab.py run "$@"
