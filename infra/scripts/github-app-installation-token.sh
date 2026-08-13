#!/usr/bin/env bash
set -euo pipefail
umask 077

secret_arn="${1:?GitHub App secret ARN is required}"
token_file="${2:?Token output path is required}"
secret_json="$(aws secretsmanager get-secret-value --secret-id "$secret_arn" --query SecretString --output text)"
app_id="$(jq -r '.app_id' <<<"$secret_json")"
installation_id="$(jq -r '.installation_id' <<<"$secret_json")"
if [[ "$app_id" == "null" || "$installation_id" == "null" ]]; then
  echo "GitHub App secret must contain app_id and installation_id" >&2
  exit 1
fi

base64_url() {
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

now="$(date +%s)"
header="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | base64_url)"
payload="$(printf '{"iat":%s,"exp":%s,"iss":"%s"}' "$((now - 60))" "$((now + 540))" "$app_id" | base64_url)"
signature="$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign <(jq -r '.private_key' <<<"$secret_json") | base64_url)"
jwt="$header.$payload.$signature"

token_response="$(curl --fail --silent --show-error \
  --request POST \
  --header "Accept: application/vnd.github+json" \
  --header "Authorization: Bearer $jwt" \
  --header "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/app/installations/$installation_id/access_tokens")"
token="$(jq -r '.token' <<<"$token_response")"
if [[ "$token" == "null" || -z "$token" ]]; then
  echo "GitHub did not return an installation token" >&2
  exit 1
fi
printf '%s' "$token" >"$token_file"
