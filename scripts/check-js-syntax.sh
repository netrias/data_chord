#!/usr/bin/env bash
set -euo pipefail

# Batch execution makes find return failure if any syntax check fails.
find src -path '*/static/*.js' -exec sh -c '
  for file do
    node --check "$file" || exit 1
  done
' sh {} +
