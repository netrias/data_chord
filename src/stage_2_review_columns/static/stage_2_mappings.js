/**
 * Handle column mapping review and user overrides before harmonization.
 * Reads analysis payload from storage, renders mapping UI, and persists user selections.
 */
import { initStepInstruction, setActiveStage, initNavigationEvents, isSafeRelativeUrl, advanceMaxReachedStage, updateStepInstruction } from '/assets/shared/step-instruction-ui.js';
import { STAGE_2_PAYLOAD_KEY, STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, COLUMN_PREVIEW_KEY, isValidFileId, removeFromSession, readFromSession, writeToSession } from '/assets/shared/storage-keys.js';
import { createCombobox } from '/assets/shared/combobox.js';
import { determineRowState } from '/assets/shared/row-state.js';

/* Animation timing constants */
const ANIMATION_DURATION_MS = 5000;
const MAX_DISPLAY_COLUMNS = 8;

/* Configuration and constants */
const config = window.stageTwoConfig ?? {};
const HARMONIZE_BUTTON_LABEL = 'Harmonize →';
const NO_MAPPING_OPTION = config.noMappingLabel ?? 'No Mapping';
const stageThreeUrl = config.stageThreeUrl ?? '/stage-3';

/* why: index 0 is the 'No Mapping' option, visually separated from CDE options. */
const NO_MAPPING_INDEX = 0;
const manualOptions = [NO_MAPPING_OPTION, ...(config.manualOptions ?? [])];

/* CSS class constants */
const CSS_MAPPING_ROW = 'mapping-row';
const CSS_MAPPING_TD = 'mapping-td';
const CSS_SUGGESTION_TARGET = 'suggestion-target';

/* User-facing messages */
const MSG_NO_ANALYSIS_DATA = 'No analysis data found. Upload a file on Stage 1 to begin.';
const MSG_NO_COLUMNS = 'No columns to display.';
const MSG_MANIFEST_MISSING = 'Manifest missing. Please rerun analysis before harmonizing.';
const MSG_INVALID_FILE = 'Invalid file reference. Please restart the upload process.';
const MSG_STORAGE_ERROR = 'Unable to prepare harmonization request. Please enable browser storage and retry.';

const mappingResults = document.getElementById('mappingResults');
const emptyState = document.getElementById('mappingEmptyState');
const harmonizeButton = document.getElementById('harmonizeButton');
const harmonizeButtonText = harmonizeButton?.querySelector('.btn-3d-front');
const transformContainer = document.getElementById('transformContainer');
const analyzeOverlay = document.getElementById('analyzeOverlay');

const state = {
  payload: null,
  preview: null,
  manualSelections: new Map(),
  isSubmitting: false,
  isAnalyzing: false,
  animationComplete: false,
};

/* why: use shared utilities for consistent session storage handling. */
const _savePayloadToStorage = (payload) => {
  writeToSession(STAGE_2_PAYLOAD_KEY, payload);
};

const _readPayloadFromStorage = () => {
  return readFromSession(STAGE_2_PAYLOAD_KEY);
};

const _readPreviewFromStorage = () => {
  return readFromSession(COLUMN_PREVIEW_KEY);
};

/* Animation functions for CSV grid transformation */

/** Build the horizontal column bar showing column names like CSV headers */
const _buildColumnBar = (preview) => {
  if (!preview?.columns?.length) {
    return null;
  }

  const columns = preview.columns.slice(0, MAX_DISPLAY_COLUMNS);
  const bar = document.createElement('div');
  bar.className = 'column-bar';

  columns.forEach((col, idx) => {
    const chip = document.createElement('div');
    chip.className = 'column-chip';
    chip.style.setProperty('--chip-index', idx);
    chip.textContent = col.column_name;
    chip.title = col.column_name;
    bar.appendChild(chip);
  });

  return bar;
};

/** Return a promise that resolves after the animation duration */
const _playTransformAnimation = () => {
  return new Promise((resolve) => {
    setTimeout(resolve, ANIMATION_DURATION_MS);
  });
};

/** Show/hide the analyze overlay */
const _showOverlay = (show) => {
  if (!analyzeOverlay) return;
  analyzeOverlay.classList.toggle('hidden', !show);
};

/** Build a placeholder row for animation using only column name (no AI data yet) */
const _buildPlaceholderRow = (columnName, rowIndex) => {
  const row = document.createElement('div');
  row.className = `${CSS_MAPPING_ROW} ${CSS_MAPPING_ROW}--no-mapping`;
  row.dataset.columnName = columnName.toLowerCase();
  row.style.setProperty('--row-index', rowIndex);

  /* Status icon cell - shows empty state initially */
  const statusCell = document.createElement('div');
  statusCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-status mapping-td-status--loading`;
  statusCell.setAttribute('aria-hidden', 'true');
  statusCell.textContent = '○';

  /* Column name cell */
  const columnCell = document.createElement('div');
  columnCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-column`;
  columnCell.textContent = columnName;

  /* AI suggestion cell - placeholder */
  const suggestionCell = document.createElement('div');
  suggestionCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-suggestion mapping-td-suggestion--loading`;
  const suggestionTarget = document.createElement('span');
  suggestionTarget.className = `${CSS_SUGGESTION_TARGET} ${CSS_SUGGESTION_TARGET}--empty`;
  suggestionTarget.textContent = '...';
  suggestionCell.appendChild(suggestionTarget);

  /* Override cell - placeholder with same size as combobox to prevent layout shift */
  const overrideCell = document.createElement('div');
  overrideCell.className = `${CSS_MAPPING_TD} ${CSS_MAPPING_TD}-override mapping-td-override--loading`;
  const placeholder = document.createElement('div');
  placeholder.style.height = '36px';
  placeholder.style.minWidth = '180px';
  overrideCell.appendChild(placeholder);

  row.appendChild(statusCell);
  row.appendChild(columnCell);
  row.appendChild(suggestionCell);
  row.appendChild(overrideCell);

  return row;
};

/** Update existing placeholder rows with API data instead of full re-render */
const _updateRowsWithApiData = () => {
  const columns = state.payload?.columns ?? [];

  columns.forEach((column) => {
    const normalizedKey = _normalizeColumnKey(column.column_name);
    const row = mappingResults.querySelector(`[data-column-name="${normalizedKey}"]`);
    if (!row) return;

    const suggestions = _getColumnSuggestions(column);
    const topTarget = suggestions[0];
    const manualSelection = state.manualSelections.get(normalizedKey) ?? null;

    /* Update row state class */
    const aiRecommendation = topTarget?.target ?? null;
    const { state: rowState, icon: statusIcon } = determineRowState({
      aiRecommendation,
      userSelection: manualSelection,
      noMappingValue: NO_MAPPING_OPTION,
    });
    row.className = `${CSS_MAPPING_ROW} ${CSS_MAPPING_ROW}--${rowState}`;

    /* Update status icon */
    const statusCell = row.querySelector(`.${CSS_MAPPING_TD}-status`);
    if (statusCell) {
      statusCell.textContent = statusIcon;
      statusCell.classList.remove('mapping-td-status--loading');
    }

    /* Update suggestion */
    const suggestionCell = row.querySelector(`.${CSS_MAPPING_TD}-suggestion`);
    if (suggestionCell) {
      const target = suggestionCell.querySelector(`.${CSS_SUGGESTION_TARGET}`);
      if (target) {
        target.textContent = topTarget?.target ?? '—';
        if (topTarget) {
          target.classList.remove(`${CSS_SUGGESTION_TARGET}--empty`);
        } else {
          target.classList.add(`${CSS_SUGGESTION_TARGET}--empty`);
        }
      }
      suggestionCell.classList.remove('mapping-td-suggestion--loading');
      suggestionCell.classList.add('mapping-td-suggestion--ready');
    }

    /* Replace override placeholder with combobox */
    const overrideCell = row.querySelector(`.${CSS_MAPPING_TD}-override`);
    if (overrideCell) {
      overrideCell.innerHTML = '';
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
      overrideCell.appendChild(combobox);
      overrideCell.classList.remove('mapping-td-override--loading');
      overrideCell.classList.add('mapping-td-override--ready');
    }
  });

  /* Enable harmonize button */
  _renderMappingRows();
};

/** Calculate transform offsets to move chips to their row positions */
const _calculateChipTargets = (chips, rows) => {
  chips.forEach((chip, idx) => {
    const row = rows[idx];
    if (!row) return;

    /* Find the column name cell in the row */
    const columnCell = row.querySelector(`.${CSS_MAPPING_TD}-column`);
    if (!columnCell) return;

    /* Get positions */
    const chipRect = chip.getBoundingClientRect();
    const cellRect = columnCell.getBoundingClientRect();

    /* Calculate how far the chip needs to move */
    const deltaX = cellRect.left - chipRect.left;
    const deltaY = cellRect.top - chipRect.top;

    /* Set CSS custom properties for the animation */
    chip.style.setProperty('--target-x', `${deltaX}px`);
    chip.style.setProperty('--target-y', `${deltaY}px`);
  });
};

/** Start the animation immediately with preview data */
const _startAnimationWithPreview = (preview) => {
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (!preview?.columns?.length) {
    return;
  }

  const columns = preview.columns.slice(0, MAX_DISPLAY_COLUMNS);
  mappingResults.innerHTML = '';
  emptyState.classList.add('hidden');

  /* Build placeholder rows first (hidden initially for animation) */
  columns.forEach((col, idx) => {
    const row = _buildPlaceholderRow(col.column_name, idx);
    if (!prefersReducedMotion) {
      row.classList.add('mapping-row--slide-target');
    }
    mappingResults.appendChild(row);
  });

  /* Show column bar with chips */
  if (!prefersReducedMotion && transformContainer) {
    const bar = _buildColumnBar(preview);
    if (bar) {
      transformContainer.innerHTML = '';
      transformContainer.appendChild(bar);
      transformContainer.classList.remove('hidden');
    }
  }

  /* Trigger the slide animation */
  if (!prefersReducedMotion && transformContainer) {
    /* Wait for layout to settle, then calculate target positions */
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const chips = transformContainer.querySelectorAll('.column-chip');
        const rows = mappingResults.querySelectorAll('.mapping-row--slide-target');

        /* Make rows visible briefly to measure positions, then hide again */
        rows.forEach((row) => row.style.visibility = 'hidden');
        rows.forEach((row) => row.style.opacity = '1');

        _calculateChipTargets(chips, rows);

        /* Hide rows again for animation */
        rows.forEach((row) => {
          row.style.opacity = '';
          row.style.visibility = '';
        });

        /* Start the animation */
        setTimeout(() => {
          const bar = transformContainer.querySelector('.column-bar');
          if (bar) {
            bar.classList.add('column-bar--transforming');
          }

          /* After chips start moving, reveal the rows */
          setTimeout(() => {
            rows.forEach((row) => {
              row.classList.add('mapping-row--slide-active');
            });

            /* Clean up the column bar after animation */
            setTimeout(() => {
              transformContainer.classList.add('hidden');
              transformContainer.innerHTML = '';
            }, ANIMATION_DURATION_MS);
          }, 500);
        }, 100);
      });
    });
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
    console.error(MSG_MANIFEST_MISSING);
    return;
  }
  const fileId = state.payload.file_id;

  /* Validate file_id before constructing URL to prevent malformed requests. */
  if (!isValidFileId(fileId)) {
    console.error(MSG_INVALID_FILE);
    state.isSubmitting = false;
    harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
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
    console.error(MSG_STORAGE_ERROR);
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

/** Bootstrap page state from storage or backend with animation. */
const _init = async () => {
  setActiveStage('mapping');
  initStepInstruction('mapping');
  initNavigationEvents();

  if (harmonizeButton) {
    harmonizeButton.addEventListener('click', _submitHarmonize);
    harmonizeButton.disabled = true;
  }

  const params = new URLSearchParams(window.location.search);
  const fileId = params.get('file_id');
  const schema = params.get('schema') || config.targetSchema;

  /* Check if we already have a full payload (returning to page) */
  const existingPayload = _readPayloadFromStorage();
  if (existingPayload?.file_id === fileId && existingPayload?.cde_targets) {
    /* Already have full data - skip animation, render immediately */
    state.payload = existingPayload;
    const overrides = existingPayload.manual_overrides
      ? Object.entries(existingPayload.manual_overrides)
      : [];
    state.manualSelections = new Map(
      overrides.map(([key, value]) => [_normalizeColumnKey(key), value])
    );
    _renderMappingRows();
    return;
  }

  /* Fresh navigation from Stage 1 - play animation and fetch mappings */
  const preview = _readPreviewFromStorage();
  state.preview = preview;

  if (!fileId) {
    _renderMappingRows();
    return;
  }

  /* Show loading indicator in step instruction */
  updateStepInstruction('mapping_loading');

  /* Start animation IMMEDIATELY with preview data */
  if (preview?.columns?.length) {
    _startAnimationWithPreview(preview);
  }

  /* Start animation timer and API call in parallel */
  let apiComplete = false;

  const animationPromise = _playTransformAnimation();

  const fetchPromise = (async () => {
    state.isAnalyzing = true;
    try {
      const payload = await _fetchPayload(fileId, schema);
      return payload;
    } catch (error) {
      console.error('Failed to fetch mappings:', error);
      updateStepInstruction('mapping');
      return null;
    } finally {
      apiComplete = true;
      state.isAnalyzing = false;
    }
  })();

  /* Wait for animation to complete first */
  await animationPromise;
  state.animationComplete = true;

  /* If API isn't done yet, show the overlay */
  if (!apiComplete) {
    _showOverlay(true);
  }

  /* Now wait for API if it's still running */
  const payload = await fetchPromise;

  /* Hide overlay */
  _showOverlay(false);

  if (!payload) {
    _renderMappingRows();
    return;
  }

  state.payload = payload;
  const overrides = payload.manual_overrides ? Object.entries(payload.manual_overrides) : [];
  state.manualSelections = new Map(
    overrides.map(([key, value]) => [_normalizeColumnKey(key), value])
  );

  /* Update rows in-place if animation rows exist, otherwise full re-render */
  const hasAnimationRows = mappingResults.querySelector('[data-column-name]');
  if (hasAnimationRows) {
    _updateRowsWithApiData();
  } else {
    _renderMappingRows();
  }

  /* Restore normal step instruction */
  updateStepInstruction('mapping');
};

_init().catch(console.error);
