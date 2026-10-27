#!/usr/bin/env bash
set -euo pipefail

if [[ "${FORCE_JAVASCRIPT_ACTIONS_TO_NODE24:-}" != "true" ]]; then
  echo 'Pinned JavaScript actions must select the checked Node 24 runtime.' >&2
  exit 1
fi
case "${ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION:-}" in
  ''|false|0|'$false') ;;
  *) echo 'An insecure JavaScript action-runtime opt-out is not permitted.' >&2; exit 1 ;;
esac

# The runner owns this runtime. setup-node changes project Node, not this binary.
if ! runner_executable="$(readlink -f "/proc/$PPID/exe")" || [[ "${runner_executable##*/}" != "Runner.Worker" ]]; then
  echo 'Cannot identify the GitHub runner worker; refusing runtime attestation.' >&2
  exit 1
fi
runtime="$(dirname "$runner_executable")/../externals/node24/bin/node"
test -x "$runtime"
version="$("$runtime" --version)"
if ! [[ "$version" =~ ^v24\.[0-9]+\.[0-9]+$ ]]; then
  echo "Unexpected runner-owned Node version: $version" >&2
  exit 1
fi
IFS=. read -r major minor patch <<<"${version#v}"
if ((minor < 22 || (minor == 22 && patch < 1))); then
  echo "Runner-owned Node is below the repair floor: $version" >&2
  exit 1
fi
printf 'Runner worker: %s\nRunner-owned Node: %s\n' "$runner_executable" "$version"
sha256sum "$runtime"
"$runtime" -p 'JSON.stringify({node:process.version,openssl:process.versions.openssl,platform:process.platform,arch:process.arch})'
