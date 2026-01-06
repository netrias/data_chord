/**
 * Centralized step instruction content for progress tracker.
 * Each step has a short description (shown when active) and a
 * long description (shown on hover).
 */

/** Canonical stage order for progress tracker navigation. */
export const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'verify', 'review'];

export const STEP_INSTRUCTIONS = {
  upload: {
    short: "Upload your CSV dataset to begin the harmonization workflow.",
    long: "Select a CSV file from your computer to upload. The system will analyze your file's columns and prepare them for mapping to standard ontologies."
  },
  mapping: {
    short: "Review and confirm which ontologies your data columns should map to.",
    long: "The AI has suggested mappings for each column in your dataset. Review these recommendations and adjust if needed. Confirm all mappings before proceeding to harmonization."
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
    long: "Your harmonization is complete. Review the summary statistics showing AI changes vs manual overrides per column. Download the final harmonized dataset in CSV format. You can also start a new harmonization workflow from here."
  }
};
