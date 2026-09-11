#!/usr/bin/env bash
set -euo pipefail

for file in scripts/*.sh infra/scripts/*.sh infra/tests/*.sh; do
  bash -n "$file"
done
