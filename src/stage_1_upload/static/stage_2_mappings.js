const config = window.stageTwoConfig ?? {};
const STORAGE_KEY = config.storageKey ?? 'stage2Payload';
const MIN_CONFIDENCE = Number(config.minConfidence ?? 0.5);
const manualOptions = config.manualOptions ?? [];

const mappingResults = document.getElementById('mappingResults');
const mappingHint = document.getElementById('mappingHint');
const mappingFileName = document.getElementById('mappingFileName');
const mappingRowCount = document.getElementById('mappingRowCount');
const toggleLowConfidenceButton = document.getElementById('toggleLowConfidence');
const hiddenCountLabel = document.getElementById('hiddenCount');
const emptyState = document.getElementById('mappingEmptyState');
const harmonizeButton = document.getElementById('harmonizeButton');
const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');

const CONFIDENCE_LABELS = {
  low: 'Low confidence',
  medium: 'Medium confidence',
  high: 'High confidence',
};

const state = {
  payload: null,
  showLowConfidence: false,
  manualSelections: new Map(),
  isSubmitting: false,
};

const setActiveStage = (stage) => {
  const order = ['upload', 'mapping', 'harmonize', 'review', 'export'];
  const targetIndex = order.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = order.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

const savePayloadToStorage = (payload) => {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Unable to persist stage 2 payload', error);
  }
};

const readPayloadFromStorage = () => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.warn('Unable to read stage 2 payload', error);
    return null;
  }
};

const getColumnSuggestions = (column) => {
  const targets = state.payload?.cde_targets ?? {};
  return (
    targets[column.column_name] ||
    targets[column.column_name?.toLowerCase()] ||
    targets[column.column_name?.toUpperCase()] ||
    []
  );
};

const topSuggestionScore = (column) => {
  const suggestions = getColumnSuggestions(column);
  if (suggestions.length) {
    const value = Number(suggestions[0].similarity ?? 0);
    return Number.isNaN(value) ? 0 : value;
  }
  const fallback = Number(column.confidence_score ?? 0);
  return Number.isNaN(fallback) ? 0 : fallback;
};

const persistManualOverrides = () => {
  if (!state.payload) {
    return;
  }
  const overrides = Object.fromEntries(state.manualSelections.entries());
  const nextPayload = { ...state.payload, manual_overrides: overrides };
  state.payload = nextPayload;
  savePayloadToStorage(nextPayload);
  const toggle = document.getElementById('toggleLowConfidence');
  if (toggle) {
    toggle.textContent = state.showLowConfidence ? 'Hide low confidence' : 'Show low confidence';
  }
};

const renderMappingRows = () => {
  if (!state.payload) {
    mappingResults.innerHTML = '';
    emptyState.classList.remove('hidden');
    emptyState.textContent = 'No analysis data found. Upload a file on Stage 1 to begin.';
    return;
  }

  const columns = state.payload.columns ?? [];
  mappingResults.innerHTML = '';

  let hiddenCount = 0;

  columns.forEach((column) => {
    const score = topSuggestionScore(column);
    const shouldHide = !state.showLowConfidence && score < MIN_CONFIDENCE;
    if (shouldHide) {
      hiddenCount += 1;
      return;
    }

    const row = document.createElement('article');
    row.className = `mapping-row confidence-${column.confidence_bucket}`;

    const columnCell = document.createElement('div');
    columnCell.className = 'mapping-cell';
    columnCell.innerHTML = `
      <p class="label">Column</p>
      <h3>${column.column_name}</h3>
      <p class="meta">Detected ${column.inferred_type}</p>
    `;
    const badge = document.createElement('span');
    badge.className = `confidence-badge ${column.confidence_bucket}`;
    badge.textContent = CONFIDENCE_LABELS[column.confidence_bucket] || 'Confidence';
    columnCell.appendChild(badge);

    const suggestionCell = document.createElement('div');
    suggestionCell.className = 'mapping-cell suggestion-cell';

    const suggestionLabel = document.createElement('p');
    suggestionLabel.className = 'label';
    suggestionLabel.textContent = 'AI target';
    suggestionCell.appendChild(suggestionLabel);

  const suggestions = getColumnSuggestions(column);
  const topTarget = suggestions[0];
  const manualSelection =
    state.manualSelections.get(column.column_name) ||
    state.manualSelections.get(column.column_name.toLowerCase()) ||
    null;

    const suggestionTarget = document.createElement('p');
    suggestionTarget.className = 'suggestion-target';
    const suggestionScore = document.createElement('p');
    suggestionScore.className = 'suggestion-score';

    if (manualSelection) {
      suggestionTarget.textContent = manualSelection;
      suggestionScore.textContent = 'Mapped via Netrias CDE ID';
    } else if (topTarget) {
      const topScoreRounded = Math.round(Number(topTarget.similarity) * 100) / 100;
      suggestionTarget.textContent = topTarget.target;
      suggestionScore.textContent = `Confidence score ${topScoreRounded}`;
    } else {
      suggestionTarget.textContent = 'No recommendation yet';
      suggestionScore.textContent = 'Upload a richer sample to improve suggestions.';
    }

    suggestionCell.appendChild(suggestionTarget);
    suggestionCell.appendChild(suggestionScore);

    const selectLabel = document.createElement('label');
    selectLabel.className = 'label select-label';
    selectLabel.textContent = 'Manual override';
    const select = document.createElement('select');
    select.className = 'select-control';
    select.dataset.column = column.column_name;

    const placeholderOption = document.createElement('option');
    placeholderOption.value = '';
    placeholderOption.textContent = 'Keep AI suggestion';
    select.appendChild(placeholderOption);

    manualOptions.forEach((option) => {
      const opt = document.createElement('option');
      opt.value = option;
      opt.textContent = option;
      select.appendChild(opt);
    });

    const defaultValue = manualSelection || topTarget?.target || '';
    select.value = defaultValue;
    select.addEventListener('change', () => {
      if (select.value) {
        state.manualSelections.set(column.column_name, select.value);
      } else {
        state.manualSelections.delete(column.column_name);
      }
      persistManualOverrides();
      renderMappingRows();
    });

    selectLabel.appendChild(select);
    suggestionCell.appendChild(selectLabel);

    if (manualSelection) {
      const manualPill = document.createElement('p');
      manualPill.className = 'manual-pill';
      manualPill.textContent = `Manual: ${manualSelection}`;
      suggestionCell.appendChild(manualPill);
    }

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

    const sampleCell = document.createElement('div');
    sampleCell.className = 'mapping-cell';
    const sampleLabel = document.createElement('p');
    sampleLabel.className = 'label';
    sampleLabel.textContent = 'Sample values';
    sampleCell.appendChild(sampleLabel);

    const sampleList = document.createElement('ul');
    sampleList.className = 'sample-chips';
    (column.sample_values || []).slice(0, 5).forEach((value) => {
      const chip = document.createElement('li');
      chip.className = 'sample-chip';
      chip.textContent = value || '—';
      sampleList.appendChild(chip);
    });
    sampleCell.appendChild(sampleList);

    row.appendChild(columnCell);
    row.appendChild(suggestionCell);
    row.appendChild(sampleCell);
    mappingResults.appendChild(row);
  });

  if (!mappingResults.children.length) {
    emptyState.classList.remove('hidden');
    emptyState.textContent = 'All visible columns are below the confidence threshold.';
  } else {
    emptyState.classList.add('hidden');
  }

  hiddenCountLabel.textContent = hiddenCount
    ? `${hiddenCount} column${hiddenCount === 1 ? '' : 's'} hidden (< ${MIN_CONFIDENCE})`
    : '';
  toggleLowConfidenceButton.textContent = state.showLowConfidence ? 'Hide low confidence' : 'Show low confidence';
  if (harmonizeButton) {
    harmonizeButton.disabled = !state.payload;
  }
};

const hydrateView = () => {
  if (!state.payload) {
    mappingHint.textContent = 'Upload a file on Stage 1 to get started.';
    mappingFileName.textContent = '—';
    mappingRowCount.textContent = '—';
    renderMappingRows();
    return;
  }

  mappingHint.textContent = state.payload.next_step_hint || 'Review AI suggestions before proceeding.';
  mappingFileName.textContent = state.payload.file_name || '—';
  mappingRowCount.textContent = state.payload.total_rows?.toLocaleString?.() ?? String(state.payload.total_rows ?? '—');
  renderMappingRows();
};

const fetchPayload = async (fileId, targetSchema) => {
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
  savePayloadToStorage(payload);
  return payload;
};

const submitHarmonize = async () => {
  if (!state.payload || state.isSubmitting) {
    return;
  }

  state.isSubmitting = true;
  harmonizeButton.disabled = true;
  harmonizeButton.textContent = 'Harmonizing…';

  const overrides = Object.fromEntries(state.manualSelections.entries());
  const body = {
    file_id: state.payload.file_id,
    target_schema: config.targetSchema,
    manual_overrides: overrides,
  };

  try {
    const response = await fetch('/stage-2/harmonize', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || 'Harmonization failed.');
    }
    window.location.assign(payload.next_stage_url);
  } catch (error) {
    console.error(error);
    harmonizeButton.disabled = false;
    harmonizeButton.textContent = 'Harmonize';
    mappingHint.textContent = error.message;
  } finally {
    state.isSubmitting = false;
  }
};

const initialize = async () => {
  setActiveStage('mapping');
  toggleLowConfidenceButton.addEventListener('click', () => {
    state.showLowConfidence = !state.showLowConfidence;
    renderMappingRows();
  });

  if (harmonizeButton) {
    harmonizeButton.addEventListener('click', submitHarmonize);
  }

  let payload = readPayloadFromStorage();
  const params = new URLSearchParams(window.location.search);
  const fileId = params.get('file_id') || payload?.file_id;
  const schema = params.get('schema') || config.targetSchema;

  if (!payload) {
    try {
      payload = await fetchPayload(fileId, schema);
    } catch (error) {
      console.error(error);
      mappingHint.textContent = error.message;
    }
  }

  if (!payload) {
    hydrateView();
    return;
  }

  state.payload = payload;
  const overrides = payload.manual_overrides ? Object.entries(payload.manual_overrides) : [];
  state.manualSelections = new Map(overrides);
  hydrateView();
};

initialize();
