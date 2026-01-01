# Agent Instructions for Data Chord

## Task Management

Use `bd` (Beads) for task tracking and persistent memory across sessions.

### Getting Started with Beads

- Run `bd ready` to see tasks with no open blockers
- Run `bd create "Task title" -p 1` to create priority 1 tasks
- Run `bd dep add <child> <parent>` to establish dependencies
- Run `bd show <id>` to view task details
- Run `bd list` to see all open tasks

### Session Completion Protocol

When ending a session, always:

1. File issues for any remaining work: `bd create "Remaining work description"`
2. Update task statuses: `bd update <id> --status in_progress` or `bd close <id>`
3. Run quality gates if code changed (ruff, basedpyright, pytest)
4. DO NOT run `bd sync` or `bd hooks install` (stealth mode - no git integration)

### Project-Specific Guidelines

**Architecture:**

- Stages must only depend on domain, never on each other
- See CLAUDE.md for full code conventions
- Pre-commit hooks enforce: ruff-format, ruff, no-cross-stage-imports

**Development Workflow:**

- Use `uv` for Python package management, never pip
- Run tests: `uv run pytest`
- Lint: `uv run ruff check .`
- Type check: `uv run basedpyright`

**Important:**

- This project uses Beads in STEALTH MODE - .beads/ is local-only, never committed
- Do not install git hooks for Beads (conflicts with pre-commit)
- Do not run `bd sync` or any git integration commands
