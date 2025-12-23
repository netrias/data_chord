#!/usr/bin/env bash
# Check that stage modules only import from domain, not from each other.
# Stages must depend on domain, never on other stages.

set -euo pipefail

error_found=false

while IFS= read -r -d "" file; do
  source_stage=$(echo "$file" | grep -oE "stage_[0-9]" | head -1 || true)
  [ -n "$source_stage" ] || continue

  while IFS= read -r line; do
    import_stage=$(echo "$line" | grep -oE "stage_[0-9]" | tail -1 || true)
    if [ "$source_stage" != "$import_stage" ]; then
      echo "ERROR: $file imports from different stage: $line"
      error_found=true
    fi
  done < <(grep -n "from src\.stage_" "$file" 2>/dev/null || true)
done < <(find src -type f -name "*.py" -path "src/stage_*/*" -print0)

if [ "$error_found" = true ]; then
  exit 1
fi
