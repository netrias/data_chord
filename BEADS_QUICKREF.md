# Beads Quick Reference

## Common Commands

| Command | Description |
|---------|-------------|
| `bd ready` | Show tasks with no open blockers |
| `bd list` | List all open tasks |
| `bd create "Title" -p 1` | Create priority 1 task |
| `bd show <id>` | View task details and history |
| `bd update <id> --status in_progress` | Mark task as in progress |
| `bd close <id>` | Close completed task |
| `bd dep add <child> <parent>` | Add dependency (child blocks on parent) |
| `bd dep tree <id>` | Show dependency tree |
| `bd stats` | Show overall statistics |
| `bd blocked` | Show tasks blocked by dependencies |

## Priority Levels

- **P0**: Critical, blocking work
- **P1**: High priority
- **P2**: Medium priority
- **P3**: Low priority

## Task Types

- `task`: Standard work item
- `feature`: New functionality
- `bug`: Defect to fix
- `epic`: Parent task with subtasks

## Stealth Mode Notes

- All Beads data is local-only (.beads/ directory)
- Never committed to git
- Safe for use on shared projects
- Do NOT use git integration commands (bd sync, bd hooks install)

## Example Workflow

```bash
# Start session
bd ready                    # See what's available

# Start work
bd update data_chord-a1b2 --status in_progress

# Create follow-up tasks
bd create "Add error handling" -p 2

# Complete work
bd close data_chord-a1b2 --reason "Implemented with tests passing"

# Check progress
bd stats
```

## Creating Tasks with Descriptions

```bash
# Basic task
bd create "Implement user authentication" -p 1 -t feature

# Task with description
bd create "Fix login bug" -p 0 -t bug --description="Users can't log in with special characters in password"

# Epic with subtasks
bd create "API Redesign" -t epic -p 2
bd create "Design API schema" -p 2 -t task
bd dep add data_chord-xyz data_chord-abc  # xyz depends on abc
```

## Viewing Dependencies

```bash
# Show what blocks a task
bd show data_chord-4vx

# View dependency tree
bd dep tree data_chord-4vx

# List all blocked tasks
bd blocked
```

## Quick Status Check

```bash
# See actionable work
bd ready

# Overall project status
bd stats

# All open issues
bd list
```

## Tips for AI Agents

1. Always run `bd ready` at session start to see actionable work
2. Create tasks for any discovered work during implementation
3. Use `bd close` with descriptive reasons when completing tasks
4. Add dependencies when discovering task relationships
5. Never run `bd sync` or `bd hooks install` in stealth mode
