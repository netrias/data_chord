# BDF-725 production release

This branch keeps production's existing application, SDK, state, network,
authentication, and storage. Its supported release modes are `plan`, `apply`,
and `build`. The old automatic apply modes stop with an error.

Use account `084828580051`, Region `us-east-2`, profile `strides`, and the
existing `env/prod.backend.hcl` state. Commit and push the reviewed source first.
No bootstrap, data import, secret creation, or user invitation is required.
The security plan excludes the existing unconfirmed email-subscription drift;
notification changes remain outside this repair.

1. Run `AWS_PROFILE=strides infra/scripts/deploy.sh prod plan` while keeping
   the existing image. Review the saved plan. The first change should only
   update CodeBuild's source/bootstrap, environment, and cache configuration.
2. Set `DATA_CHORD_SAVED_PLAN` to the printed absolute path and run
   `AWS_PROFILE=strides infra/scripts/deploy.sh prod apply`. This applies
   those exact saved bytes. It does not rebuild or silently replan.
3. Run `AWS_PROFILE=strides infra/scripts/deploy.sh prod build`. CodeBuild
   prepares and audits a separate CA store, restarts Docker, and fetches the
   full source commit. Production and its SDK are public repositories, so this
   path needs no GitHub application token. Build tags use the full commit.
4. Qualify both release and recovery artifacts. Keep the image digests, SBOM,
   scanner results, effective CA audits, TLS checks, and synthetic workflow and
   export results with the release evidence. The operator qualification JSON
   contains `qualified_images`, a map from each full source commit tag to its
   verified ECR `sha256:` digest. Creating that record requires the evidence;
   it does not qualify an artifact by itself.
5. Set `DATA_CHORD_RELEASE_QUALIFICATION` to that private record and
   `DATA_CHORD_IMAGE_TAG` to the selected full commit. For the first patched
   baseline, set `TF_VAR_enable_automatic_rollback=false` so ECS cannot restore
   July's unqualified image. Plan, review, and apply the saved plan as above.
   Keep the separately rehearsed patched recovery artifact ready.
6. After the patched baseline passes live checks, plan the final candidate with
   automatic rollback enabled. Both the new image and the running recovery
   image must be qualified. Rehearse recovery and forward release, verify
   exports, and observe production errors and restarts for at least 15 minutes.

The checker rejects other resource changes, task setting changes, and image
repository changes. It rejects unqualified forward or automatic recovery images.
The deployment requires one stable completed ECS deployment and rechecks its
actual task and image at plan and apply time. A partial rollout or mismatch with
OpenTofu state stops for reconciliation. The service must select the matching
planned task; a service-only task switch is rejected.

The deployment checks each accepted tag against its actual immutable ECR digest
at plan and apply time. OpenTofu also rejects a stale saved plan after state
changes. A changed source commit or plan checksum requires a fresh reviewed plan.
Keep earlier plan directories for diagnosis; a new plan gets its own directory.

The qualification record is an operator evidence artifact. It is not stored in
Git, and it grants no AWS access. Do not add secrets or customer data to it.
