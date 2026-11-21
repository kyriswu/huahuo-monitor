#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f grafana/.light-mode ]; then
    exec docker compose -f compose.yaml -f compose.logs.yaml -f compose.grafana-light.yaml "$@"
fi
exec docker compose -f compose.yaml -f compose.logs.yaml "$@"
