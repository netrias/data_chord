/**
 * Handle column mapping review and user overrides before harmonization.
 * Reads analysis payload from storage, renders mapping UI, and persists user selections.
 */
import { initStepInstruction, setActiveStage, initNavigationEvents, isSafeRelativeUrl } from '/assets/shared/step-instruction-ui.js';
import { STAGE_2_PAYLOAD_KEY, STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, isValidFileId, removeFromSession } from '/assets/shared/storage-keys.js';
import { createCombobox } from '/assets/shared/combobox.js';

const config = window.stageTwoConfig ?? {};
const NO_MAPPING_OPTION = 'No mapping';
const manualOptions = [NO_MAPPING_OPTION, ...(config.manualOptions ?? [])];
const stageThreeUrl = config.stageThreeUrl ?? '/stage-3';

const mappingResults = document.getElementById('mappingResults');
const mappingHint = document.getElementById('mappingHint');
const emptyState = document.getElementById('mappingEmptyState');
const harmonizeButton = document.getElementById('harmonizeButton');
const state = {
  payload: null,
  manualSelections: new Map(),
  isSubmitting: false,
};

/** Persist payload to session storage for cross-page state. */
const _savePayloadToStorage = (payload) => {
  try {
    sessionStorage.setItem(STAGE_2_PAYLOAD_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Unable to persist stage 2 payload', error);
  }
};

/** Read payload from session storage. */
const _readPayloadFromStorage = () => {
  try {
    const raw = sessionStorage.getItem(STAGE_2_PAYLOAD_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.warn('Unable to read stage 2 payload', error);
    return null;
  }
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
  try {
    sessionStorage.setItem(STAGE_3_PAYLOAD_KEY, JSON.stringify(payloadForStageThree));
    return true;
  } catch (error) {
    console.error('Unable to persist stage three payload', error);
    return false;
  }
};

/** Determine row state and icon based on AI recommendation and user selection. */
const _determineRowState = (hasRecommendation, aiRecommendation, manualSelection) => {
  const isNoMapping = manualSelection === NO_MAPPING_OPTION;
  const isOverrideDifferentFromAI =
    manualSelection &&
    !isNoMapping &&
    manualSelection.toLowerCase() !== (aiRecommendation ?? '').toLowerCase();

  if (isNoMapping) {
    return { state: 'no-mapping', icon: '—' };
  }
  if (isOverrideDifferentFromAI) {
    return { state: 'override', icon: '✎' };
  }
  if (hasRecommendation || manualSelection) {
    return { state: 'recommended', icon: '✓' };
  }
  return { state: 'no-recommendation', icon: '○' };
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
  const hasRecommendation = Boolean(topTarget);
  const aiRecommendation = topTarget?.target ?? null;
  const { state: rowState, icon: statusIcon } = _determineRowState(
    hasRecommendation,
    aiRecommendation,
    manualSelection
  );
  row.className = `mapping-row mapping-row--${rowState}`;

  /* Status icon cell (first column, no header) */
  const statusCell = document.createElement('div');
  statusCell.className = 'mapping-td mapping-td-status';
  statusCell.setAttribute('aria-hidden', 'true');
  statusCell.textContent = statusIcon;

  const columnCell = document.createElement('div');
  columnCell.className = 'mapping-td mapping-td-column';
  columnCell.textContent = column.column_name;

  const suggestionCell = document.createElement('div');
  suggestionCell.className = 'mapping-td mapping-td-suggestion';

  const suggestionTarget = document.createElement('span');
  suggestionTarget.className = 'suggestion-target';

  if (topTarget) {
    suggestionTarget.textContent = topTarget.target;
  } else {
    suggestionTarget.textContent = '—';
    suggestionTarget.className = 'suggestion-target suggestion-target--empty';
  }

  suggestionCell.appendChild(suggestionTarget);

  /* Use the Combobox widget for override selection */
  const combobox = createCombobox({
    options: manualOptions,
    initialValue: manualSelection,
    placeholder: topTarget ? 'Keep AI suggestion' : NO_MAPPING_OPTION,
    separatorAfterIndex: 0,
    mutedIndices: [0],
    onChange: (newValue) => {
      if (newValue) {
        state.manualSelections.set(normalizedKey, newValue);
      } else {
        state.manualSelections.delete(normalizedKey);
      }
      _persistManualOverrides();
      _renderMappingRows();
    },
  });

  const overrideCell = document.createElement('div');
  overrideCell.className = 'mapping-td mapping-td-override';
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
    emptyState.textContent = 'No analysis data found. Upload a file on Stage 1 to begin.';
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
    emptyState.textContent = 'No columns to display.';
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
    mappingHint.textContent = 'Upload a file on Stage 1 to get started.';
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
  harmonizeButton.textContent = 'Preparing harmonize...';

  const overrides = Object.fromEntries(state.manualSelections.entries());
  const manifest = state.payload?.manifest;
  if (!manifest || !manifest.column_mappings) {
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    harmonizeButton.textContent = 'Harmonize';
    mappingHint.textContent = 'Manifest missing. Please rerun analysis before harmonizing.';
    return;
  }
  const fileId = state.payload.file_id;

  /* Validate file_id before constructing URL to prevent malformed requests. */
  if (!isValidFileId(fileId)) {
    console.error('Invalid file ID format');
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    harmonizeButton.textContent = 'Harmonize';
    mappingHint.textContent = 'Invalid file reference. Please restart the upload process.';
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
    harmonizeButton.textContent = 'Harmonize';
    mappingHint.textContent = 'Unable to prepare harmonization request. Please enable browser storage and retry.';
    return;
  }

  /* Validate stageThreeUrl before navigation to prevent open redirect. */
  if (!isSafeRelativeUrl(stageThreeUrl)) {
    console.error('Invalid stage three URL');
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    harmonizeButton.textContent = 'Harmonize';
    return;
  }

  const url = new URL(stageThreeUrl, window.location.origin);
  url.searchParams.set('file_id', fileId);
  url.searchParams.set('target_schema', config.targetSchema);

  /* Navigate immediately - isSubmitting stays true to prevent duplicate clicks. */
  window.location.assign(url.toString());
};

/** Bootstrap page state from storage or backend. */
const init = async () => {
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
      mappingHint.textContent = error.message;
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

init();
