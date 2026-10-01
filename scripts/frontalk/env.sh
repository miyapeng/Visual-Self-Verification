#!/usr/bin/env bash
# Source this file before running the frozen FronTalk release.

_frontalk_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MMCODE_ROOT="$(cd "${_frontalk_script_dir}/../.." && pwd)"
export FRONTALK_PYTHON="${FRONTALK_PYTHON:-/data/miyapeng/miniconda3/envs/frontalk/bin/python}"

export MPLCONFIGDIR="${MPLCONFIGDIR:-${MMCODE_ROOT}/.runtime/frontalk/matplotlib}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${MMCODE_ROOT}/.runtime/frontalk/cache}"
export SE_CACHE_PATH="${SE_CACHE_PATH:-${MMCODE_ROOT}/.runtime/frontalk/selenium}"
export SE_AVOID_STATS="${SE_AVOID_STATS:-true}"

# FronTalk uses Selenium. We reuse the locally frozen full Chromium binary,
# while the matching driver is supplied by the frontalk Python environment.
export CHROME_BINARY="${CHROME_BINARY:-${MMCODE_ROOT}/.runtime/interactweb/playwright/chromium-1234/chrome-linux64/chrome}"
if [[ -z "${CHROME_DRIVER:-}" ]]; then
  export CHROME_DRIVER
  CHROME_DRIVER="$(${FRONTALK_PYTHON} -c 'import chromedriver_binary; print(chromedriver_binary.chromedriver_filename)')"
fi

# Selenium's client must talk to its local driver without going through an
# HTTP proxy. External model/API proxy settings are otherwise left intact.
export no_proxy="localhost,127.0.0.1${no_proxy:+,${no_proxy}}"
export NO_PROXY="${no_proxy}"

mkdir -p "${MPLCONFIGDIR}" "${XDG_CACHE_HOME}" "${SE_CACHE_PATH}"
unset _frontalk_script_dir
