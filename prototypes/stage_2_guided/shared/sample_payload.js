/**
 * Realistic seed payload for Stage 2 guided prototypes.
 *
 * Models a patient-cohort sheet (25 columns) with a believable distribution:
 *   - 16 auto-safe (high AI confidence + high value overlap, or empty)
 *   - 3 empty columns (no values at all)
 *   - 6 needing reviewer attention (low overlap, ambiguous match, or
 *     rewrite consequences the user should see)
 *
 * Shape mirrors what Stage 2's column-detail endpoint returns, simplified
 * so prototypes can render without a backend.
 */

export const SAMPLE_FILE = {
  file_name: "patient_cohort_dec2026.xlsx",
  sheet_name: "Cohort_All",
  total_rows: 4127,
  target_standard: "CCDI v2.1",
};

// Catalog the AI can suggest from. Real catalog has hundreds — these are the
// ones referenced by any column in this fixture.
export const CDE_CATALOG = {
  subject_id:                   { label: "subject_id",                   type: "passthrough", description: "Unique participant identifier." },
  age_at_index:                 { label: "age_at_index",                 type: "passthrough", description: "Age in years at the reference date." },
  sex:                          { label: "sex",                          type: "pv",          description: "Biological sex assigned at birth.", pvs: ["Female","Male","Unknown","Not Reported"] },
  gender:                       { label: "gender",                       type: "passthrough", description: "Self-identified gender (free text)." },
  race:                         { label: "race",                         type: "pv",          description: "Self-reported race.", pvs: ["American Indian or Alaska Native","Asian","Black or African American","Native Hawaiian or Other Pacific Islander","White","Other","Unknown","Not Reported"] },
  ethnicity:                    { label: "ethnicity",                    type: "pv",          description: "Hispanic or Latino origin.", pvs: ["Hispanic or Latino","Not Hispanic or Latino","Unknown","Not Reported"] },
  vital_status:                 { label: "vital_status",                 type: "pv",          description: "Survival status as of last contact.", pvs: ["Alive","Dead","Lost to Follow-up","Unknown","Not Reported"] },
  primary_diagnosis:            { label: "primary_diagnosis",            type: "pv",          description: "Initial diagnosis term.", pvs: ["Lung Cancer","Breast Cancer","Colon Cancer","Ovarian Cancer","Melanoma","Pancreatic Cancer","Leukemia","Lymphoma","Other","Not Reported"] },
  morphology:                   { label: "morphology",                   type: "pv",          description: "ICD-O-3 morphology code.", pvs: ["Adenocarcinoma","Ductal Carcinoma","Lobular Carcinoma","Serous Carcinoma","Melanoma","Squamous Cell Carcinoma","Not Reported"] },
  tissue_or_organ_of_origin:    { label: "tissue_or_organ_of_origin",    type: "pv",          description: "Anatomic site of disease origin.", pvs: ["Lung","Breast","Colon","Ovary","Skin","Pancreas","Liver","Stomach","Heart","Brain","Not Reported"] },
  year_of_birth:                { label: "year_of_birth",                type: "passthrough", description: "4-digit year of birth." },
  consent_type:                 { label: "consent_type",                 type: "pv",          description: "Type of consent obtained.", pvs: ["Broad","Specific","Tiered","Withdrawn"] },
  tissue_collection_site:       { label: "tissue_collection_site",       type: "pv",          description: "Institution where tissue was collected.", pvs: ["Mayo Clinic","Mass General","UCSF","MD Anderson","Cleveland Clinic","Johns Hopkins","Stanford","Memorial Sloan Kettering","Other"] },
  sample_type:                  { label: "sample_type",                  type: "pv",          description: "Type of biospecimen.", pvs: ["Primary Tumor","Recurrent Tumor","Normal Tissue","Blood","Saliva","Other"] },
  treatment_type:               { label: "treatment_type",               type: "pv",          description: "Class of treatment received.", pvs: ["Chemotherapy","Radiation Therapy","Immunotherapy","Surgery","Targeted Therapy","Hormone Therapy","Other"] },
  icd10_classification:         { label: "icd10_classification",         type: "passthrough", description: "ICD-10 diagnosis code (free text)." },
  primary_diagnosis_status:     { label: "primary_diagnosis_status",     type: "pv",          description: "Status of the primary diagnosis at time of report.", pvs: ["Active","Resolved","In Remission","Recurrence","Unknown"] },
  disease_status:               { label: "disease_status",               type: "pv",          description: "Overall disease status across the cohort.", pvs: ["Newly Diagnosed","Active Disease","Stable","Remission","Progressive","Refractory","Unknown"] },
  histologic_grade:             { label: "histologic_grade",             type: "pv",          description: "Tumor grade (G1–G4 system).", pvs: ["G1","G2","G3","G4","GX","Not Reported"] },
  ajcc_pathologic_stage:        { label: "ajcc_pathologic_stage",        type: "pv",          description: "AJCC pathologic stage.", pvs: ["Stage 0","Stage I","Stage IA","Stage IB","Stage II","Stage IIA","Stage IIB","Stage III","Stage IIIA","Stage IIIB","Stage IIIC","Stage IV","Stage IVA","Stage IVB","Not Reported"] },
  treatment_outcome:            { label: "treatment_outcome",            type: "pv",          description: "Response to treatment.", pvs: ["Complete Response","Partial Response","Stable Disease","Progressive Disease","Not Evaluable"] },
  clinical_notes_text:          { label: "clinical_notes_text",          type: "passthrough", description: "Free-form clinical narrative; passed through unchanged." },
};

// All columns the user's sheet has. classification is what the app decides:
//   "auto_safe"     — confidence high enough to confirm without asking
//   "empty"         — column has no non-null values; auto-skipped
//   "needs_review"  — the user must look
export const COLUMNS = [
  // ─── Auto-safe: high overlap, clear AI rec ──────────────────────────────
  { key: "record_id", classification: "auto_safe", value_count: 4127, distinct_count: 4127,
    suggested_cde: "subject_id", overlap: null, // passthrough — no overlap concept
    distinct_values: [{ value: "R0001", count: 1 }, { value: "R0002", count: 1 }, { value: "R0003", count: 1 }, { value: "R0004", count: 1 }, { value: "R0005", count: 1 }],
    alternatives: [],
  },
  { key: "age_at_enrollment", classification: "auto_safe", value_count: 4124, distinct_count: 86,
    suggested_cde: "age_at_index", overlap: null,
    distinct_values: [{ value: "42", count: 102 }, { value: "55", count: 98 }, { value: "61", count: 87 }, { value: "37", count: 71 }, { value: "73", count: 64 }],
    alternatives: [],
  },
  { key: "sex", classification: "auto_safe", value_count: 4127, distinct_count: 3,
    suggested_cde: "sex", overlap: 1.0,
    distinct_values: [{ value: "Female", count: 2173 }, { value: "Male", count: 1948 }, { value: "Unknown", count: 6 }],
    alternatives: [{ cde: "gender", overlap: 0.0, label: "gender" }],
  },
  { key: "race", classification: "auto_safe", value_count: 4106, distinct_count: 6,
    suggested_cde: "race", overlap: 1.0,
    distinct_values: [{ value: "White", count: 2987 }, { value: "Black or African American", count: 681 }, { value: "Asian", count: 312 }, { value: "Other", count: 78 }, { value: "American Indian or Alaska Native", count: 31 }, { value: "Unknown", count: 17 }],
    alternatives: [],
  },
  { key: "ethnicity", classification: "auto_safe", value_count: 4098, distinct_count: 3,
    suggested_cde: "ethnicity", overlap: 1.0,
    distinct_values: [{ value: "Not Hispanic or Latino", count: 3621 }, { value: "Hispanic or Latino", count: 412 }, { value: "Unknown", count: 65 }],
    alternatives: [],
  },
  { key: "vital_status", classification: "auto_safe", value_count: 4127, distinct_count: 4,
    suggested_cde: "vital_status", overlap: 1.0,
    distinct_values: [{ value: "Alive", count: 3214 }, { value: "Dead", count: 798 }, { value: "Lost to Follow-up", count: 87 }, { value: "Unknown", count: 28 }],
    alternatives: [],
  },
  { key: "primary_diagnosis", classification: "auto_safe", value_count: 4127, distinct_count: 7,
    suggested_cde: "primary_diagnosis", overlap: 1.0,
    distinct_values: [{ value: "Breast Cancer", count: 1108 }, { value: "Lung Cancer", count: 901 }, { value: "Colon Cancer", count: 743 }, { value: "Melanoma", count: 519 }, { value: "Ovarian Cancer", count: 487 }, { value: "Leukemia", count: 261 }, { value: "Pancreatic Cancer", count: 108 }],
    alternatives: [],
  },
  { key: "morphology", classification: "auto_safe", value_count: 4108, distinct_count: 6,
    suggested_cde: "morphology", overlap: 1.0,
    distinct_values: [{ value: "Adenocarcinoma", count: 1487 }, { value: "Ductal Carcinoma", count: 892 }, { value: "Melanoma", count: 519 }, { value: "Squamous Cell Carcinoma", count: 612 }, { value: "Serous Carcinoma", count: 387 }, { value: "Lobular Carcinoma", count: 211 }],
    alternatives: [],
  },
  { key: "site_of_origin", classification: "auto_safe", value_count: 4127, distinct_count: 8,
    suggested_cde: "tissue_or_organ_of_origin", overlap: 1.0,
    distinct_values: [{ value: "Breast", count: 1108 }, { value: "Lung", count: 901 }, { value: "Colon", count: 743 }, { value: "Skin", count: 519 }, { value: "Ovary", count: 487 }, { value: "Pancreas", count: 108 }, { value: "Liver", count: 173 }, { value: "Brain", count: 88 }],
    alternatives: [],
  },
  { key: "gender_identity", classification: "auto_safe", value_count: 3982, distinct_count: 5,
    suggested_cde: "gender", overlap: null,
    distinct_values: [{ value: "woman", count: 2014 }, { value: "man", count: 1781 }, { value: "non-binary", count: 142 }, { value: "transgender woman", count: 28 }, { value: "transgender man", count: 17 }],
    alternatives: [],
  },
  { key: "date_of_birth", classification: "auto_safe", value_count: 4127, distinct_count: 4127,
    suggested_cde: "year_of_birth", overlap: null,
    distinct_values: [{ value: "1964-03-12", count: 1 }, { value: "1972-08-04", count: 1 }, { value: "1958-11-23", count: 1 }, { value: "1989-01-17", count: 1 }, { value: "1950-06-29", count: 1 }],
    alternatives: [],
  },
  { key: "consent_status", classification: "auto_safe", value_count: 4127, distinct_count: 4,
    suggested_cde: "consent_type", overlap: 1.0,
    distinct_values: [{ value: "Broad", count: 3201 }, { value: "Specific", count: 712 }, { value: "Tiered", count: 198 }, { value: "Withdrawn", count: 16 }],
    alternatives: [],
  },
  { key: "collection_site", classification: "auto_safe", value_count: 4127, distinct_count: 7,
    suggested_cde: "tissue_collection_site", overlap: 1.0,
    distinct_values: [{ value: "Mayo Clinic", count: 1102 }, { value: "Mass General", count: 798 }, { value: "UCSF", count: 612 }, { value: "MD Anderson", count: 587 }, { value: "Cleveland Clinic", count: 421 }, { value: "Johns Hopkins", count: 412 }, { value: "Stanford", count: 195 }],
    alternatives: [],
  },
  { key: "sample_type", classification: "auto_safe", value_count: 4127, distinct_count: 5,
    suggested_cde: "sample_type", overlap: 1.0,
    distinct_values: [{ value: "Primary Tumor", count: 2891 }, { value: "Blood", count: 712 }, { value: "Normal Tissue", count: 387 }, { value: "Recurrent Tumor", count: 98 }, { value: "Saliva", count: 39 }],
    alternatives: [],
  },
  { key: "treatment_type", classification: "auto_safe", value_count: 3998, distinct_count: 6,
    suggested_cde: "treatment_type", overlap: 1.0,
    distinct_values: [{ value: "Chemotherapy", count: 1487 }, { value: "Surgery", count: 1102 }, { value: "Radiation Therapy", count: 812 }, { value: "Immunotherapy", count: 412 }, { value: "Targeted Therapy", count: 119 }, { value: "Hormone Therapy", count: 66 }],
    alternatives: [],
  },
  { key: "icd10_diagnosis", classification: "auto_safe", value_count: 4127, distinct_count: 142,
    suggested_cde: "icd10_classification", overlap: null,
    distinct_values: [{ value: "C50.911", count: 412 }, { value: "C34.90", count: 387 }, { value: "C18.9", count: 312 }, { value: "C43.9", count: 519 }, { value: "C56.9", count: 287 }],
    alternatives: [],
  },

  // ─── Empty columns: nothing to do, but user should know they exist ─────
  { key: "legacy_id_v1", classification: "empty", value_count: 0, distinct_count: 0,
    suggested_cde: null, overlap: null, distinct_values: [], alternatives: [],
  },
  { key: "internal_notes", classification: "empty", value_count: 0, distinct_count: 0,
    suggested_cde: null, overlap: null, distinct_values: [], alternatives: [],
  },
  { key: "_temp_col", classification: "empty", value_count: 0, distinct_count: 0,
    suggested_cde: null, overlap: null, distinct_values: [], alternatives: [],
  },

  // ─── Needs review: the careful work ─────────────────────────────────────
  // Ambiguous: two plausible CDEs, similar overlap
  { key: "diagnosis_status", classification: "needs_review", value_count: 4108, distinct_count: 6,
    suggested_cde: "primary_diagnosis_status", overlap: 0.67,
    risk: "ambiguous",
    risk_reason: "Two CDEs match closely. \"disease_status\" matches 81% of your values; the AI suggestion only matches 67%.",
    distinct_values: [
      { value: "Active", count: 1812 },
      { value: "Newly Diagnosed", count: 1102 },  // not in primary_diagnosis_status; is in disease_status
      { value: "Remission", count: 678 },          // close but PV is "In Remission"
      { value: "Recurrence", count: 312 },         // matches primary_diagnosis_status
      { value: "Progressive", count: 187 },        // only in disease_status
      { value: "Unknown", count: 17 },
    ],
    alternatives: [
      { cde: "disease_status", overlap: 0.81, label: "disease_status" },
      { cde: "primary_diagnosis_status", overlap: 0.67, label: "primary_diagnosis_status" },
    ],
  },
  // Case-mismatch: values look right but won't match the PV strings literally
  { key: "tumor_grade", classification: "needs_review", value_count: 3812, distinct_count: 5,
    suggested_cde: "histologic_grade", overlap: 0.40,
    risk: "case_mismatch",
    risk_reason: "Your column uses \"Grade 3\" form; the standard uses \"G3\". Confirming will rewrite 3 of your 5 distinct values.",
    distinct_values: [
      { value: "G1", count: 412 },          // matches
      { value: "G2", count: 1087 },         // matches
      { value: "Grade 3", count: 1521 },    // doesn't match — PV is G3
      { value: "Grade 4", count: 587 },     // doesn't match — PV is G4
      { value: "Unknown", count: 205 },     // doesn't match — PV is "Not Reported"
    ],
    alternatives: [
      { cde: "histologic_grade", overlap: 0.40, label: "histologic_grade" },
    ],
  },
  // Subtle case-mismatch: 'IIIa' vs 'IIIA'. All 8 are mismatched on case.
  { key: "staging", classification: "needs_review", value_count: 3987, distinct_count: 8,
    suggested_cde: "ajcc_pathologic_stage", overlap: 0.0,
    risk: "case_mismatch",
    risk_reason: "The standard uses uppercase letters (\"Stage IIIA\") — your column has \"stage IIIa\". All 8 distinct values will be rewritten.",
    distinct_values: [
      { value: "stage I", count: 487 },
      { value: "stage IIa", count: 612 },
      { value: "stage IIb", count: 587 },
      { value: "stage IIIa", count: 1102 },
      { value: "stage IIIb", count: 712 },
      { value: "stage IV", count: 412 },
      { value: "stage IVa", count: 75 },
      { value: "unknown", count: 20 },
    ],
    alternatives: [
      { cde: "ajcc_pathologic_stage", overlap: 0.0, label: "ajcc_pathologic_stage" },
    ],
  },
  // Low overlap + likely wrong AI rec — most dangerous case
  { key: "therapy_response", classification: "needs_review", value_count: 2891, distinct_count: 142,
    suggested_cde: "treatment_outcome", overlap: 0.20,
    risk: "low_overlap",
    risk_reason: "Only 20% of your values match the standard. The remaining 80% look like free-text notes — they would be left as-is, but the mapping may be wrong.",
    distinct_values: [
      { value: "Complete Response", count: 412 },
      { value: "Partial Response", count: 587 },
      { value: "patient reports no improvement after 3 cycles", count: 1 },
      { value: "mild side effects, continuing tx", count: 1 },
      { value: "progressed on imaging 6mo follow-up", count: 1 },
      { value: "Stable Disease", count: 287 },
      { value: "Progressive Disease", count: 198 },
    ],
    alternatives: [
      { cde: "treatment_outcome", overlap: 0.20, label: "treatment_outcome" },
    ],
  },
  // Probably should be "No Mapping"
  { key: "ethnicity_secondary", classification: "needs_review", value_count: 412, distinct_count: 6,
    suggested_cde: "race", overlap: 0.12,
    risk: "wrong_mapping",
    risk_reason: "AI suggested \"race\" but only 12% of values match. Values like \"Sicilian\" and \"Cajun\" suggest free-form heritage notes, not race.",
    distinct_values: [
      { value: "Sicilian", count: 87 },
      { value: "Cajun", count: 41 },
      { value: "Ashkenazi Jewish", count: 112 },
      { value: "Filipino", count: 78 },
      { value: "White", count: 49 },
      { value: "Asian", count: 45 },
    ],
    alternatives: [
      { cde: "race", overlap: 0.12, label: "race" },
    ],
  },
  // Sensitive passthrough — user may want to not include
  { key: "clinical_notes", classification: "needs_review", value_count: 2143, distinct_count: 2143,
    suggested_cde: "clinical_notes_text", overlap: null,
    risk: "sensitive_passthrough",
    risk_reason: "This column will pass through unchanged. Free-text notes can contain PHI — confirm the destination standard handles this appropriately.",
    distinct_values: [
      { value: "Pt tolerating chemo well, no dose reduction needed.", count: 1 },
      { value: "Family hx significant for BRCA mutation.", count: 1 },
      { value: "Refused biopsy; will re-image in 6mo.", count: 1 },
      { value: "Adjuvant tamoxifen started 2024-08-14.", count: 1 },
    ],
    alternatives: [],
  },
];

export const COUNTS = {
  total: COLUMNS.length,
  auto_safe: COLUMNS.filter(c => c.classification === "auto_safe").length,
  empty: COLUMNS.filter(c => c.classification === "empty").length,
  needs_review: COLUMNS.filter(c => c.classification === "needs_review").length,
};

export const RISK_LABEL = {
  ambiguous: "Two standards match — pick one",
  case_mismatch: "Values will be rewritten to match the standard",
  low_overlap: "Most of your values don't match the standard",
  wrong_mapping: "AI suggestion looks wrong",
  sensitive_passthrough: "Free text — values pass through unchanged",
};

export const RISK_TONE = {
  ambiguous: "magenta",        // needs decision, not dangerous
  case_mismatch: "magenta",    // rewrite — they should see this
  low_overlap: "warning",      // suggestion may be wrong
  wrong_mapping: "warning",    // definitely re-examine
  sensitive_passthrough: "info",
};
