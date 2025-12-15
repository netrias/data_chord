/**
 * Handle column mapping review and user overrides before harmonization.
 * Reads analysis payload from storage, renders mapping UI, and persists user selections.
 */
import { initStepInstruction } from '/assets/shared/step-instruction-ui.js';

const config = window.stageTwoConfig ?? {};
const STORAGE_KEY = config.storageKey ?? 'stage2Payload';
const manualOptions = config.manualOptions ?? [];
const stageThreeUrl = config.stageThreeUrl ?? '/stage-3';
const stageThreePayloadKey = config.stageThreePayloadKey ?? 'stage3HarmonizePayload';
const stageThreeJobKey = config.stageThreeJobKey ?? 'stage3HarmonizeJob';

const mappingResults = document.getElementById('mappingResults');
const mappingHint = document.getElementById('mappingHint');
const emptyState = document.getElementById('mappingEmptyState');
const harmonizeButton = document.getElementById('harmonizeButton');
const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');

const state = {
  payload: null,
  manualSelections: new Map(),
  isSubmitting: false,
};

const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'review', 'export'];

/** Update progress tracker UI to reflect current stage. */
const setActiveStage = (stage) => {
  const targetIndex = STAGE_ORDER.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = STAGE_ORDER.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

/** Persist payload to session storage for cross-page state. */
const _savePayloadToStorage = (payload) => {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Unable to persist stage 2 payload', error);
  }
};

/** Read payload from session storage. */
const _readPayloadFromStorage = () => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.warn('Unable to read stage 2 payload', error);
    return null;
  }
};

/** Look up CDE target suggestions for a column, checking case variants. */
const _getColumnSuggestions = (column) => {
  const targets = state.payload?.cde_targets ?? {};
  return (
    targets[column.column_name] ||
    targets[column.column_name?.toLowerCase()] ||
    targets[column.column_name?.toUpperCase()] ||
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
    sessionStorage.setItem(stageThreePayloadKey, JSON.stringify(payloadForStageThree));
    return true;
  } catch (error) {
    console.error('Unable to persist stage three payload', error);
    return false;
  }
};

/** Build and render a single mapping row for a column. */
const _buildMappingRow = (column) => {
  const row = document.createElement('article');
  row.className = `mapping-row confidence-${column.confidence_bucket}`;

  const columnCell = document.createElement('div');
  columnCell.className = 'mapping-cell';
  columnCell.innerHTML = `
    <p class="label">Column</p>
    <h3>${column.column_name}</h3>
  `;

  const suggestionCell = document.createElement('div');
  suggestionCell.className = 'mapping-cell suggestion-cell';

  const suggestionLabel = document.createElement('p');
  suggestionLabel.className = 'label';
  suggestionLabel.textContent = 'AI recommended model';
  suggestionCell.appendChild(suggestionLabel);

  const suggestions = _getColumnSuggestions(column);
  const topTarget = suggestions[0];
  const manualSelection =
    state.manualSelections.get(column.column_name) ||
    state.manualSelections.get(column.column_name.toLowerCase()) ||
    null;

  if (!topTarget) {
    row.className = 'mapping-row confidence-medium';
  }

  const suggestionTarget = document.createElement('p');
  suggestionTarget.className = 'suggestion-target';

  if (manualSelection) {
    suggestionTarget.textContent = manualSelection;
  } else if (topTarget) {
    suggestionTarget.textContent = topTarget.target;
  } else {
    suggestionTarget.textContent = 'No recommendation';
  }

  suggestionCell.appendChild(suggestionTarget);

  const select = document.createElement('select');
  select.className = 'select-control';
  select.dataset.column = column.column_name;

  if (topTarget) {
    const placeholderOption = document.createElement('option');
    placeholderOption.value = '';
    placeholderOption.textContent = 'Keep AI suggestion';
    if (!manualSelection) {
      placeholderOption.selected = true;
    }
    select.appendChild(placeholderOption);
  } else {
    const noModelOption = document.createElement('option');
    noModelOption.value = '';
    noModelOption.textContent = 'No model';
    if (!manualSelection) {
      noModelOption.selected = true;
    }
    select.appendChild(noModelOption);
  }

  manualOptions.forEach((option) => {
    const opt = document.createElement('option');
    opt.value = option;
    opt.textContent = option;
    if (manualSelection && option === manualSelection) {
      opt.selected = true;
    }
    select.appendChild(opt);
  });

  select.addEventListener('change', () => {
    if (select.value) {
      state.manualSelections.set(column.column_name, select.value);
    } else {
      state.manualSelections.delete(column.column_name);
    }
    _persistManualOverrides();
    _renderMappingRows();
  });

  suggestionCell.appendChild(select);

  if (suggestions.length > 1) {
    const list = document.createElement('ul');
    list.className = 'suggestion-list';
    suggestions.slice(1, 4).forEach((item) => {
      const chip = document.createElement('li');
      const scoreRounded = Math.round(Number(item.similarity) * 100) / 100;
      chip.textContent = `${item.target} · ${scoreRounded}`;
      list.appendChild(chip);
    });
    suggestionCell.appendChild(list);
  }

  row.appendChild(columnCell);
  row.appendChild(suggestionCell);
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
  const payload = await response.json().catch(() => ({}));
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
  const body = {
    file_id: state.payload.file_id,
    target_schema: config.targetSchema,
    manual_overrides: overrides,
    manifest,
  };

  try {
    sessionStorage.removeItem(stageThreeJobKey);
  } catch (error) {
    console.warn('Unable to reset previous harmonize job', error);
  }

  const payloadSaved = _persistStageThreePayload({ ...body });
  if (!payloadSaved) {
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    harmonizeButton.textContent = 'Harmonize';
    mappingHint.textContent = 'Unable to prepare harmonization request. Please enable browser storage and retry.';
    return;
  }

  const url = new URL(stageThreeUrl, window.location.origin);
  url.searchParams.set('file_id', state.payload.file_id);
  url.searchParams.set('target_schema', config.targetSchema);

  window.requestAnimationFrame(() => {
    state.isSubmitting = false;
    window.location.assign(url.toString());
  });
};

/** Bootstrap page state from storage or backend. */
const _initialize = async () => {
  setActiveStage('mapping');
  initStepInstruction('mapping');

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
  state.manualSelections = new Map(overrides);
  _hydrateView();
};

_initialize();
