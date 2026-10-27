#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(tr -d '\r\n' < "$repository_root/.nvmrc")"
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "The Node runtime pin must be an exact release version." >&2
  exit 1
fi

case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) platform=linux-x64 ;;
  Linux/aarch64) platform=linux-arm64 ;;
  Darwin/arm64) platform=darwin-arm64 ;;
  Darwin/x86_64) platform=darwin-x64 ;;
  *) echo "Unsupported Node runtime platform." >&2; exit 1 ;;
esac

archive="node-v${version}-${platform}.tar.gz"
expected="$(awk -v archive="$archive" '$2 == archive {print $1}' "$repository_root/config/node-runtime-sha256.txt")"
if ! [[ "$expected" =~ ^[0-9a-f]{64}$ ]]; then
  echo "No reviewed checksum for $archive; update the runtime pin and checksums together." >&2
  exit 1
fi

runtime_directory="$(mktemp -d "${TMPDIR:-/tmp}/data-chord-node.XXXXXX")"
curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
  --output "$runtime_directory/$archive" "https://nodejs.org/dist/v$version/$archive"
if command -v sha256sum >/dev/null 2>&1; then
  actual="$(sha256sum "$runtime_directory/$archive" | awk '{print $1}')"
else
  actual="$(shasum -a 256 "$runtime_directory/$archive" | awk '{print $1}')"
fi
if [[ "$actual" != "$expected" ]]; then
  echo "Node archive checksum mismatch; refusing to extract it." >&2
  exit 1
fi

tar -xzf "$runtime_directory/$archive" -C "$runtime_directory" --strip-components=1
test -x "$runtime_directory/bin/node"
printf '%s\n' "$runtime_directory"
