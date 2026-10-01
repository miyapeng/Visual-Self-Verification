#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 SCRIPT [ARG...]" >&2
  exit 2
fi

# proxy_on is defined by the platform image's interactive Bash setup. Execute
# the target in that shell so exported proxy variables are inherited, without
# printing credentials into the job specification or logs.
exec bash -ic 'proxy_on; exec bash "$@"' proxy-shell "$@"
