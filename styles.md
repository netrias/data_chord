## Stage 4 UI style guide

- **Stage shell**: `min(1900px, 98vw)` max width with tight horizontal padding keeps up to five review columns visible without horizontal scroll. Row index column is 100px; each data column uses `minmax(280px, 1fr)`.
- **Row layout**: scrollable table with pinned headers. Every column cell stacks three fixed zones (Recommended / Original input / Manual override). Preserve that order so values align across rows.
- **Confidence colors**: only two active buckets—high (score ≥80%) uses green `#bbf7d0`/`#effdf5`, and everything below that is treated as low (red `#fecaca`/`#fff5f5`). Rows with no changes stay gray. Don’t reintroduce a third medium tone.
- **Manual overrides**: dashed full-width input at the bottom of each card, pencil icon on the right, no placeholder text. Include the ARIA label `Manual override for {column}` even if the visible label is hidden.
- **Help menu**: toggle button next to batch progress opens a short tips list covering color meanings, override instructions, and batch workflow reminders.
- **Spacing**: maintain ~0.75rem gaps between stacked sections inside cards and 1.5rem card padding for readability while keeping rows dense.
- **Navigation**: Every stage places nav buttons in the same `.stage-nav` bar—Stage 1 exposes only a forward CTA, while Stages 2‑5 include a `Back` ghost button and a forward/action button describing the next step. Buttons use `.nav-btn` (with built‑in arrow pseudo elements) plus `.nav-forward`/`.nav-back` modifiers.
