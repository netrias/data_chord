#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
commit_id="$(git -C "$repository_root" rev-parse --short=12 HEAD)"
container="data-chord-demo-$$"
image="${container}:${commit_id}"
port="${DATA_CHORD_DEMO_PORT:-8000}"
url="http://127.0.0.1:${port}/stage-1"

if ! command -v docker >/dev/null 2>&1; then
  echo "The Docker demo needs Docker. Install and start Docker, then run 'just demo-docker' again." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "The Docker demo cannot reach the Docker service. Start Docker, then run 'just demo-docker' again." >&2
  exit 1
fi

_stop_demo() {
  docker stop "$container" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
}

_stop_demo_and_exit() {
  _stop_demo
  exit 0
}

trap _stop_demo EXIT
trap _stop_demo_and_exit INT TERM

demo_github_token="${GITHUB_TOKEN:-}"
if [[ -z "$demo_github_token" ]] && command -v gh >/dev/null 2>&1; then
  demo_github_token="$(gh auth token 2>/dev/null || true)"
fi
if [[ -z "$demo_github_token" ]]; then
  echo "The Docker demo build needs GitHub access to the private harmonization library."
  echo "Sign in with 'gh auth login', then run 'just demo-docker' again."
  exit 1
fi
export DATA_CHORD_DEMO_GITHUB_TOKEN="$demo_github_token"
echo "Building the current working tree for the Docker demo."
docker build \
  --secret id=github_token,env=DATA_CHORD_DEMO_GITHUB_TOKEN \
  --tag "$image" \
  "$repository_root"
unset DATA_CHORD_DEMO_GITHUB_TOKEN

docker run \
  --rm \
  --name "$container" \
  --publish "127.0.0.1:${port}:8000" \
  "$image" \
  python -m scripts.demo &
container_pid=$!

for _attempt in {1..60}; do
  if curl --fail --silent "$url" >/dev/null; then
    echo "Data Chord demo is ready at $url"
    if command -v open >/dev/null 2>&1; then
      open "$url"
    elif command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$url" >/dev/null 2>&1 || true
    fi
    wait "$container_pid"
    exit $?
  fi
  if ! kill -0 "$container_pid" 2>/dev/null; then
    wait "$container_pid"
  fi
  sleep 0.5
done

echo "The demo did not become ready at $url."
exit 1
