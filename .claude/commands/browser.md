---
description: Manually test the application flow using browser automation
---

# Browser Testing Protocol

Use the dev-browser skill to manually test the application workflow. This validates recent changes work correctly in the live application.

## Setup

Start the browser server:
```bash
cd /Users/harman/.claude/plugins/cache/dev-browser-marketplace/dev-browser/66682fb0513a/skills/dev-browser && ./server.sh &
```

Wait for "Ready" message before proceeding.

## Test Flow

### Phase 1: Identify What to Test
- Review the plan/changes made
- Identify the happy path that needs testing
- Identify edge cases or alternative paths that may have been impacted

### Phase 2: Execute Happy Path
Navigate through the full workflow testing the primary use case.

Take screenshots at each stage for verification.

### Phase 3: Test Impacted Paths
Based on recent changes test other adjacent features that may have been impacted

### Phase 4: Report Results
Present findings.

## Cleanup

Close the browser server when done:
```bash
pkill -f "dev-browser"
```

## Reference
- App URL: http://localhost:8000
- Test fixtures: tests/fixtures/
- Browser skill docs: ~/.claude/plugins/cache/dev-browser-marketplace/dev-browser/*/skills/dev-browser/
