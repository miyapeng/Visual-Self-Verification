#!/usr/bin/env bash
# Source this file before running the frozen InteractWeb-Bench release.

_interactweb_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MMCODE_ROOT="$(cd "${_interactweb_script_dir}/../.." && pwd)"
export INTERACTWEB_PYTHON="${INTERACTWEB_PYTHON:-/data/miyapeng/miniconda3/envs/interactweb/bin/python}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${MMCODE_ROOT}/.local/runtime/interactweb/playwright}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${MMCODE_ROOT}/.local/runtime/interactweb/matplotlib}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${MMCODE_ROOT}/.local/runtime/interactweb/cache}"

export no_proxy="localhost,127.0.0.1${no_proxy:+,${no_proxy}}"
export NO_PROXY="${no_proxy}"

mkdir -p "${PLAYWRIGHT_BROWSERS_PATH}" "${MPLCONFIGDIR}" "${XDG_CACHE_HOME}"
unset _interactweb_script_dir
