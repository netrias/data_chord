terraform {
  required_version = ">= 1.10"
  backend "s3" {
    bucket       = "bdf-datachord-state-084828580051-us-east-2"
    key          = "datachord/bdf/worker-trust-proof/staging/tofu.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.45.0"
    }
  }
}

provider "aws" {
  region              = "us-east-2"
  allowed_account_ids = ["084828580051"]
}

resource "aws_cloudwatch_log_group" "proof" {
  name              = "/aws/codebuild/data-chord-build-trust-proof"
  retention_in_days = 14
}

resource "aws_iam_role" "proof" {
  name                 = "data-chord-build-trust-proof"
  path                 = "/application/"
  permissions_boundary = "arn:aws:iam::084828580051:policy/bdf-datachord-application-role-boundary"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "codebuild.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "proof_logs" {
  name = "write-proof-logs"
  role = aws_iam_role.proof.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.proof.arn}:*"
    }]
  })
}

resource "aws_codebuild_project" "proof" {
  name          = "data-chord-build-trust-proof"
  description   = "BDF-725 worker audit and isolated phase-failure proof; no application credentials"
  service_role  = aws_iam_role.proof.arn
  build_timeout = 10
  depends_on    = [aws_iam_role_policy.proof_logs]

  artifacts {
    type = "NO_ARTIFACTS"
  }
  environment {
    compute_type = "BUILD_GENERAL1_SMALL"
    image        = "aws/codebuild/standard:7.0"
    type         = "LINUX_CONTAINER"
    environment_variable {
      name  = "TRUST_PROOF_CASE"
      value = "worker"
    }
  }
  source {
    type = "NO_SOURCE"
    buildspec = yamlencode({
      version = "0.2"
      env     = { shell = "bash" }
      phases = {
        install = {
          "on-failure" = "ABORT"
          commands = [
            <<-SETUP
              python3 -I - <<'PY'
              import base64
              from pathlib import Path
              root = Path('/tmp/data-chord-trust-proof')
              root.mkdir()
              (root / 'check.py').write_bytes(base64.b64decode('${filebase64("${path.module}/../../../scripts/check-build-host-trust.py")}'))
              (root / 'prepare.py').write_bytes(base64.b64decode('${filebase64("${path.module}/../../../scripts/prepare-build-trust.py")}'))
              (root / 'pin.json').write_bytes(base64.b64decode('${filebase64("${path.module}/../../../config/build-trust.json")}'))
              (root / 'emsign.pem').write_bytes(base64.b64decode('${filebase64("${path.module}/../../../tests/fixtures/build-trust/emsign-g3.pem")}'))
              (root / 'aaa.pem').write_bytes(base64.b64decode('${filebase64("${path.module}/../../../tests/fixtures/build-trust/aaa-services.pem")}'))
              (root / 'certs').mkdir()
              PY
            SETUP
            ,
            <<-GATE
              set -euo pipefail
              root=/tmp/data-chord-trust-proof
              case "$TRUST_PROOF_CASE" in
                worker) python3 -I "$root/check.py" ;;
                repaired-worker)
                  python3 -I "$root/prepare.py" --pin "$root/pin.json" --checker "$root/check.py" --store "$root/approved-certs"
                  export SSL_CERT_DIR="$root/approved-certs" SSL_CERT_FILE="$root/approved-certs/ca-certificates.crt"
                  unset CURL_CA_BUNDLE
                  export AWS_CA_BUNDLE="$SSL_CERT_FILE" GIT_SSL_CAINFO="$SSL_CERT_FILE" GIT_SSL_CAPATH="$SSL_CERT_DIR"
                  python3 -I "$root/check.py" --bundle "$SSL_CERT_FILE" --directory "$SSL_CERT_DIR"
                  python3 -I -c 'import urllib.request; print("PUBLIC_TLS_STATUS", urllib.request.urlopen("https://api.github.com", timeout=30).status)'
                  curl --verbose --fail --silent --show-error https://api.github.com -o /dev/null
                  git ls-remote https://github.com/netrias/data_chord.git HEAD
                  aws sts get-caller-identity --query Account --output text
                  ;;
                safe)
                  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 -subj /CN=build-proof -keyout "$root/key.pem" -out "$root/bundle.pem" 2>/dev/null
                  python3 -I "$root/check.py" --bundle "$root/bundle.pem" --directory "$root/certs"
                  ;;
                blocked-bundle)
                  python3 -I "$root/check.py" --bundle "$root/emsign.pem" --directory "$root/certs"
                  ;;
                blocked-directory)
                  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 1 -subj /CN=build-proof -keyout "$root/key.pem" -out "$root/bundle.pem" 2>/dev/null
                  cp "$root/aaa.pem" "$root/certs/12345678.0"
                  python3 -I "$root/check.py" --bundle "$root/bundle.pem" --directory "$root/certs"
                  ;;
                missing)
                  python3 -I "$root/check.py" --bundle "$root/missing.pem" --directory "$root/certs"
                  ;;
                malformed)
                  printf '%s\n' '-----BEGIN CERTIFICATE-----' invalid '-----END CERTIFICATE-----' > "$root/bundle.pem"
                  python3 -I "$root/check.py" --bundle "$root/bundle.pem" --directory "$root/certs"
                  ;;
                *) echo 'Unknown proof case' >&2; exit 2 ;;
              esac
            GATE
            ,
            "echo INSTALLER_BOUNDARY_REACHED"
          ]
        }
        pre_build = {
          commands = ["echo APP_TOKEN_BOUNDARY_REACHED"]
        }
        build = {
          commands = ["echo IMAGE_BUILD_BOUNDARY_REACHED"]
        }
        post_build = {
          commands = ["test \"$CODEBUILD_BUILD_SUCCEEDING\" = 1", "echo IMAGE_PUSH_BOUNDARY_REACHED"]
        }
      }
    })
  }
  logs_config {
    cloudwatch_logs {
      status     = "ENABLED"
      group_name = aws_cloudwatch_log_group.proof.name
    }
  }
}

output "project_name" {
  value = aws_codebuild_project.proof.name
}
