#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/env.sh"

echo "[frontalk] Python"
"${FRONTALK_PYTHON}" --version
"${FRONTALK_PYTHON}" -m pip check

echo "[frontalk] Frozen data and official source integrity"
"${FRONTALK_PYTHON}" "${MMCODE_ROOT}/evaluate/frontalk/run_official.py" preflight
"${FRONTALK_PYTHON}" "${MMCODE_ROOT}/evaluate/verify_official_sources.py" frontalk

echo "[frontalk] Browser and driver binaries"
"${CHROME_BINARY}" --version
"${CHROME_DRIVER}" --version

echo "[frontalk] Emoji font"
fc-match "Noto Color Emoji" | head -n 1

echo "[frontalk] Released Selenium browser smoke test"
if [[ "$(id -u)" == "0" ]]; then
  # The released Chrome options intentionally do not contain --no-sandbox.
  # Chrome therefore must run as a non-root user, as it does in a normal job.
  smoke_home="$(mktemp -d /tmp/frontalk-browser-check.XXXXXX)"
  chmod 0777 "${smoke_home}"
  mkdir -p "${smoke_home}/cache" "${smoke_home}/matplotlib"
  chmod 0777 "${smoke_home}/cache" "${smoke_home}/matplotlib"
  setpriv --reuid=65534 --regid=65534 --clear-groups \
    env \
      HOME="${smoke_home}" \
      XDG_CACHE_HOME="${smoke_home}/cache" \
      MPLCONFIGDIR="${smoke_home}/matplotlib" \
      CHROME_BINARY="${CHROME_BINARY}" \
      CHROME_DRIVER="${CHROME_DRIVER}" \
      NO_PROXY="${NO_PROXY}" \
      no_proxy="${no_proxy}" \
      "${FRONTALK_PYTHON}" "${script_dir}/check_browser.py"
else
  "${FRONTALK_PYTHON}" "${script_dir}/check_browser.py"
fi

echo "[frontalk] environment OK"
