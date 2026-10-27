# CodeBuild trust proof

This root runs the canonical certificate checker on a real CodeBuild worker.
It is separate from the staging and production application roots. State is in
the existing BDF foundation bucket, under the key declared in `main.tf`.

The project has no source checkout, application secrets, image-push permission,
or privileged Docker access. Its role can write only to its own log group.
The inline buildspec embeds the repository checker and public CA fixtures.

Plan and review before applying:

```sh
AWS_PROFILE=strides tofu -chdir=infra/verification/build-trust init
AWS_PROFILE=strides tofu -chdir=infra/verification/build-trust plan -out=proof.tfplan
AWS_PROFILE=strides tofu -chdir=infra/verification/build-trust apply proof.tfplan
```

Run one case at a time by replacing `worker` below:

```sh
aws codebuild start-build \
  --project-name data-chord-build-trust-proof \
  --environment-variables-override name=TRUST_PROOF_CASE,value=worker,type=PLAINTEXT \
  --profile strides --region us-east-2
```

| Case | Required result |
| --- | --- |
| `worker` | Inspect the real system bundle and CA directory. A failure blocks use of that worker for release. |
| `safe` | A generated test root passes; all four downstream markers appear. This does not qualify the system trust store. |
| `blocked-bundle` | The public emSign fixture fails in INSTALL; no later marker appears. |
| `blocked-directory` | The public AAA fixture fails in the CA directory; no later marker appears. |
| `missing` | A missing trust bundle fails in INSTALL; no later marker appears. |
| `malformed` | An invalid certificate fails in INSTALL; no later marker appears. |

Inspect both `batch-get-builds` phases and the complete CloudWatch log. Match
standalone marker output, not the buildspec command text printed in a log.
The markers represent custom installers, application-token retrieval, image
build, and image push. Those operations do not actually run in this project.

Keep worker-image identity with the result. The application projects still
have their own earlier managed `DOWNLOAD_SOURCE` boundary. This proof alone
does not qualify that boundary or a mutable vendor image tag.
