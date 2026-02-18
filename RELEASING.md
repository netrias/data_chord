# Releasing

We use **git tags + GitHub Releases**. See [ADR 008](adr/ADR_008_release_strategy.md)
for the full decision rationale.

## Versioning

Semantic versioning: `vMAJOR.MINOR.PATCH`

- **MAJOR** — breaking changes (workflow, data format, environment setup)
- **MINOR** — new features, significant UI changes
- **PATCH** — bug fixes, small improvements

## Cutting a release

```bash
git tag -a v1.0.0 -m "Brief description"
git push origin v1.0.0
```
