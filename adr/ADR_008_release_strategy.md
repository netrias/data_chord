# ADR 008: Release Strategy

## Status

Accepted

## Context

The project is stable enough to distribute to users. Recipients are technical
(comfortable with `git clone`, `uv sync`, running locally), but they need a
predictable way to get known-good versions without tracking active development.

We considered four approaches:

1. **Main-is-stable** — users clone `main` directly
2. **Git tags + GitHub Releases** — tag specific commits, users check out tags
3. **Long-lived release branch** — a `release` branch updated via cherry-pick
4. **Tags + release branch hybrid** — a `stable` branch fast-forwarded to tags

## Decision

**Git tags + GitHub Releases** (option 2).

### Versioning

Use semantic versioning: `vMAJOR.MINOR.PATCH`.

- **MAJOR**: Breaking changes to workflow, data format, or environment setup
- **MINOR**: New features, new stages, significant UI changes
- **PATCH**: Bug fixes, small improvements

The first release is `v1.0.0`.

### Release process

1. Ensure `main` is in a known-good state (tests pass, app runs correctly)
2. Create an annotated tag:
   ```bash
   git tag -a v1.0.0 -m "Brief description of this release"
   git push origin v1.0.0
   ```
3. Create a GitHub Release from the tag, including:
   - What changed since the last release (features, fixes)
   - Any setup or migration steps (new env vars, dependency changes)
   - Known limitations

### What users do

- **First time**: Clone the repo, check out the tag, follow setup instructions
  ```bash
  git clone <repo-url>
  cd stuttgart
  git checkout v1.0.0
  uv sync
  ```
- **Updating**: Fetch tags and check out the new version
  ```bash
  git fetch --tags
  git checkout v1.1.0
  uv sync
  ```

### When to release

No fixed cadence. Cut a release when:
- A meaningful feature or fix lands on `main`
- A user-facing bug is resolved
- Setup or environment requirements change

### What goes in the GitHub Release notes

Each release should note:
- **Added**: New capabilities
- **Changed**: Modified behavior
- **Fixed**: Bug fixes
- **Setup**: Any new environment variables, dependencies, or migration steps

## Alternatives Considered

- **Main-is-stable**: Zero overhead, but any broken merge is immediately visible
  to users. No buffer between development and distribution.
- **Long-lived release branch**: Cherry-picking creates merge drift and
  maintenance burden. Overkill for a single release track with a small user
  base.
- **Tags + release branch hybrid**: Additive complexity for no immediate
  benefit. Can be adopted later if users want a branch to `git pull` against,
  by adding a `stable` branch that fast-forwards to tagged releases.
