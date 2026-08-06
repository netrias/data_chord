/**
 * Centralized step instruction content for progress tracker.
 * Each step has a short description (shown when active) and a
 * long description (shown on hover).
 */

/** Canonical stage order for progress tracker navigation. */
export const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'verify', 'review'];

export const STEP_INSTRUCTIONS = {
  upload: {
    short: "Upload your dataset to begin the harmonization workflow.",
    long: "Select a CSV, TSV, or XLSX file from your computer to upload. The system will analyze your file's columns and prepare them for mapping to standard common data elements."
  },
  mapping: {
    short: "Review and confirm which common data elements your columns should map to.",
    long: "Each row is one column from your file, with an AI-suggested target common data element and a value-fit score.\n\nColumns fall into three categories:\n  ✎ Rewrite — values will be harmonized to match the standard\n  → Pass-through — the target standard does not have permissible values that we can conform your data to, so your data will be left untouched\n  — Unmapped — column will pass through unchanged\n\nUse the Settings sidebar to toggle category visibility, show empty columns, and choose whether to rename columns so they align with the target standard names. Column renaming is optional.\n\nClick any row to open its detail view where you can change the target or review individual value mappings."
  },
  harmonize: {
    short: "Your data is being transformed to match the selected standards.",
    long: "The harmonization engine processes each row of your data and converts values to match the selected common data elements. This step can take a few minutes. Keep this tab open while processing completes."
  },
  harmonize_complete: {
    short: "Your data has been transformed to match the selected standards.",
    long: "The harmonization engine has processed your data. The source-column table shows exact distinct-value changes, affected rows, and which final values still need review. Continue to Verify to inspect individual values."
  },
  verify: {
    short: "Inspect harmonized values and override any AI suggestions as needed.",
    long: "Each card shows the original value at top, an arrow, then the AI-suggested harmonization below. Cards are color-coded by confidence level. Use the input field at the bottom of each card to enter a manual override if needed."
  },
  review: {
    short: "Review the current output and download your harmonized dataset.",
    long: "The certificate and aggregate change totals describe the current downloadable output. Use Needs attention to find final values outside approved sets, or open Decision history to inspect how a value reached its current output."
  }
};
