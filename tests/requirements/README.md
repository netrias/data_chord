# Requirements Tests

The requirements tests live under `tests/requirements` and are linked to numbered requirements in
`requirements.md` with `@pytest.mark.requirements(...)`.

Run the traceability check:

```bash
just requirements-trace
```

Run only the requirements tests with coverage:

```bash
just requirements-coverage
```

The coverage command measures Python application code under `src` and `backend`. It enables branch
coverage and records pytest test contexts so the report can show both missed paths and, where the
report format supports it, which requirement test exercised a line.

Generated reports are written under `.artifacts/requirements-coverage/`:

```text
.artifacts/requirements-coverage/html/index.html
.artifacts/requirements-coverage/coverage.json
.artifacts/requirements-coverage/coverage.xml
```
