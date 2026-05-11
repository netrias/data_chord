#!/usr/bin/env python3
"""Generate mock data for the Stage 2 redesign mock.

Exercises the documented edge cases: ~70 columns, columns with up to 10k
distinct values, target ontologies up to 14k permissible values, mix of
all-unique columns, free text, and small categoricals.

Run directly; produces ``data.js`` next to this file.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Callable, TypedDict

random.seed(42)

ROW_COUNT = 12_000


def _pad_synthetic(out: set[str], n: int, prefix: str) -> list[str]:
    """Backstop: if a probabilistic generator stalls, pad with deterministic uniques.

    Lets every PV generator terminate even when the random combinatoric space is
    smaller than expected (caught one infinite loop the slow way already).
    """
    i = 0
    while len(out) < n:
        out.add(f"{prefix}_pad_{i:05d}")
        i += 1
    return sorted(out)


def _gen_icd10(n: int) -> list[str]:
    out: set[str] = set()
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        cat = random.choice(string.ascii_uppercase)
        major = random.randint(0, 99)
        minor = random.choice(["", *(f".{i}" for i in range(10))])
        out.add(f"{cat}{major:02d}{minor}")
    return _pad_synthetic(out, n, "icd10")


def _gen_genes(n: int) -> list[str]:
    bases = ["TP","BRCA","EGFR","KRAS","PTEN","RB","MYC","ERBB","ABL","ALK","BRAF","CDK","MET","NF","PIK3","STK","VHL","WT","APC","ATM","NRAS","HRAS","NOTCH","SMAD","JAK","MTOR","FGFR","PDGFR","BCR","RET","FLT","IDH","CTNNB","TET","DNMT","EZH","KDM","ARID","SETD","SF3B"]
    suffixes = ["", "A", "B", "C", "D", "L", "P", "AS", "OS", "R", "L1", "L2", "BP"]
    out: set[str] = set()
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        out.add(f"{random.choice(bases)}{random.randint(1, 199)}{random.choice(suffixes)}")
    return _pad_synthetic(out, n, "gene")


def _gen_drugs(n: int) -> list[str]:
    pre = ["ato","beva","cetu","dabra","erlot","gefit","imati","laro","nivo","pembro","rituxi","sora","trastu","veme","crizo","palbo","rega","ruxo","ruci","selume","tame","tofa","upada","vorino","abe","afati","apre","ave","baricit","bos","brigatinib","calvi","cana","celec","cobimet","crom","datopo","dolute","duvelisib","encora"]
    suf = ["mab","nib","mycin","cillin","statin","oxetine","pram","azole","stat","prazole","sartan","tinib","fenib","setib","penem","fenacin","romide"]
    mods = ["", " hydrochloride", " sulfate", " citrate", " maleate", " sodium", " calcium"]
    doses = ["", " 5mg", " 10mg", " 25mg", " 50mg", " 100mg", " 200mg", " 500mg", " 1mg", " 2mg"]
    forms = ["", " tablet", " capsule", " injection", " oral solution", " syrup"]
    out: set[str] = set()
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        out.add(
            f"{random.choice(pre)}{random.choice(suf)}{random.choice(mods)}"
            f"{random.choice(doses)}{random.choice(forms)}".strip()
        )
    return _pad_synthetic(out, n, "drug")


def _gen_diagnoses(n: int) -> list[str]:
    base = ["Adenocarcinoma","Astrocytoma","Basal Cell Carcinoma","Breast Cancer","Cervical Cancer","Colorectal Cancer","Ductal Carcinoma","Endometrial Cancer","Esophageal Cancer","Follicular Lymphoma","Gastric Cancer","Glioblastoma","Hepatocellular Carcinoma","Hodgkin Lymphoma","Invasive Ductal Carcinoma","Kidney Cancer","Leukemia","Liver Cancer","Lobular Carcinoma","Lung Cancer","Lymphoma","Melanoma","Mesothelioma","Multiple Myeloma","Nasopharyngeal Carcinoma","Neuroblastoma","Non-Hodgkin Lymphoma","Oral Cancer","Osteosarcoma","Ovarian Cancer","Pancreatic Cancer","Prostate Cancer","Renal Cell Carcinoma","Retinoblastoma","Rhabdomyosarcoma","Sarcoma","Skin Cancer","Squamous Cell Carcinoma","Stomach Cancer","Testicular Cancer","Thyroid Cancer","Urothelial Carcinoma","Uterine Cancer","Wilms Tumor","Bronchogenic Carcinoma"]
    qual = ["NOS","Recurrent","Metastatic","Stage I","Stage II","Stage III","Stage IV","Pediatric","Adult","Familial","High Grade","Low Grade","Mixed Type","Poorly Differentiated","Well Differentiated"]
    site = ["of the Lung","of the Breast","of the Colon","of the Skin","of the Liver","of the Kidney","of the Brain","of the Pancreas","of the Stomach","of the Ovary","of the Prostate","of the Bladder","of the Esophagus","of the Cervix"]
    out: set[str] = set(base)
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        b = random.choice(base)
        q = random.choice([*qual, ""])
        s = random.choice([*site, ""])
        out.add(" ".join(p for p in [q, b, s] if p))
    return _pad_synthetic(out, n, "diagnosis")


def _gen_anatomic(n: int) -> list[str]:
    base = ["Adrenal Gland","Bladder","Bone","Bone Marrow","Brain","Breast","Cervix","Colon","Esophagus","Eye","Gallbladder","Heart","Kidney","Larynx","Liver","Lung","Lymph Node","Mouth","Ovary","Pancreas","Pharynx","Prostate","Rectum","Skin","Small Intestine","Spleen","Stomach","Testis","Thyroid","Tongue","Uterus"]
    qual = ["Left","Right","Upper","Lower","Anterior","Posterior"]
    out: set[str] = set(base)
    attempts = 0
    while len(out) < n and attempts < n * 50:
        attempts += 1
        out.add(f"{random.choice(qual)} {random.choice(base)}")
    return _pad_synthetic(out, n, "anatomic")


def _gen_synthetic(prefix: str, n: int) -> list[str]:
    return sorted({f"{prefix}_v{i:04d}" for i in range(n)})


def _gen_numeric_values(n: int, low: int = 0, high: int = 10_000) -> list[str]:
    """Distinct numeric strings — integers within range, float fallback when needed."""
    span = high - low + 1
    if n <= span:
        return [str(v) for v in random.sample(range(low, high + 1), n)]
    out: set[str] = {str(i) for i in range(low, high + 1)}
    while len(out) < n:
        out.add(f"{random.uniform(low, high):.2f}")
    return list(out)[:n]


def _is_numeric(s: str) -> bool:
    """A column value counts as numeric if Python's float() can parse it."""
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


# ---------- CDE catalog ------------------------------------------------------

PvSource = Callable[[int], list[str]] | list[str] | None

# (key, label, description, pv_count, generator-or-fixed-list, note)
CDE_DEFS: list[tuple[str, str, str, int, PvSource, str | None]] = [
    ("icd10_diagnosis", "icd10_diagnosis",
     "International Classification of Diseases v10 code.",
     14_000, _gen_icd10,
     "Aligned with WHO ICD-10 release 2019; superset of legacy ICD-9 mappings."),
    ("hgnc_gene_symbol", "hgnc_gene_symbol",
     "HUGO Gene Nomenclature Committee approved gene symbol.",
     8_500, _gen_genes,
     "Mirrors HGNC quarterly release; legacy aliases live in legacy_gene_alias."),
    ("rxnorm_drug_name", "rxnorm_drug_name",
     "Normalized drug ingredient name from RxNorm.",
     9_800, _gen_drugs,
     "Brand names map back to ingredient via RxNorm relationships."),
    ("tumor_diagnosis", "tumor_diagnosis",
     "Primary tumor diagnosis encoded against NCI Thesaurus disease ontology.",
     1_247, _gen_diagnoses,
     "Aligned with NCI Thesaurus disease ontology; values overlap with primary_site_disease in legacy datasets."),
    ("anatomic_site", "anatomic_site",
     "Anatomic site of the primary tumor.",
     89, _gen_anatomic,
     "Derived from SNOMED-CT body structure hierarchy, flattened."),
    ("histological_grade", "histological_grade",
     "Histological grade of the tumor as determined by pathology.",
     5, ["G1", "G2", "G3", "G4", "GX"], None),
    ("ajcc_stage", "ajcc_stage",
     "AJCC clinical stage at diagnosis.",
     12, ["Stage 0", "Stage I", "Stage IA", "Stage IB", "Stage II", "Stage IIA", "Stage IIB", "Stage III", "Stage IIIA", "Stage IIIB", "Stage IV", "Stage IVB"], None),
    ("sex_at_birth", "sex_at_birth",
     "Biological sex assigned at birth.",
     4, ["Female", "Male", "Other", "Unknown"], None),
    ("race", "race",
     "Self-reported race per OMB categories.",
     10, ["American Indian or Alaska Native", "Asian", "Black or African American", "More than one race", "Native Hawaiian or Other Pacific Islander", "Not Reported", "Other", "Refused", "Unknown", "White"], None),
    ("ethnicity", "ethnicity",
     "Self-reported ethnicity per OMB categories.",
     6, ["Hispanic or Latino", "Not Hispanic or Latino", "Not Reported", "Other", "Refused", "Unknown"], None),
    ("smoking_status", "tobacco_smoking_status",
     "Current tobacco smoking status of the patient.",
     4, ["Current", "Former", "Never", "Unknown"], None),
    ("alcohol_status", "alcohol_use_status",
     "Self-reported alcohol consumption status.",
     5, ["Current", "Former", "Heavy", "Never", "Unknown"], None),
    ("preservation_method", "preservation_method",
     "Specimen preservation method.",
     8, ["Cell Pellet", "DNA", "FFPE", "Fresh", "Frozen", "Plasma", "RNA", "Serum"], None),
    ("consent_status", "consent_status",
     "Patient consent status for research use.",
     4, ["Consented", "Pending", "Unknown", "Withdrawn"], None),
    ("vital_status", "vital_status",
     "Patient vital status.",
     4, ["Alive", "Deceased", "Lost to Follow Up", "Unknown"], None),
    ("country_iso", "country_iso_code",
     "ISO 3166-1 alpha-2 country code.",
     195, None, None),
    ("us_state", "us_state_abbreviation",
     "US state postal abbreviation.",
     50, None, None),
    ("language_iso", "language_preferred_iso_code",
     "ISO 639-1 preferred language code.",
     200, None, None),
    ("specimen_type", "specimen_type",
     "Type of biological specimen collected.",
     50, None, None),
    ("variant_classification", "variant_classification_type",
     "Functional classification of genetic variant.",
     12, None, None),
    ("assay_platform", "assay_platform",
     "Sequencing or assay platform used.",
     50, None, None),
    ("admin_route", "drug_administration_route",
     "Route of drug administration.",
     30, None, None),
    ("dose_frequency", "drug_dose_frequency",
     "Frequency of drug administration.",
     50, None, None),
    ("body_system", "body_system",
     "Body system affected.",
     24, None, None),
    ("hist_subtype_long", "histological_subtype_classification_with_modifiers",
     "Detailed histological subtype with modifiers per WHO 2022 classification.",
     340, None, None),
    ("therapy_class_long", "anti_cancer_therapy_class_with_combination_modifiers",
     "Therapeutic class for anti-cancer agents including combination modifiers.",
     78, None, None),
    # ── Non-PV CDEs: numeric and pass-through types ──────────────────────────
    # These have no permissible value list. Validation/match semantics differ —
    # see CDE_TYPES and the build loop below.
    ("numeric_age", "numeric_age",
     "Patient age in years. Numeric field — values must parse as numbers.",
     0, None, None),
    ("numeric_weight_kg", "numeric_weight_kg",
     "Patient weight in kilograms. Numeric field — values must parse as numbers.",
     0, None, None),
    ("text_passthrough_notes", "text_passthrough_clinical_notes",
     "Free-text clinical notes. Stored as-is; no validation.",
     0, None, None),
    ("identifier_passthrough", "identifier_passthrough",
     "Patient or specimen identifier. Stored as-is; no validation.",
     0, None, None),
]


# Maps a CDE key to its semantic type. Default for keys not listed here is "pv".
#   pv          — column values must match an entry in the CDE's permissible value list
#   numeric     — column values must parse as numbers; no PV list
#   passthrough — values are stored as-is; no validation
CDE_TYPES: dict[str, str] = {
    "numeric_age": "numeric",
    "numeric_weight_kg": "numeric",
    "text_passthrough_notes": "passthrough",
    "identifier_passthrough": "passthrough",
}


def _build_pvs() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, _label, _desc, count, source, _note in CDE_DEFS:
        # Non-PV types (numeric, passthrough) own no permissible-value list.
        if CDE_TYPES.get(key, "pv") != "pv":
            out[key] = []
            continue
        if isinstance(source, list):
            out[key] = sorted(source)
        elif callable(source):
            out[key] = source(count)
        else:
            out[key] = _gen_synthetic(key, count)
    return out


# ---------- Column specs -----------------------------------------------------


class _ColumnSpecOptional(TypedDict, total=False):
    override: str
    value_kind: str            # "string" (default) | "numeric"
    numeric_low: int
    numeric_high: int


class ColumnSpec(_ColumnSpecOptional):
    header: str
    ai: str | None
    distinct: int
    match_pct: float
    status: str  # "rec" | "ovr" | "none"


# match_pct = fraction of distinct values in this column that already match a PV
# of the AI-recommended CDE (drives the conformance check story).
COLUMN_SPECS: list[ColumnSpec] = [
    {"header": "icd10_code", "ai": "icd10_diagnosis", "distinct": 9_500, "match_pct": 0.85, "status": "rec"},
    {"header": "drug_name", "ai": "rxnorm_drug_name", "distinct": 4_000, "match_pct": 0.62, "status": "rec"},
    {"header": "gene_symbol", "ai": "hgnc_gene_symbol", "distinct": 1_200, "match_pct": 0.78, "status": "rec"},
    {"header": "diagnosis", "ai": "tumor_diagnosis", "distinct": 312, "match_pct": 0.45, "status": "rec"},
    {"header": "diagnosis_freetext", "ai": "tumor_diagnosis", "distinct": 8_000, "match_pct": 0.08, "status": "ovr", "override": "icd10_diagnosis"},
    {"header": "patient_id", "ai": "identifier_passthrough", "distinct": ROW_COUNT, "match_pct": 0, "status": "rec"},
    {"header": "sample_id", "ai": None, "distinct": ROW_COUNT, "match_pct": 0, "status": "none"},
    {"header": "accession_number", "ai": None, "distinct": ROW_COUNT - 12, "match_pct": 0, "status": "none"},
    {"header": "mrn", "ai": None, "distinct": 8_400, "match_pct": 0, "status": "none"},
    {"header": "biopsy_date", "ai": None, "distinct": 3_200, "match_pct": 0, "status": "none"},
    {"header": "collection_date", "ai": None, "distinct": 3_100, "match_pct": 0, "status": "none"},
    {"header": "report_date", "ai": None, "distinct": 2_900, "match_pct": 0, "status": "none"},
    {"header": "date_of_birth", "ai": None, "distinct": 7_800, "match_pct": 0, "status": "none"},
    {"header": "age", "ai": "numeric_age", "distinct": 87, "match_pct": 0, "status": "rec",
     "value_kind": "numeric", "numeric_low": 0, "numeric_high": 120},
    {"header": "weight_kg", "ai": "numeric_weight_kg", "distinct": 1_200, "match_pct": 0, "status": "rec",
     "value_kind": "numeric", "numeric_low": 30, "numeric_high": 220},
    {"header": "height_cm", "ai": None, "distinct": 78, "match_pct": 0, "status": "none"},
    {"header": "bmi", "ai": None, "distinct": 1_800, "match_pct": 0, "status": "none"},
    {"header": "systolic_bp", "ai": None, "distinct": 95, "match_pct": 0, "status": "none"},
    {"header": "diastolic_bp", "ai": None, "distinct": 62, "match_pct": 0, "status": "none"},
    {"header": "heart_rate", "ai": None, "distinct": 110, "match_pct": 0, "status": "none"},
    {"header": "sex", "ai": "sex_at_birth", "distinct": 3, "match_pct": 1.0, "status": "rec"},
    {"header": "race", "ai": "race", "distinct": 7, "match_pct": 0.85, "status": "rec"},
    {"header": "ethnicity", "ai": "ethnicity", "distinct": 4, "match_pct": 1.0, "status": "rec"},
    {"header": "vital_status", "ai": "vital_status", "distinct": 3, "match_pct": 1.0, "status": "rec"},
    {"header": "smoking_status", "ai": "smoking_status", "distinct": 4, "match_pct": 0.75, "status": "rec"},
    {"header": "alcohol_use", "ai": "alcohol_status", "distinct": 4, "match_pct": 0.5, "status": "ovr", "override": "smoking_status"},
    {"header": "tumor_grade", "ai": "histological_grade", "distinct": 5, "match_pct": 0.8, "status": "rec"},
    {"header": "tumor_stage", "ai": "ajcc_stage", "distinct": 8, "match_pct": 0.625, "status": "rec"},
    {"header": "primary_site", "ai": "anatomic_site", "distinct": 35, "match_pct": 0.85, "status": "rec"},
    {"header": "metastatic_site", "ai": "anatomic_site", "distinct": 28, "match_pct": 0.75, "status": "rec"},
    {"header": "specimen_type", "ai": "specimen_type", "distinct": 12, "match_pct": 0.83, "status": "rec"},
    {"header": "preservation_method", "ai": "preservation_method", "distinct": 6, "match_pct": 1.0, "status": "rec"},
    {"header": "consent_status", "ai": "consent_status", "distinct": 4, "match_pct": 1.0, "status": "rec"},
    {"header": "histological_subtype_classification_with_modifiers", "ai": "hist_subtype_long", "distinct": 89, "match_pct": 0.62, "status": "rec"},
    {"header": "anti_cancer_therapy_class_with_combination_modifiers", "ai": "therapy_class_long", "distinct": 23, "match_pct": 0.48, "status": "rec"},
    {"header": "lab_glucose", "ai": None, "distinct": 245, "match_pct": 0, "status": "none"},
    {"header": "lab_creatinine", "ai": None, "distinct": 187, "match_pct": 0, "status": "none"},
    {"header": "lab_hemoglobin", "ai": None, "distinct": 212, "match_pct": 0, "status": "none"},
    {"header": "lab_platelets", "ai": None, "distinct": 320, "match_pct": 0, "status": "none"},
    {"header": "lab_wbc", "ai": None, "distinct": 167, "match_pct": 0, "status": "none"},
    {"header": "country", "ai": "country_iso", "distinct": 18, "match_pct": 0.5, "status": "rec"},
    {"header": "state", "ai": "us_state", "distinct": 50, "match_pct": 1.0, "status": "rec"},
    {"header": "zip", "ai": None, "distinct": 421, "match_pct": 0, "status": "none"},
    {"header": "preferred_language", "ai": "language_iso", "distinct": 8, "match_pct": 0.62, "status": "rec"},
    {"header": "drug_dose", "ai": None, "distinct": 245, "match_pct": 0, "status": "none"},
    {"header": "drug_route", "ai": "admin_route", "distinct": 9, "match_pct": 0.78, "status": "rec"},
    {"header": "drug_frequency", "ai": "dose_frequency", "distinct": 18, "match_pct": 0.83, "status": "rec"},
    {"header": "variant_class", "ai": "variant_classification", "distinct": 7, "match_pct": 0.86, "status": "rec"},
    {"header": "assay_platform", "ai": "assay_platform", "distinct": 11, "match_pct": 0.55, "status": "ovr", "override": "specimen_type"},
    {"header": "sequencer", "ai": None, "distinct": 8, "match_pct": 0, "status": "none"},
    {"header": "read_length", "ai": None, "distinct": 5, "match_pct": 0, "status": "none"},
    {"header": "tumor_purity_pct", "ai": None, "distinct": 73, "match_pct": 0, "status": "none"},
    {"header": "notes", "ai": "text_passthrough_notes", "distinct": 6_700, "match_pct": 0, "status": "rec"},
    {"header": "clinician_comments", "ai": None, "distinct": 4_200, "match_pct": 0, "status": "none"},
    {"header": "pathology_summary", "ai": None, "distinct": 5_100, "match_pct": 0, "status": "none"},
    {"header": "study_id", "ai": None, "distinct": 12, "match_pct": 0, "status": "none"},
    {"header": "cohort", "ai": None, "distinct": 6, "match_pct": 0, "status": "none"},
    {"header": "site_id", "ai": None, "distinct": 9, "match_pct": 0, "status": "none"},
    {"header": "enrolled_by", "ai": None, "distinct": 28, "match_pct": 0, "status": "none"},
    {"header": "data_source", "ai": None, "distinct": 5, "match_pct": 0, "status": "none"},
    {"header": "record_version", "ai": None, "distinct": 4, "match_pct": 0, "status": "none"},
    {"header": "created_by", "ai": None, "distinct": 32, "match_pct": 0, "status": "none"},
    {"header": "updated_by", "ai": None, "distinct": 35, "match_pct": 0, "status": "none"},
    {"header": "created_at", "ai": None, "distinct": 11_400, "match_pct": 0, "status": "none"},
    {"header": "updated_at", "ai": None, "distinct": 11_800, "match_pct": 0, "status": "none"},
    {"header": "is_active", "ai": None, "distinct": 2, "match_pct": 0, "status": "none"},
    {"header": "flag_for_review", "ai": None, "distinct": 2, "match_pct": 0, "status": "none"},
    {"header": "flag_priority", "ai": None, "distinct": 4, "match_pct": 0, "status": "none"},
    {"header": "external_ref", "ai": None, "distinct": 6_800, "match_pct": 0, "status": "none"},
    {"header": "study_arm", "ai": None, "distinct": 8, "match_pct": 0, "status": "none"},
]


def _gen_distinct_values(spec: ColumnSpec, distinct: int, pvs: dict[str, list[str]]) -> list[str]:
    """Build the distinct values for a column.

    For numeric columns we generate plausible numeric strings in the configured
    range. For PV-backed columns, ``match_pct`` of values are drawn from the AI
    CDE's permissible value set so conformance counts look realistic.
    """
    if spec.get("value_kind") == "numeric":
        low = spec.get("numeric_low", 0)
        high = spec.get("numeric_high", 10_000)
        return _gen_numeric_values(distinct, low, high)

    ai_cde = spec.get("ai")
    match_pct = spec.get("match_pct", 0.0)
    matched: list[str] = []
    if ai_cde and ai_cde in pvs and pvs[ai_cde]:
        n_match = min(int(distinct * match_pct), len(pvs[ai_cde]))
        if n_match:
            matched = random.sample(pvs[ai_cde], n_match)

    n_unmatched = distinct - len(matched)
    unmatched: list[str] = []
    while len(unmatched) < n_unmatched:
        if matched and random.random() < 0.3:
            unmatched.append(random.choice(matched).lower() + "_x" + str(len(unmatched)))
        else:
            unmatched.append("u_" + "".join(random.choices(string.ascii_letters, k=8)) + str(len(unmatched)))
    return matched + unmatched


def _gen_frequencies(distinct: int, all_unique: bool) -> list[int]:
    """Power-law frequency distribution that sums to ROW_COUNT for non-unique columns."""
    if all_unique:
        return [1] * distinct

    freqs: list[int] = []
    remaining = ROW_COUNT
    for i in range(distinct):
        if remaining <= 0:
            freqs.append(1)
            continue
        if i < min(20, distinct):
            f = max(1, int(remaining * 0.18 / (i + 1)))
        else:
            f = max(1, int(remaining / max(1, distinct - i + 5)))
        f = min(f, max(1, remaining))
        freqs.append(f)
        remaining -= f

    # Pad: any row-count slack pushes onto the top bucket.
    if remaining > 0:
        freqs[0] += remaining
    return freqs


def _build_columns(pvs: dict[str, list[str]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    pv_sets = {k: set(v) for k, v in pvs.items()}

    for spec in COLUMN_SPECS:
        distinct = min(spec["distinct"], ROW_COUNT)
        all_unique = distinct >= ROW_COUNT
        ai = spec.get("ai")
        override = spec.get("override")
        selected_cde = override or ai

        values = _gen_distinct_values(spec, distinct, pvs)
        random.shuffle(values)
        freqs = _gen_frequencies(distinct, all_unique)

        pairs = sorted(zip(values, freqs), key=lambda p: -p[1])
        selected_pv_set = pv_sets.get(selected_cde, set()) if selected_cde else set()

        # Emit every distinct value — the takeover virtualizes the list, so the
        # full set is shippable. Replaces the earlier top-200 + tail-summary cap.
        samples = [
            {"v": v, "c": c, "match": v in selected_pv_set}
            for v, c in pairs
        ]

        # Match counts for every CDE in the catalog (sparse — only positives).
        # Semantics differ by CDE type:
        #   pv          → distinct values that appear in the CDE's PV set
        #   numeric     → distinct values that parse as numbers
        #   passthrough → all distinct values (everything passes through)
        distinct_set = set(values)
        numeric_count = sum(1 for v in distinct_set if _is_numeric(v))
        match_counts: dict[str, int] = {}
        for k, pv_set in pv_sets.items():
            cde_type = CDE_TYPES.get(k, "pv")
            if cde_type == "pv":
                n = len(distinct_set & pv_set)
            elif cde_type == "numeric":
                n = numeric_count
            elif cde_type == "passthrough":
                n = len(distinct_set)
            else:
                n = 0
            if n:
                match_counts[k] = n

        out.append({
            "key": spec["header"],
            "header": spec["header"],
            "status": spec["status"],
            "ai_cde": ai,
            "override_cde": override,
            "all_unique": all_unique,
            "rows": ROW_COUNT,
            "distinct": distinct,
            "null_pct": round(random.uniform(0, 5), 1),
            "samples": samples,
            "match_counts": match_counts,
        })

    return out


def main() -> None:
    pvs = _build_pvs()
    columns = _build_columns(pvs)

    catalog = [
        {
            "key": k,
            "label": label,
            "description": desc,
            "pv_count": len(pvs[k]),
            "type": CDE_TYPES.get(k, "pv"),
        }
        for k, label, desc, _, _, _ in CDE_DEFS
    ]
    notes = {k: note for k, _, _, _, _, note in CDE_DEFS if note}

    data = {
        "row_count": ROW_COUNT,
        "cde_catalog": catalog,
        "cde_notes": notes,
        "cde_pvs": pvs,
        "columns": columns,
    }

    out_path = Path(__file__).parent / "data.js"
    payload = "window.mockData = " + json.dumps(data, separators=(",", ":")) + ";\n"
    out_path.write_text(payload)

    print(f"wrote {out_path}")
    print(f"  columns: {len(columns)}")
    print(f"  CDEs:    {len(catalog)}")
    print(f"  PVs:     {sum(len(v) for v in pvs.values()):,} total")
    print(f"  size:    {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
