/**
 * Centralized step instruction content for progress tracker.
 * Each step has a short description (shown when active) and a
 * long description (shown on hover).
 */

/** Canonical stage order for progress tracker navigation. */
export const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'verify', 'review'];

export const STEP_INSTRUCTIONS = {
  upload: {
    short: "Upload your CSV or TSV dataset to begin the harmonization workflow.",
    long: "Select a CSV or TSV file from your computer to upload. The system will analyze your file's columns and prepare them for mapping to standard ontologies."
  },
  mapping: {
    short: "Review and confirm which ontologies your data columns should map to.",
    long: "Each row is one column from your file, with an AI-suggested target standard and a value-fit score.\n\nColumns fall into three categories:\n  ✎ Rewrite — values will be harmonized to match the standard\n  → Pass-through — mapped, but no permissible values to enforce\n  — Unmapped — column will pass through unchanged\n\nUse the Settings sidebar to toggle category visibility, show empty columns, and control column renaming. Hover a category to preview which rows would appear or disappear.\n\nClick any row to open its detail view where you can change the target or review individual value mappings."
  },
  harmonize: {
    short: "Your data is being transformed to match the target ontology.",
    long: "The harmonization engine processes each row of your data, converting values to match the selected ontologies. This step may take a few minutes depending on dataset size. Keep this tab open while processing completes."
  },
  harmonize_complete: {
    short: "Your data has been transformed to match the target ontology.",
    long: "The harmonization engine has processed your data, converting values to match the selected ontologies. Review the results to see how your data was transformed."
  },
  verify: {
    short: "Inspect harmonized values and override any AI suggestions as needed.",
    long: "Each card shows the original value at top, an arrow, then the AI-suggested harmonization below. Cards are color-coded by confidence level. Use the input field at the bottom of each card to enter a manual override if needed."
  },
  review: {
    short: "Download your harmonized dataset and review the change summary.",
    long: "Your harmonization is complete. Review the summary statistics showing AI changes vs manual overrides per column. Download the final harmonized dataset in the same file format you uploaded. You can also start a new harmonization workflow from here."
  }
};
