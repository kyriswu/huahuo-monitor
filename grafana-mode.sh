#!/bin/sh
# Rebuild dashboard defaults and recreate only Grafana. Volumes are preserved.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
case "${1:-}" in
    light) touch grafana/.light-mode ;;
    standard) rm -f grafana/.light-mode ;;
    *) echo "Usage: $0 light|standard" >&2; exit 2 ;;
esac
python3 grafana/build_dashboards.py
./monitoring.sh config --quiet
./monitoring.sh up -d --no-deps --wait --wait-timeout 120 grafana
echo "Grafana mode: $1 (alerting enabled; persistent data preserved)."
