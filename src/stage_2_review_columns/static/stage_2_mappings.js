/**
 * Handle column mapping review and user overrides before harmonization.
 * Reads analysis payload from storage, renders mapping UI, and persists user selections.
 */
import { initStepInstruction, setActiveStage, initNavigationEvents, isSafeRelativeUrl, advanceMaxReachedStage } from '/assets/shared/step-instruction-ui.js';
import { STAGE_2_PAYLOAD_KEY, STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, isValidFileId, removeFromSession, readFromSession, writeToSession } from '/assets/shared/storage-keys.js';
import { createCombobox } from '/assets/shared/combobox.js';
import { determineRowState } from '/assets/shared/row-state.js';

/* Configuration and constants */
const config = window.stageTwoConfig ?? {};
const HARMONIZE_BUTTON_LABEL = 'Harmonize →';
const NO_MAPPING_OPTION = config.noMappingLabel ?? 'No Mapping';
const stageThreeUrl = config.stageThreeUrl ?? '/stage-3';

/* why: index 0 is the 'No Mapping' option, visually separated from CDE options. */
const NO_MAPPING_INDEX = 0;

/* Build options from dynamic CDE data or fall back to legacy format */
const cdeOptions = config.cdeOptions ?? [];
const cdeLabels = cdeOptions.map((cde) => cde.cde_key ?? cde.label ?? cde);
const manualOptions = [NO_MAPPING_OPTION, ...cdeLabels];

/* Build lookup for CDE metadata (for tooltips/descriptions) */
const cdeByKey = new Map(cdeOptions.map((cde) => [cde.cde_key ?? cde.label ?? cde, cde]));

/* CSS class constants */
const CSS_MAPPING_ROW = 'mapping-row';
const CSS_MAPPING_TD = 'mapping-td';
const CSS_SUGGESTION_TARGET = 'suggestion-target';

/* User-facing messages */
const MSG_NO_ANALYSIS_DATA = 'No analysis data found. Upload a file on Stage 1 to begin.';
const MSG_NO_COLUMNS = 'No columns to display.';
const MSG_UPLOAD_HINT = 'Upload a file on Stage 1 to get started.';
const MSG_MANIFEST_MISSING = 'Manifest missing. Please rerun analysis before harmonizing.';
const MSG_INVALID_FILE = 'Invalid file reference. Please restart the upload process.';
const MSG_STORAGE_ERROR = 'Unable to prepare harmonization request. Please enable browser storage and retry.';

const mappingResults = document.getElementById('mappingResults');
const mappingHint = document.getElementById('mappingHint');
const emptyState = document.getElementById('mappingEmptyState');
const harmonizeButton = document.getElementById('harmonizeButton');
const harmonizeButtonText = harmonizeButton?.querySelector('.btn-3d-front');
const state = {
  payload: null,
  manualSelections: new Map(),
  isSubmitting: false,
};

/* why: use shared utilities for consistent session storage handling. */
const _savePayloadToStorage = (payload) => {
  writeToSession(STAGE_2_PAYLOAD_KEY, payload);
};

const _readPayloadFromStorage = () => {
  return readFromSession(STAGE_2_PAYLOAD_KEY);
};

/** Look up CDE target suggestions for a column, checking case variants. */
const _getColumnSuggestions = (column) => {
  if (!column?.column_name) {
    return [];
  }
  const targets = state.payload?.cde_targets ?? {};
  return (
    targets[column.column_name] ||
    targets[column.column_name.toLowerCase()] ||
    targets[column.column_name.toUpperCase()] ||
    []
  );
};

/** Save user overrides to payload and persist to storage. */
const _persistManualOverrides = () => {
  if (!state.payload) {
    return;
  }
  const overrides = Object.fromEntries(state.manualSelections.entries());
  const nextPayload = { ...state.payload, manual_overrides: overrides };
  state.payload = nextPayload;
  _savePayloadToStorage(nextPayload);
};

/** Store stage 3 payload in session storage for handoff. */
const _persistStageThreePayload = (body) => {
  const payloadForStageThree = {
    request: body,
    context: {
      fileName: state.payload?.file_name || 'Uploaded dataset',
      totalRows: state.payload?.total_rows ?? null,
      targetSchema: config.targetSchema,
    },
    manifest: state.payload?.manifest || null,
  };
  return writeToSession(STAGE_3_PAYLOAD_KEY, payloadForStageThree);
};

/** Normalize column name for consistent Map key lookups. */
const _normalizeColumnKey = (columnName) => (columnName ?? '').toLowerCase();

/** Build and render a single mapping row for a column. */
const _buildMappingRow = (column) => {
  const suggestions = _getColumnSuggestions(column);
  const topTarget = suggestions[0];
  const normalizedKey = _normalizeColumnKey(column.column_name);
  const manualSelection = state.manualSelections.get(normalizedKey) ?? null;

  const row = document.createElement('div');
  const aiRecommendation = topTarget?.target ?? null;
  const { state: rowState, icon: statusIcon } = determineRowState({
    aiRecommendation,
    userSelection: manualSelection,
    noMappingValue: NO_MAPPING_OPTION,
  });
  row.className = `${CSS_MAPPING_ROW} ${CSS_MAPPING_ROW}--${rowState}`;

  /* Status icon cell (first column, no header) */
  const statusCell = document.createElement('div');
  statusCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-status`;
  statusCell.setAttribute('aria-hidden', 'true');
  statusCell.textContent = statusIcon;

  const columnCell = document.createElement('div');
  columnCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-column`;
  columnCell.textContent = column.column_name;

  const suggestionCell = document.createElement('div');
  suggestionCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-suggestion`;

  const suggestionTarget = document.createElement('span');
  suggestionTarget.className = CSS_SUGGESTION_TARGET;

  if (topTarget) {
    suggestionTarget.textContent = topTarget.target;
  } else {
    suggestionTarget.textContent = '—';
    suggestionTarget.className = `${CSS_SUGGESTION_TARGET} ${CSS_SUGGESTION_TARGET}--empty`;
  }

  suggestionCell.appendChild(suggestionTarget);

  /* Use the Combobox widget for override selection */
  const combobox = createCombobox({
    options: manualOptions,
    initialValue: manualSelection,
    placeholder: topTarget ? 'Keep AI suggestion' : NO_MAPPING_OPTION,
    separatorAfterIndex: NO_MAPPING_INDEX,
    mutedIndices: [NO_MAPPING_INDEX],
    onChange: (newValue) => {
      const isNoMappingSelection = newValue?.toLowerCase() === NO_MAPPING_OPTION.toLowerCase();
      if (newValue && !isNoMappingSelection) {
        state.manualSelections.set(normalizedKey, newValue);
      } else {
        state.manualSelections.delete(normalizedKey);
      }
      _persistManualOverrides();
      _renderMappingRows();
    },
  });

  const overrideCell = document.createElement('div');
  overrideCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-override`;
  overrideCell.appendChild(combobox);

  row.appendChild(statusCell);
  row.appendChild(columnCell);
  row.appendChild(suggestionCell);
  row.appendChild(overrideCell);
  return row;
};

/** Render all column mapping rows sorted by recommendation availability. */
const _renderMappingRows = () => {
  if (!state.payload) {
    mappingResults.innerHTML = '';
    emptyState.classList.remove('hidden');
    emptyState.textContent = MSG_NO_ANALYSIS_DATA;
    return;
  }

  const columns = state.payload.columns ?? [];
  mappingResults.innerHTML = '';

  const sortedColumns = [...columns].sort((a, b) => {
    const aHasRecommendation = _getColumnSuggestions(a).length > 0;
    const bHasRecommendation = _getColumnSuggestions(b).length > 0;
    if (aHasRecommendation && !bHasRecommendation) return -1;
    if (!aHasRecommendation && bHasRecommendation) return 1;
    return 0;
  });

  sortedColumns.forEach((column) => {
    const row = _buildMappingRow(column);
    mappingResults.appendChild(row);
  });

  if (!mappingResults.children.length) {
    emptyState.classList.remove('hidden');
    emptyState.textContent = MSG_NO_COLUMNS;
  } else {
    emptyState.classList.add('hidden');
  }

  if (harmonizeButton) {
    harmonizeButton.disabled = !state.payload;
  }
};

/** Populate hint text and render rows based on current state. */
const _hydrateView = () => {
  if (!state.payload) {
    if (mappingHint) mappingHint.textContent = MSG_UPLOAD_HINT;
    _renderMappingRows();
    return;
  }

  _renderMappingRows();
};

/** Fetch analysis payload from backend. */
const _fetchPayload = async (fileId, targetSchema) => {
  if (!fileId) {
    return null;
  }
  const response = await fetch(config.analyzeEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      file_id: fileId,
      target_schema: targetSchema || config.targetSchema,
    }),
  });
  const payload = await response.json().catch((err) => {
    console.error('Failed to parse response JSON:', err);
    return {};
  });
  if (!response.ok) {
    throw new Error(payload.detail || 'Unable to fetch mapping data');
  }
  _savePayloadToStorage(payload);
  return payload;
};

/** Prepare and navigate to stage 3 with harmonization payload. */
const _submitHarmonize = async () => {
  if (!state.payload || state.isSubmitting) {
    return;
  }

  state.isSubmitting = true;
  harmonizeButton.disabled = true;
  if (harmonizeButtonText) harmonizeButtonText.textContent = 'Preparing…';

  const overrides = Object.fromEntries(state.manualSelections.entries());
  const manifest = state.payload?.manifest;
  if (!manifest || !manifest.column_mappings) {
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    if (mappingHint) mappingHint.textContent = MSG_MANIFEST_MISSING;
    return;
  }
  const fileId = state.payload.file_id;

  /* Validate file_id before constructing URL to prevent malformed requests. */
  if (!isValidFileId(fileId)) {
    console.error('Invalid file ID format');
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    if (mappingHint) mappingHint.textContent = MSG_INVALID_FILE;
    return;
  }

  const body = {
    file_id: fileId,
    target_schema: config.targetSchema,
    manual_overrides: overrides,
    manifest,
  };

  removeFromSession(STAGE_3_JOB_KEY);

  const payloadSaved = _persistStageThreePayload({ ...body });
  if (!payloadSaved) {
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    if (mappingHint) mappingHint.textContent = MSG_STORAGE_ERROR;
    return;
  }

  /* Validate stageThreeUrl before navigation to prevent open redirect. */
  if (!isSafeRelativeUrl(stageThreeUrl)) {
    console.error('Invalid stage three URL');
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    return;
  }

  const url = new URL(stageThreeUrl, window.location.origin);
  url.searchParams.set('file_id', fileId);
  url.searchParams.set('target_schema', config.targetSchema);

  /* Navigate immediately - isSubmitting stays true to prevent duplicate clicks. */
  advanceMaxReachedStage('harmonize');
  window.location.assign(url.toString());
};

/** Bootstrap page state from storage or backend. */
const _init = async () => {
  setActiveStage('mapping');
  initStepInstruction('mapping');
  initNavigationEvents();

  if (harmonizeButton) {
    harmonizeButton.addEventListener('click', _submitHarmonize);
  }

  let payload = _readPayloadFromStorage();
  const params = new URLSearchParams(window.location.search);
  const fileId = params.get('file_id') || payload?.file_id;
  const schema = params.get('schema') || config.targetSchema;

  if (!payload) {
    try {
      payload = await _fetchPayload(fileId, schema);
    } catch (error) {
      console.error(error);
      if (mappingHint) mappingHint.textContent = error.message;
    }
  }

  if (!payload) {
    _hydrateView();
    return;
  }

  state.payload = payload;
  const overrides = payload.manual_overrides ? Object.entries(payload.manual_overrides) : [];
  state.manualSelections = new Map(overrides.map(([key, value]) => [_normalizeColumnKey(key), value]));
  _hydrateView();
};

_init();
