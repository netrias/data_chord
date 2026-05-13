/**
 * Stage 2 — column-mapping list with row-click takeover.
 *
 * The base view is a simple list of columns; clicking any row opens a
 * takeover that shows the column's distinct values on the left and the
 * target standard on the right. Adapted from mocks/stage-2/takeover.html.
 */
import {
  initStepInstruction,
  setActiveStage,
  initNavigationEvents,
  isSafeRelativeUrl,
  advanceMaxReachedStage,
} from '/assets/shared/step-instruction-ui.js';
import {
  STAGE_2_PAYLOAD_KEY,
  STAGE_3_PAYLOAD_KEY,
  STAGE_3_JOB_KEY,
  isValidFileId,
  removeFromSession,
  readFromSession,
  writeToSession,
} from '/assets/shared/storage-keys.js';

/* ─── Configuration ──────────────────────────────────────── */
const config = window.stageTwoConfig ?? {};
const HARMONIZE_BUTTON_LABEL = 'Harmonize →';
const NO_MAPPING_OPTION = config.noMappingLabel ?? 'No Mapping';
const NO_MAP = null;
const NO_MAP_OPTION_VALUE = '__none__';
const stageThreeUrl = config.stageThreeUrl ?? '/stage-3';
const columnDetailBase = config.columnDetailBase ?? '/stage-2/column-detail';
const targetVersionNumber = config.targetVersionNumber ?? null;

const cdeCatalog = (config.cdeCatalog ?? []).map((c) => ({
  key: c.cde_key,
  label: c.label ?? c.cde_key,
  description: c.description ?? '',
  type: c.cde_type ?? 'pv',
}));
const cdeByKey = new Map(cdeCatalog.map((c) => [c.key, c]));

/* ─── Constants ──────────────────────────────────────────── */
const MAPPING_KIND = { VALUE_MAPPING: 'value_mapping', RENAME_ONLY: 'rename_only', UNMAPPED: 'unmapped' };

const OUTCOME = { REWRITE: 'rewrite', PASSTHROUGH: 'passthrough', UNCHANGED: 'unchanged' };
const OUTCOME_ICON = { [OUTCOME.REWRITE]: '✎', [OUTCOME.PASSTHROUGH]: '→', [OUTCOME.UNCHANGED]: '—' };
const OUTCOME_CLASS = {
  [OUTCOME.REWRITE]: 'mapping-ico--rewrite',
  [OUTCOME.PASSTHROUGH]: 'mapping-ico--passthrough',
  [OUTCOME.UNCHANGED]: 'mapping-ico--unchanged',
};
const OUTCOME_TIP = {
  [OUTCOME.REWRITE]: 'Values will be rewritten to match the standard',
  [OUTCOME.PASSTHROUGH]: 'No permissible values — data will not be changed',
  [OUTCOME.UNCHANGED]: 'Unmapped — column will be skipped',
};
const OUTCOME_DESC = {
  [OUTCOME.REWRITE]: 'Mapped to a standard with permissible values — data will be harmonized during processing.',
  [OUTCOME.PASSTHROUGH]: 'Mapped to a standard, but the standard has no enumerated permissible values, so your values will stay the same.',
  [OUTCOME.UNCHANGED]: 'No target standard selected. Column will pass through without any changes.',
};
const MATCH_TIP = "Distinct values in your column that exactly match a permissible value of this CDE.";
const PASSTHROUGH_FIT_TIP = "This standard has no permissible value list — values will pass through unchanged.";
const PASSTHROUGH_GLYPH = '→';
const NO_MAP_DESC = "Skip this column. Values will not be harmonized to any standard.";
const VALUE_MAPPING_SECTION_LABEL = 'Harmonize values';
const RENAME_ONLY_SECTION_LABEL = 'No value harmonization';

/* User-facing messages */
const MSG_NO_ANALYSIS_DATA = 'No analysis data found. Upload a file on Stage 1 to begin.';
const MSG_NO_COLUMNS = 'No columns to display.';
const MSG_MANIFEST_MISSING = 'Manifest missing. Please rerun analysis before harmonizing.';
const MSG_INVALID_FILE = 'Invalid file reference. Please restart the upload process.';
const MSG_STORAGE_ERROR = 'Unable to prepare harmonization request. Please enable browser storage and retry.';

/* ─── DOM ────────────────────────────────────────────────── */
const sourceFilterEl = document.getElementById('sourceFilter');
const colSearchEl = document.getElementById('colSearch');
const rowsEl = document.getElementById('mappingRows');
const emptyState = document.getElementById('mappingEmptyState');
const harmonizeButton = document.getElementById('harmonizeButton');
const harmonizeButtonText = harmonizeButton?.querySelector('.btn-3d-front');
const takeoverEl = document.getElementById('takeover');
const takeoverCardEl = document.getElementById('takeoverCard');

/* ─── State ──────────────────────────────────────────────── */
const SORT = {
  FILE: 'file', FILE_REV: 'file-reverse',
  TARGET_ASC: 'target-asc', TARGET_DESC: 'target-desc',
  FIT_ASC: 'fit-asc', FIT_DESC: 'fit-desc',
};

const state = {
  payload: null,
  filters: {
    outcomes: new Set([OUTCOME.REWRITE, OUTCOME.PASSTHROUGH, OUTCOME.UNCHANGED]),
    showEmpty: false,
  },
  filterSidebarOpen: false,
  filterText: '',
  overrides: new Map(),
  takeoverKey: null,
  pickerOpen: false,
  detailByColumn: new Map(),
  isSubmitting: false,
  activeSort: SORT.FILE,
  seenColumns: new Set(),
  renameDefault: false,
  renameOverrides: new Map(),
  renameTargets: new Map(),
  renamePickerOpen: false,
};

/* ─── Storage helpers ────────────────────────────────────── */
const _savePayloadToStorage = (payload) => writeToSession(STAGE_2_PAYLOAD_KEY, payload);
const _readPayloadFromStorage = () => readFromSession(STAGE_2_PAYLOAD_KEY);

const _persistReviewChoices = () => {
  if (!state.payload) return;
  state.payload = {
    ...state.payload,
    manual_overrides: _manualOverridesPayload(),
    column_renames: _columnRenamesPayload(),
  };
  _savePayloadToStorage(state.payload);
};

/* ─── Domain helpers ─────────────────────────────────────── */
const _columnKey = (column) => column.column_key ?? column.column_name;
const _columnLabel = (column) => column.header ?? column.column_name;

const _columnSuggestions = (column) => {
  const key = _columnKey(column);
  const targets = state.payload?.cde_targets ?? {};
  const raw = targets[key] || [];
  return raw.filter((s) => cdeByKey.has(s.target));
};

const _isNoMapValue = (value) => value === NO_MAP || value === NO_MAPPING_OPTION || value === NO_MAP_OPTION_VALUE;

const _normalizeOverrideValue = (value) => (_isNoMapValue(value) ? NO_MAP : value);

const _isRenameOnly = (cdeType) => cdeType !== 'pv';

// Ordered list of AI-recommended CDE keys for a column (top first), filtered
// to the catalog the frontend knows about. The picker fans this list out as
// individual ✦ AI rec rows so users can see every candidate the model returned.
const _aiCdeKeys = (column) => _columnSuggestions(column).map((s) => s.target);

// The single CDE that auto-populates the row's "Target standard" cell when
// the user has not set an override — i.e., the implicit applied default.
const _topAiCdeKey = (column) => _aiCdeKeys(column)[0] ?? null;

const _effectiveCde = (column) => {
  const colKey = _columnKey(column);
  if (state.overrides.has(colKey)) {
    const v = state.overrides.get(colKey);
    if (_isNoMapValue(v) || !cdeByKey.has(v)) return null;
    return v;
  }
  return _topAiCdeKey(column);
};

const _effectiveOutcome = (column) => {
  const cde = _effectiveCde(column);
  if (!cde) return OUTCOME.UNCHANGED;
  const meta = cdeByKey.get(cde);
  if (!meta) return OUTCOME.UNCHANGED;
  return meta.type === 'pv' ? OUTCOME.REWRITE : OUTCOME.PASSTHROUGH;
};

const _mappingKindFor = (column) => {
  const cde = _effectiveCde(column);
  if (!cde) return MAPPING_KIND.UNMAPPED;
  const meta = cdeByKey.get(cde);
  if (!meta) return MAPPING_KIND.UNMAPPED;
  return _isRenameOnly(meta.type) ? MAPPING_KIND.RENAME_ONLY : MAPPING_KIND.VALUE_MAPPING;
};

// Profiles are loaded lazily (column_profiles is empty in the initial payload),
// so fall back to sample_values from the column entry, then to detail data.
const _hasValues = (column) => {
  const colKey = _columnKey(column);
  const detail = state.detailByColumn.get(colKey);
  if (detail?.profile) {
    return (detail.profile.distinct_values ?? []).length > 0;
  }
  const profile = state.payload?.column_profiles?.[colKey];
  if (profile) {
    return (profile.total_distinct ?? profile.distinct_values?.length ?? 0) > 0;
  }
  return (column.sample_values ?? []).some((v) => v !== '' && v != null);
};

const _passesFilters = (column) => {
  if (!state.filters.outcomes.has(_effectiveOutcome(column))) return false;
  if (!state.filters.showEmpty && !_hasValues(column)) return false;
  return true;
};

const _overlapRatioFor = (column) => {
  const colKey = _columnKey(column);
  const cde = _effectiveCde(column);
  if (!cde || !cdeByKey.has(cde)) return null;
  if (state.overrides.has(colKey)) {
    const detail = state.detailByColumn.get(colKey);
    const ratio = detail?.overlap_by_cde?.[cde];
    return Number.isFinite(ratio) ? ratio : null;
  }
  const ratio = state.payload?.column_summaries?.[colKey]?.value_overlap_ratio;
  return Number.isFinite(ratio) ? ratio : null;
};

const _filteredColumns = () => {
  const cols = state.payload?.columns ?? [];
  const filtered = cols.filter((c) => {
    if (!_passesFilters(c)) return false;
    if (state.filterText && !_columnLabel(c).toLowerCase().includes(state.filterText)) {
      return false;
    }
    return true;
  });
  if (state.activeSort === SORT.FILE) return filtered;
  if (state.activeSort === SORT.FILE_REV) return [...filtered].reverse();
  if (state.activeSort === SORT.TARGET_ASC || state.activeSort === SORT.TARGET_DESC) {
    return _sortByTarget(filtered, state.activeSort === SORT.TARGET_DESC);
  }
  return _sortByFit(filtered, state.activeSort === SORT.FIT_ASC ? 1 : -1);
};

const OUTCOME_SORT_RANK = { [OUTCOME.REWRITE]: 0, [OUTCOME.PASSTHROUGH]: 1, [OUTCOME.UNCHANGED]: 2 };

const _sortByTarget = (cols, desc) => {
  const sign = desc ? -1 : 1;
  return [...cols].sort((a, b) => {
    const ra = OUTCOME_SORT_RANK[_effectiveOutcome(a)] ?? 9;
    const rb = OUTCOME_SORT_RANK[_effectiveOutcome(b)] ?? 9;
    if (ra !== rb) return sign * (ra - rb);
    const la = (_effectiveCde(a) ?? '').toLowerCase();
    const lb = (_effectiveCde(b) ?? '').toLowerCase();
    return sign * la.localeCompare(lb);
  });
};

// Unmapped columns (null ratio, no CDE) sink below passthrough (null ratio,
// has CDE) so "—" and "N/A" don't intermingle when sorting by fit.
const _sortByFit = (cols, sign) => [...cols].sort((a, b) => {
  const ka = _overlapRatioFor(a);
  const kb = _overlapRatioFor(b);
  const aHas = Number.isFinite(ka);
  const bHas = Number.isFinite(kb);
  if (aHas && bHas) return sign * (ka - kb);
  if (aHas) return -1;
  if (bHas) return 1;
  const aCde = _effectiveCde(a);
  const bCde = _effectiveCde(b);
  if (aCde && !bCde) return -1;
  if (!aCde && bCde) return 1;
  return 0;
});

/* ─── Filter sidebar (slides in from left, hover-preview) ── */
const sidebarEl = document.getElementById('filterSidebar');

const _isDefaultFilters = () =>
  state.filters.outcomes.size === 3 && !state.filters.showEmpty;

// Eye icon inside filter checkboxes — slash line hides/shows via CSS :checked
// sibling combinator, so one SVG works for both states.
const VISIBILITY_EYE_SVG = `<span class="fm-check-eye"><svg viewBox="0 0 16 14" width="16" height="12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><path d="M1 7S4 2.5 8 2.5 15 7 15 7s-3 4.5-7 4.5S1 7 1 7z"/><circle cx="8" cy="7" r="2" class="fm-eye-pupil"/><line x1="2.5" y1="1" x2="13.5" y2="13" class="fm-eye-slash"/></svg></span>`;

const _outcomeCounts = () => {
  const cols = state.payload?.columns ?? [];
  const counts = { [OUTCOME.REWRITE]: 0, [OUTCOME.PASSTHROUGH]: 0, [OUTCOME.UNCHANGED]: 0 };
  let empty = 0;
  for (const col of cols) {
    counts[_effectiveOutcome(col)]++;
    if (!_hasValues(col)) empty++;
  }
  return { ...counts, empty, total: cols.length };
};

// Horizontal sliders icon — broader than the funnel, fits "settings" framing
const SETTINGS_ICON_SVG = `<svg class="filter-sidebar-trigger-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="1" y="3" width="14" height="1.5" rx=".75"/><rect x="1" y="7.25" width="14" height="1.5" rx=".75"/><rect x="1" y="11.5" width="14" height="1.5" rx=".75"/><circle cx="5" cy="3.75" r="2" fill="var(--white)" stroke="currentColor" stroke-width="1.2"/><circle cx="11" cy="8" r="2" fill="var(--white)" stroke="currentColor" stroke-width="1.2"/><circle cx="7" cy="12.25" r="2" fill="var(--white)" stroke="currentColor" stroke-width="1.2"/></svg>`;

const renderFilterTrigger = () => {
  const isOpen = state.filterSidebarOpen;
  const isDefault = _isDefaultFilters();
  const cls = isOpen
    ? 'filter-sidebar-trigger filter-sidebar-trigger--open'
    : isDefault
      ? 'filter-sidebar-trigger'
      : 'filter-sidebar-trigger filter-sidebar-trigger--active';
  sourceFilterEl.innerHTML = `
    <button class="${cls}" id="filterSidebarTrigger">
      ${SETTINGS_ICON_SVG}
      <span>Settings</span>
    </button>
  `;
};

const _openFilterSidebar = () => {
  state.filterSidebarOpen = true;
  sidebarEl.classList.remove('hidden');
  _renderSidebarContent();
  renderFilterTrigger();
};

const _closeFilterSidebar = () => {
  state.filterSidebarOpen = false;
  sidebarEl.classList.add('hidden');
  _clearHoverPreview();
  renderFilterTrigger();
};

const _countBadge = (n, total) =>
  `<span class="fm-check-count">${n} / ${total}</span>`;

const _outcomeCheckHtml = (outcome, label, icon, iconCls, desc, count, total) => {
  const checked = state.filters.outcomes.has(outcome) ? 'checked' : '';
  return `
    <label class="fm-check" data-outcome="${outcome}" data-highlight="outcome:${outcome}">
      <input type="checkbox" ${checked} />
      ${VISIBILITY_EYE_SVG}
      <div class="fm-check-content">
        <div class="fm-check-head">
          <span class="mapping-filter-icon ${iconCls}">${icon}</span>
          <span class="fm-check-label">${label}</span>
          ${_countBadge(count, total)}
        </div>
        <p class="fm-check-desc">${desc}</p>
      </div>
    </label>
  `;
};

const _toggleCheckHtml = (id, label, checked, desc, count, total) => `
  <label class="fm-check" data-toggle="${id}" data-highlight="${id}">
    <input type="checkbox" ${checked ? 'checked' : ''} />
    ${VISIBILITY_EYE_SVG}
    <div class="fm-check-content">
      <div class="fm-check-head">
        <span class="fm-check-label">${label}</span>
        ${_countBadge(count, total)}
      </div>
      <p class="fm-check-desc">${desc}</p>
    </div>
  </label>
`;

const _isRenameActive = (colKey) => {
  if (state.renameOverrides.has(colKey)) return state.renameOverrides.get(colKey);
  return state.renameDefault;
};

const _renderSidebarContent = () => {
  const counts = _outcomeCounts();
  sidebarEl.innerHTML = `
    <div class="fs-head">
      <h3 class="fs-title">Column settings</h3>
      <button class="fs-reset" data-action="reset-filters" ${_isDefaultFilters() ? 'disabled' : ''}>Reset</button>
    </div>
    <section class="fm-section">
      <h3 class="fm-section-title">Column visibility</h3>
      ${_outcomeCheckHtml(OUTCOME.REWRITE, 'Will rewrite', OUTCOME_ICON[OUTCOME.REWRITE], OUTCOME_CLASS[OUTCOME.REWRITE], OUTCOME_DESC[OUTCOME.REWRITE], counts[OUTCOME.REWRITE], counts.total)}
      ${_outcomeCheckHtml(OUTCOME.PASSTHROUGH, 'Pass-through', OUTCOME_ICON[OUTCOME.PASSTHROUGH], OUTCOME_CLASS[OUTCOME.PASSTHROUGH], OUTCOME_DESC[OUTCOME.PASSTHROUGH], counts[OUTCOME.PASSTHROUGH], counts.total)}
      ${_outcomeCheckHtml(OUTCOME.UNCHANGED, 'Unmapped', OUTCOME_ICON[OUTCOME.UNCHANGED], OUTCOME_CLASS[OUTCOME.UNCHANGED], OUTCOME_DESC[OUTCOME.UNCHANGED], counts[OUTCOME.UNCHANGED], counts.total)}
      <hr class="fm-divider" />
      ${_toggleCheckHtml('showEmpty', 'Show empty columns', state.filters.showEmpty, 'Include columns where all rows are blank or null.', counts.empty, counts.total)}
    </section>
    <section class="fm-section">
      <h3 class="fm-section-title">Column rename</h3>
      <label class="fs-rename-toggle">
        <span class="fs-rename-label">Rename to match standard</span>
        <input type="checkbox" ${state.renameDefault ? 'checked' : ''} />
        <span class="fs-toggle-track"><span class="fs-toggle-thumb"></span></span>
      </label>
      <p class="fm-check-desc fs-rename-desc">When enabled, your column names will be changed to match the names of the targeted standard.</p>
    </section>
  `;
  _bindSidebarEvents();
};

const _bindSidebarEvents = () => {
  sidebarEl.querySelector('[data-action="reset-filters"]')?.addEventListener('click', () => {
    state.filters.outcomes = new Set([OUTCOME.REWRITE, OUTCOME.PASSTHROUGH, OUTCOME.UNCHANGED]);
    state.filters.showEmpty = false;
    _refreshAfterFilterChange();
  });
  sidebarEl.querySelectorAll('[data-outcome]').forEach((label) => {
    label.querySelector('input').addEventListener('change', (e) => {
      const outcome = label.dataset.outcome;
      if (e.target.checked) state.filters.outcomes.add(outcome);
      else state.filters.outcomes.delete(outcome);
      _refreshAfterFilterChange();
    });
    label.addEventListener('mouseenter', () => _showHoverPreview(label.dataset.highlight));
    label.addEventListener('mouseleave', _clearHoverPreview);
  });
  sidebarEl.querySelectorAll('[data-toggle]').forEach((label) => {
    label.querySelector('input').addEventListener('change', (e) => {
      if (label.dataset.toggle === 'showEmpty') state.filters.showEmpty = e.target.checked;
      _refreshAfterFilterChange();
    });
    label.addEventListener('mouseenter', () => _showHoverPreview(label.dataset.highlight));
    label.addEventListener('mouseleave', _clearHoverPreview);
  });
  const renameInput = sidebarEl.querySelector('.fs-rename-toggle input');
  if (renameInput) {
    renameInput.addEventListener('change', (e) => {
      state.renameDefault = e.target.checked;
      _persistReviewChoices();
      renderRows();
    });
  }
};

/* Hover preview — bidirectional show/hide indicators.
   Checked categories: matching rows fade (they'd disappear on uncheck).
   Unchecked categories: ghost rows appear (they'd materialise on check). */
const MAX_GHOST_ROWS = 8;

// Sidebar re-renders replace DOM under the cursor, which fires spurious
// mouseenter events on the new labels. Suppress previews during re-render.
let _hoverSuppressed = false;

const _isHighlightActive = (highlight) => {
  if (highlight.startsWith('outcome:')) return state.filters.outcomes.has(highlight.split(':')[1]);
  if (highlight === 'showEmpty') return state.filters.showEmpty;
  return false;
};

const _highlightMatches = (highlight, col) => {
  if (highlight.startsWith('outcome:')) return _effectiveOutcome(col) === highlight.split(':')[1];
  if (highlight === 'showEmpty') return !_hasValues(col);
  return false;
};

// Only include columns that would actually become visible if this filter were
// toggled — i.e., they match the highlight AND pass all OTHER active filters.
const _wouldPassOtherFilters = (highlight, col) => {
  if (highlight.startsWith('outcome:')) {
    return state.filters.showEmpty || _hasValues(col);
  }
  if (highlight === 'showEmpty') {
    return state.filters.outcomes.has(_effectiveOutcome(col));
  }
  return true;
};

const _hiddenColumnsForHighlight = (highlight) => {
  const allCols = state.payload?.columns ?? [];
  return allCols.filter((col) => {
    if (!_highlightMatches(highlight, col)) return false;
    if (!_wouldPassOtherFilters(highlight, col)) return false;
    return !_passesFilters(col);
  });
};

const _ghostRowHtml = (col) => {
  const outcome = _effectiveOutcome(col);
  const colKey = _columnKey(col);
  const cde = _effectiveCde(col);
  const target = cde
    ? `<span class="mapping-row-target">${_escHtml(cde)}</span>`
    : `<span class="mapping-row-target mapping-row-target--empty">—</span>`;
  return `
    <div class="mapping-row mapping-row--ghost" data-key="${_escAttr(colKey)}">
      <div class="mapping-row-col">${_escHtml(_columnLabel(col))}</div>
      <div class="mapping-row-status ${OUTCOME_CLASS[outcome]}">${OUTCOME_ICON[outcome]}</div>
      ${target}
      <div class="mapping-row-fit mapping-row-fit--empty">—</div>
      <div class="mapping-row-chev"></div>
    </div>
  `;
};

const _insertGhostRows = (cols) => {
  const capped = cols.slice(0, MAX_GHOST_ROWS);
  const remaining = cols.length - capped.length;
  const container = document.createElement('div');
  container.className = 'ghost-row-container';
  container.innerHTML =
    capped.map(_ghostRowHtml).join('') +
    (remaining > 0 ? `<div class="ghost-row-more">… and ${remaining} more</div>` : '');
  rowsEl.appendChild(container);
};

let _ghostFadeOutTimer = null;

// Immediately strip all preview classes and remove ghost containers.
// Used when transitioning between previews (no animation needed).
const _clearPreviewImmediate = () => {
  if (_ghostFadeOutTimer) {
    clearTimeout(_ghostFadeOutTimer);
    _ghostFadeOutTimer = null;
  }
  rowsEl.querySelectorAll('.mapping-row').forEach((r) => {
    r.classList.remove('filter-preview-will-hide', 'filter-preview-dim');
  });
  const hadGhosts = rowsEl.querySelector('.ghost-row-container');
  rowsEl.querySelectorAll('.ghost-row-container').forEach((el) => el.remove());
  if (hadGhosts && !rowsEl.querySelector('.mapping-row')) {
    emptyState.classList.remove('hidden');
  }
};

// Animated clear — ghost rows fade out before removal.
// Used on mouseleave for a smooth exit transition.
const _clearHoverPreview = () => {
  if (_ghostFadeOutTimer) {
    clearTimeout(_ghostFadeOutTimer);
    _ghostFadeOutTimer = null;
  }
  rowsEl.querySelectorAll('.mapping-row').forEach((r) => {
    r.classList.remove('filter-preview-will-hide', 'filter-preview-dim');
  });
  const ghostContainers = rowsEl.querySelectorAll('.ghost-row-container');
  if (ghostContainers.length) {
    ghostContainers.forEach((container) => {
      container.querySelectorAll('.mapping-row--ghost').forEach((g) => g.classList.add('ghost-row-leaving'));
    });
    _ghostFadeOutTimer = setTimeout(() => {
      ghostContainers.forEach((el) => el.remove());
      _ghostFadeOutTimer = null;
      if (!rowsEl.querySelector('.mapping-row')) {
        emptyState.classList.remove('hidden');
      }
    }, 200);
  }
};

const _showHoverPreview = (highlight) => {
  if (_hoverSuppressed) return;
  _clearPreviewImmediate();
  const allCols = state.payload?.columns ?? [];

  if (_isHighlightActive(highlight)) {
    const rows = rowsEl.querySelectorAll('.mapping-row:not(.mapping-row--ghost)');
    rows.forEach((r) => {
      const col = allCols.find((c) => _columnKey(c) === r.dataset.key);
      if (col && _highlightMatches(highlight, col)) {
        r.classList.add('filter-preview-will-hide');
      }
    });
  } else {
    const hidden = _hiddenColumnsForHighlight(highlight);
    if (hidden.length) {
      _insertGhostRows(hidden);
      emptyState.classList.add('hidden');
      rowsEl.querySelectorAll('.mapping-row:not(.mapping-row--ghost)').forEach((r) => {
        r.classList.add('filter-preview-dim');
      });
    }
  }
};

const _refreshAfterFilterChange = () => {
  _hoverSuppressed = true;
  renderRows();
  renderFilterTrigger();
  if (state.filterSidebarOpen) _renderSidebarContent();
  requestAnimationFrame(() => { _hoverSuppressed = false; });
};

sourceFilterEl.addEventListener('click', (e) => {
  if (e.target.closest('#filterSidebarTrigger')) {
    if (state.filterSidebarOpen) _closeFilterSidebar();
    else _openFilterSidebar();
  }
});

colSearchEl.addEventListener('input', (e) => {
  state.filterText = e.target.value.toLowerCase();
  renderRows();
});

/* ─── Sortable column headers ───────────────────────────── */
const columnSortEl = document.getElementById('columnSortBtn');
const targetSortEl = document.getElementById('targetSortBtn');
const fitSortEl = document.getElementById('valueFitSortBtn');

const _sortArrow = (active, ascVal, descVal) => {
  if (active === ascVal) return '↑';
  if (active === descVal) return '↓';
  return '⇅';
};

const renderSortHeads = () => {
  const s = state.activeSort;
  const colArrow = columnSortEl?.querySelector('.mapping-list-head-sort-arrow');
  const tgtArrow = targetSortEl?.querySelector('.mapping-list-head-sort-arrow');
  const fitArrow = fitSortEl?.querySelector('.mapping-list-head-sort-arrow');
  if (colArrow) colArrow.textContent = _sortArrow(s, SORT.FILE, SORT.FILE_REV);
  if (tgtArrow) tgtArrow.textContent = _sortArrow(s, SORT.TARGET_ASC, SORT.TARGET_DESC);
  if (fitArrow) fitArrow.textContent = _sortArrow(s, SORT.FIT_ASC, SORT.FIT_DESC);
  columnSortEl?.classList.toggle('mapping-list-head-sort--active', s === SORT.FILE || s === SORT.FILE_REV);
  targetSortEl?.classList.toggle('mapping-list-head-sort--active', s === SORT.TARGET_ASC || s === SORT.TARGET_DESC);
  fitSortEl?.classList.toggle('mapping-list-head-sort--active', s === SORT.FIT_ASC || s === SORT.FIT_DESC);
};

const _cycleSortPair = (ascVal, descVal) => {
  if (state.activeSort === ascVal) state.activeSort = descVal;
  else state.activeSort = ascVal;
  renderSortHeads();
  renderRows();
};

columnSortEl?.addEventListener('click', () => _cycleSortPair(SORT.FILE, SORT.FILE_REV));
targetSortEl?.addEventListener('click', () => _cycleSortPair(SORT.TARGET_ASC, SORT.TARGET_DESC));
fitSortEl?.addEventListener('click', () => _cycleSortPair(SORT.FIT_ASC, SORT.FIT_DESC));

/* ─── List rows ──────────────────────────────────────────── */
const renderRows = () => {
  if (!state.payload) {
    rowsEl.innerHTML = '';
    emptyState.classList.remove('hidden');
    emptyState.textContent = MSG_NO_ANALYSIS_DATA;
    if (harmonizeButton) harmonizeButton.disabled = true;
    return;
  }
  const filtered = _filteredColumns();
  if (!filtered.length) {
    rowsEl.innerHTML = '';
    emptyState.classList.remove('hidden');
    emptyState.textContent = MSG_NO_COLUMNS;
  } else {
    emptyState.classList.add('hidden');
    rowsEl.innerHTML = filtered.map(_rowHtml).join('');
  }
  if (harmonizeButton) harmonizeButton.disabled = !state.payload;
};

const _rowHtml = (col) => {
  const outcome = _effectiveOutcome(col);
  const colKey = _columnKey(col);
  const colLabel = _columnLabel(col);
  const seen = state.seenColumns.has(colKey);
  const override = state.overrides.get(colKey);
  let target;
  if (_isNoMapValue(override)) {
    target = `<span class="mapping-row-target mapping-row-target--none">No Mapping</span>`;
  } else {
    const cde = _effectiveCde(col);
    target = cde
      ? `<span class="mapping-row-target" data-fast-tooltip="${_escAttr(cde)}"><span class="mapping-row-target-text">${_escHtml(cde)}</span></span>`
      : `<span class="mapping-row-target mapping-row-target--empty"></span>`;
  }
  const fit = _overlapCellHtml(col);
  return `
    <div class="mapping-row${seen ? ' mapping-row--seen' : ''}" data-key="${_escAttr(colKey)}" data-outcome="${outcome}" data-has-values="${_hasValues(col) ? '1' : '0'}">
      <div class="mapping-row-col" data-fast-tooltip="${_escAttr(colLabel)}"><span class="mapping-row-col-text">${_escHtml(colLabel)}</span></div>
      <div class="mapping-row-status ${OUTCOME_CLASS[outcome]}" data-fast-tooltip="${_escAttr(OUTCOME_TIP[outcome])}">${OUTCOME_ICON[outcome]}</div>
      ${target}
      ${fit}
      <div class="mapping-row-chev"${seen ? ' data-fast-tooltip="Reviewed"' : ''}>${seen ? '✓' : '›'}</div>
    </div>
  `;
};

const _overlapCellHtml = (col) => {
  const kind = _mappingKindFor(col);
  if (kind === MAPPING_KIND.RENAME_ONLY) {
    return `<div class="mapping-row-fit mapping-row-fit--na" data-fast-tooltip="${_escAttr(PASSTHROUGH_FIT_TIP)}">N/A</div>`;
  }
  if (kind === MAPPING_KIND.VALUE_MAPPING) {
    const ratio = _overlapRatioFor(col);
    if (ratio !== null) {
      return `<div class="mapping-row-fit mapping-row-fit--ratio" data-fast-tooltip="${_escAttr(MATCH_TIP)}">${_formatRatio(ratio)}</div>`;
    }
  }
  return `<div class="mapping-row-fit mapping-row-fit--empty">—</div>`;
};

rowsEl.addEventListener('click', (e) => {
  const row = e.target.closest('.mapping-row');
  if (!row) return;
  openTakeover(row.dataset.key);
});

/* ─── Takeover lifecycle ─────────────────────────────────── */
const openTakeover = async (colKey) => {
  state.takeoverKey = colKey;
  state.pickerOpen = false;
  document.body.classList.add('lock');
  takeoverEl.classList.remove('hidden');
  renderTakeover();
  // Fetch column detail (match counts, types, PVs) lazily on first open
  await _ensureColumnDetail(colKey);
  if (state.takeoverKey === colKey) renderTakeover();
};

const closeTakeover = () => {
  if (state.takeoverKey) state.seenColumns.add(state.takeoverKey);
  state.takeoverKey = null;
  state.pickerOpen = false;
  _closeRenameDropdown();
  takeoverEl.classList.add('hidden');
  document.body.classList.remove('lock');
  renderRows();
  if (state.filterSidebarOpen) _renderSidebarContent();
};

const navigate = async (delta) => {
  const list = _filteredColumns();
  const i = list.findIndex((c) => _columnKey(c) === state.takeoverKey);
  if (state.takeoverKey) state.seenColumns.add(state.takeoverKey);
  const ni = i + delta;
  if (ni < 0 || ni >= list.length) return;
  const next = list[ni];
  state.takeoverKey = _columnKey(next);
  state.pickerOpen = false;
  renderTakeover();
  await _ensureColumnDetail(state.takeoverKey);
  if (state.takeoverKey === _columnKey(next)) renderTakeover();
};

const _ensureColumnDetail = async (colKey) => {
  if (state.detailByColumn.has(colKey)) return;
  const col = (state.payload?.columns ?? []).find((c) => _columnKey(c) === colKey);
  if (!col) return;
  const fileId = state.payload.file_id;
  const selected = _effectiveCde(col);
  const url = new URL(`${columnDetailBase}/${encodeURIComponent(fileId)}/${encodeURIComponent(colKey)}`, window.location.origin);
  if (selected) url.searchParams.set('selected_cde_key', selected);
  try {
    const response = await fetch(url.toString());
    if (!response.ok) {
      console.warn('column-detail fetch failed', response.status);
      return;
    }
    const detail = await response.json();
    state.detailByColumn.set(colKey, detail);
  } catch (err) {
    console.warn('column-detail fetch error', err);
  }
};

const _effectiveRenameTarget = (colKey, cde) => {
  if (state.renameTargets.has(colKey)) return state.renameTargets.get(colKey);
  if (!cde) return null;
  return cdeByKey.get(cde)?.label ?? cde;
};

const _columnRenamesPayload = () => {
  const renames = {};
  for (const col of state.payload?.columns ?? []) {
    const colKey = _columnKey(col);
    if (!_isRenameActive(colKey)) continue;
    const targetName = _effectiveRenameTarget(colKey, _effectiveCde(col));
    if (targetName && targetName !== _columnLabel(col)) renames[colKey] = targetName;
  }
  return renames;
};

const _manualOverridesPayload = () => {
  const overrides = {};
  for (const [k, v] of state.overrides.entries()) overrides[k] = v;
  return overrides;
};

const _renameIndicatorHtml = (col, colKey, cde) => {
  const originalName = _columnLabel(col);
  const renameActive = _isRenameActive(colKey);
  const targetName = _effectiveRenameTarget(colKey, cde);
  const hasCustomTarget = state.renameTargets.has(colKey);
  if (!renameActive) {
    if (!cde) return '';
    const defaultTarget = cdeByKey.get(cde)?.label ?? cde;
    if (defaultTarget === originalName) return '';
    return `
      <button class="takeover-rename-off" data-action="toggle-rename" title="Enable rename for this column">
        Rename available → ${_escHtml(defaultTarget)}
      </button>
    `;
  }
  if (!targetName) return '';
  if (targetName === originalName && !hasCustomTarget) return '';
  return `
    <div class="takeover-rename-row">
      <span class="takeover-rename-arrow">→</span>
      <button class="takeover-rename-pick" data-action="open-rename-picker" title="Change rename target">
        <span class="takeover-rename-target">${_escHtml(targetName)}</span>
        <span class="takeover-rename-caret">▾</span>
      </button>
      <button class="takeover-rename-btn" data-action="toggle-rename" title="Disable rename for this column">✕</button>
    </div>
  `;
};

const _openRenameDropdown = (colKey) => {
  _closePicker();
  const wrap = takeoverCardEl.querySelector('.takeover-rename-row') ?? takeoverCardEl.querySelector('.takeover-head-names');
  if (!wrap) return;
  state.renamePickerOpen = true;
  const existing = wrap.querySelector('.rename-dropdown');
  if (existing) existing.remove();
  const dd = document.createElement('div');
  dd.className = 'rename-dropdown';
  dd.id = 'renameDropdown';
  dd.innerHTML = `
    <div class="rename-dd-search"><input type="search" placeholder="Filter standards…" autocomplete="off" /></div>
    <div class="rename-dd-list">${_renameDropdownItemsHtml('')}</div>
  `;
  wrap.appendChild(dd);
  const input = dd.querySelector('input');
  setTimeout(() => input?.focus(), 0);
  input?.addEventListener('input', (e) => {
    dd.querySelector('.rename-dd-list').innerHTML = _renameDropdownItemsHtml(e.target.value);
  });
  dd.addEventListener('click', (e) => {
    const opt = e.target.closest('.rename-dd-opt');
    if (!opt) return;
    state.renameTargets.set(colKey, opt.dataset.label);
    if (!_isRenameActive(colKey)) state.renameOverrides.set(colKey, true);
    _persistReviewChoices();
    _closeRenameDropdown();
    renderTakeover();
    renderRows();
  });
};

const _closeRenameDropdown = () => {
  state.renamePickerOpen = false;
  document.getElementById('renameDropdown')?.remove();
};

const _renameDropdownItemsHtml = (q) => {
  const lq = q.toLowerCase();
  const matches = cdeCatalog.filter((c) =>
    !lq || (c.label || c.key).toLowerCase().includes(lq) || (c.description || '').toLowerCase().includes(lq)
  );
  if (!matches.length) return `<div class="rename-dd-empty">No standards match "${_escHtml(q)}"</div>`;
  return matches.map((c) => `
    <div class="rename-dd-opt" data-label="${_escAttr(c.label || c.key)}">
      <span class="rename-dd-name">${_escHtml(c.label || c.key)}</span>
      ${c.description ? `<span class="rename-dd-desc">${_escHtml(c.description)}</span>` : ''}
    </div>
  `).join('');
};

const renderTakeover = () => {
  const colKey = state.takeoverKey;
  if (!colKey) { closeTakeover(); return; }
  const col = (state.payload?.columns ?? []).find((c) => _columnKey(c) === colKey);
  if (!col) { closeTakeover(); return; }

  const list = _filteredColumns();
  const idx = list.findIndex((c) => _columnKey(c) === colKey);
  const outcome = _effectiveOutcome(col);
  const cde = _effectiveCde(col);
  const detail = state.detailByColumn.get(colKey) ?? null;
  const profile = detail?.profile ?? state.payload.column_profiles?.[colKey] ?? null;
  const cdeType = detail?.cde_types?.[cde] ?? cdeByKey.get(cde)?.type ?? 'pv';
  const pvSet = (cde && cdeType === 'pv' && Array.isArray(detail?.selected_pvs))
    ? new Set(detail.selected_pvs)
    : null;

  takeoverCardEl.innerHTML = `
    <header class="takeover-head">
      <div class="takeover-head-left">
        <span class="takeover-head-status ${OUTCOME_CLASS[outcome]}">${OUTCOME_ICON[outcome]}</span>
        <div class="takeover-head-names">
          <h2 class="takeover-head-name" data-fast-tooltip="${_escAttr(_columnLabel(col))}">${_escHtml(_columnLabel(col))}</h2>
          ${_renameIndicatorHtml(col, colKey, cde)}
        </div>
      </div>
      <div class="takeover-head-right">
        <span class="takeover-counter">${idx + 1} of ${list.length}</span>
        <button class="takeover-btn" data-action="prev" ${idx <= 0 ? 'disabled' : ''}>← Prev</button>
        <button class="takeover-btn" data-action="next" ${idx >= list.length - 1 ? 'disabled' : ''}>Next →</button>
        <button class="takeover-btn takeover-btn--icon takeover-btn--close" data-action="close" aria-label="Close">✕</button>
      </div>
    </header>
    <div class="takeover-body">
      <section class="takeover-pane takeover-pane--data">${_dataPaneHtml(col, profile, pvSet)}</section>
      <section class="takeover-pane takeover-pane--target">${_targetPaneHtml(col, cde, detail, profile)}</section>
    </div>
  `;
  _bindTakeoverEvents(col);
};

const _conformSummaryHtml = (col, cde, detail, profile) => {
  if (!cde) return '';
  const totalDistinct = profile?.total_distinct ?? profile?.distinct_values?.length ?? 0;
  const cdeType = detail?.cde_types?.[cde] ?? cdeByKey.get(cde)?.type ?? 'pv';
  const matchN = detail?.match_counts?.[cde] ?? 0;

  if (cdeType === 'passthrough') {
    return `<span class="conform-summary conform-summary--neutral">${PASSTHROUGH_GLYPH} Pass-through — values unchanged</span>`;
  }
  const pct = totalDistinct > 0 ? _formatRatio(matchN / totalDistinct) : '0%';
  if (cdeType === 'numeric') {
    if (matchN > 0) {
      return `<span class="conform-summary">${pct} of values numeric</span>`;
    }
    return `<span class="conform-summary conform-summary--warning">0% of values numeric</span>`;
  }
  if (matchN > 0) {
    return `<span class="conform-summary">${pct} of values fit</span>`;
  }
  if (detail) {
    return `<span class="conform-summary conform-summary--warning">0% of values fit</span>`;
  }
  return '';
};

const _dataPaneHtml = (col, profile, pvSet) => {
  if (!profile) {
    return `
      <div class="takeover-pane-head">
        <h3 class="takeover-pane-title">Your column</h3>
      </div>
      <div class="takeover-pane-empty">No column profile available.</div>
    `;
  }
  const distinct = profile.distinct_values ?? [];
  return `
    <div class="takeover-pane-head">
      <h3 class="takeover-pane-title">Your column</h3>
    </div>
    <div class="takeover-pane-list" id="dataList">
      <div class="data-cols-head">
        <span aria-hidden="true"></span>
        <span>Value</span>
        <span>Count</span>
      </div>
      <ul class="samples-full">${distinct.map((s) => _sampleHtml(s, pvSet)).join('')}</ul>
    </div>
  `;
};

const _sampleHtml = (s, pvSet) => {
  // Strict equality against the selected CDE's PV set — case- and whitespace-
  // sensitive per the "all character differences are semantically significant"
  // domain rule. pvSet is null for non-PV CDEs (numeric / pass-through).
  const isMatch = pvSet ? pvSet.has(s.value) : false;
  const liClass = isMatch ? 'match' : '';
  const okGlyph = isMatch ? '✓' : '';
  return `<li class="${liClass}"><span class="ok">${okGlyph}</span><span class="v" title="${_escAttr(s.value)}">${_escHtml(s.value)}</span><span class="c">${(s.count ?? 0).toLocaleString()}</span></li>`;
};

const _targetPaneHtml = (col, cde, detail, profile) => {
  const colKey = _columnKey(col);
  const override = state.overrides.get(colKey);
  const isAi = cde && cde === _topAiCdeKey(col) && !state.overrides.has(colKey);
  const isNone = _isNoMapValue(override);
  const meta = cde ? cdeByKey.get(cde) : null;
  const cdeType = detail?.cde_types?.[cde] ?? meta?.type ?? 'pv';
  const pvs = detail?.selected_pvs ?? null;

  let pickerInner;
  if (isNone) {
    pickerInner = `<span class="cde-picker-name cde-picker-name--none">No Mapping</span><span class="cde-picker-caret">▾</span>`;
  } else if (!cde) {
    pickerInner = `<span class="cde-picker-name cde-picker-name--placeholder">Select a target standard…</span><span class="cde-picker-caret">▾</span>`;
  } else {
    const typeBadge = cdeType === 'numeric'
      ? `<span class="type-badge type-badge--numeric">Numeric</span>`
      : cdeType === 'passthrough'
        ? `<span class="type-badge type-badge--passthrough">Pass-through</span>`
        : '';
    pickerInner = `
      <span class="cde-picker-name" title="${_escAttr(cde)}">${_escHtml(cde)}</span>
      ${typeBadge}
      ${isAi ? `<span class="ai-badge">✦ AI rec</span>` : ''}
      <span class="cde-picker-caret cde-picker-caret--end">▾</span>
    `;
  }

  const head = `
    <div class="takeover-pane-head">
      <h3 class="takeover-pane-title">Target standard</h3>
      ${_conformSummaryHtml(col, cde, detail, profile)}
    </div>
    <div class="cde-picker-wrap" id="pickerWrap">
      <button class="cde-picker" id="cdePicker">${pickerInner}</button>
    </div>
  `;

  if (!cde) {
    return head + `<div class="target-empty">No target selected.<br/>Choose a standard from the picker above to see its permissible values.</div>`;
  }

  const desc = meta?.description ?? '';
  const descHtml = desc ? `<p class="cde-desc">${_escHtml(desc)}</p>` : '';

  if (cdeType === 'pv') {
    if (!pvs) {
      return head + descHtml + `<div class="takeover-pane-empty">Loading permissible values…</div>`;
    }
    return head + descHtml + `
      <div class="takeover-pane-list" id="pvList">
        <div class="pv-cols-head"><span>Permissible values</span></div>
        <ul class="pv-list-full">${pvs.map((p) => `<li>${_escHtml(p)}</li>`).join('')}</ul>
      </div>
    `;
  }

  const infoHtml = descHtml;
  if (cdeType === 'numeric') {
    return head + infoHtml + `
      <div class="type-card">
        <div class="type-icon">123</div>
        <div class="type-name">Numeric field</div>
        <div class="type-desc">Values are validated as numbers, not against a fixed list of permissible values.</div>
      </div>
    `;
  }
  return head + infoHtml + `
    <div class="type-card passthrough">
      <div class="type-icon">${PASSTHROUGH_GLYPH}</div>
      <div class="type-name">Pass-through field</div>
      <div class="type-desc">Values are stored as-is. There is no permissible value list and no validation against a standard.</div>
    </div>
  `;
};

/* ─── Picker dropdown (sorted by match count, with descriptions) ─ */
const _togglePicker = () => {
  if (state.pickerOpen) { _closePicker(); return; }
  _closeRenameDropdown();
  const colKey = state.takeoverKey;
  const col = (state.payload?.columns ?? []).find((c) => _columnKey(c) === colKey);
  if (!col) return;
  const detail = state.detailByColumn.get(colKey) ?? null;
  const matchCounts = detail?.match_counts ?? {};
  const cdeTypes = detail?.cde_types ?? {};
  const aiKeys = _aiCdeKeys(col);
  const aiKeysSet = new Set(aiKeys);
  // An Option is a catalog CDE overlaid with the runtime type and match count
  // from the column-detail fetch. Same shape on both sides of the divider.
  const _toOption = (c) => ({
    ...c,
    type: cdeTypes[c.key] ?? c.type,
    matches: matchCounts[c.key] ?? 0,
  });

  // Lower sections receive the catalog with AI candidates removed so each
  // CDE appears in exactly one place — top section (✦ AI rec) or below.
  const opts = cdeCatalog
    .filter((c) => !aiKeysSet.has(c.key))
    .map(_toOption)
    .sort((a, b) => b.matches - a.matches || a.label.localeCompare(b.label));
  // Preserve similarity order from the SDK — aiKeys is already top-first.
  const aiOptions = aiKeys
    .map((k) => cdeByKey.get(k))
    .filter(Boolean)
    .map(_toOption);

  const profile = detail?.profile ?? state.payload?.column_profiles?.[colKey] ?? null;
  const totalDistinct = profile?.total_distinct ?? profile?.distinct_values?.length ?? 0;

  const wrap = document.getElementById('pickerWrap');
  const dd = document.createElement('div');
  dd.className = 'dropdown';
  dd.id = 'pickerDropdown';
  dd.innerHTML = `
    <div class="dd-search"><input type="search" id="ddSearch" placeholder="Filter standards by name or description…" autocomplete="off" /></div>
    <div class="dd-list" id="ddList">${_renderDropdownItems(aiOptions, opts, '', totalDistinct)}</div>
  `;
  wrap.appendChild(dd);
  state.pickerOpen = true;
  setTimeout(() => dd.querySelector('input').focus(), 0);
  dd.querySelector('input').addEventListener('input', (e) => {
    document.getElementById('ddList').innerHTML = _renderDropdownItems(aiOptions, opts, e.target.value, totalDistinct);
  });
  dd.addEventListener('click', async (e) => {
    const opt = e.target.closest('.dd-opt');
    if (!opt) return;
    _pickOverride(state.takeoverKey, opt.dataset.value);
    _closePicker();
    renderRows();
    renderFilterTrigger();
    // Re-fetch detail with the new selection so the right-pane PV list updates.
    state.detailByColumn.delete(state.takeoverKey);
    renderTakeover();
    await _ensureColumnDetail(state.takeoverKey);
    renderRows();
    renderFilterTrigger();
    renderTakeover();
  });
};

const _closePicker = () => {
  state.pickerOpen = false;
  const dd = document.getElementById('pickerDropdown');
  if (dd) dd.remove();
};

const _renderDropdownItems = (aiOptions, opts, q, totalDistinct) => {
  const lq = q.toLowerCase();
  const matches = (c) => !lq
    || (c.label || c.key).toLowerCase().includes(lq)
    || (c.description || '').toLowerCase().includes(lq);
  const topItems = [];
  for (const ai of aiOptions) {
    if (matches(ai)) topItems.push(_optHtml(ai, 'ai', totalDistinct));
  }
  if (!q) {
    topItems.push(_optHtml({ key: NO_MAP_OPTION_VALUE, label: NO_MAPPING_OPTION, description: NO_MAP_DESC }, 'none', totalDistinct));
  }
  const valueOptions = opts.filter((o) => !_isRenameOnly(o.type) && matches(o));
  const renameOnlyOptions = opts.filter((o) => _isRenameOnly(o.type) && matches(o));
  renameOnlyOptions.sort((a, b) => a.label.localeCompare(b.label));
  const sections = [
    ...topItems,
    topItems.length ? `<div class="dd-divider"></div>` : '',
    _dropdownSectionHtml(VALUE_MAPPING_SECTION_LABEL, valueOptions, 'alt', totalDistinct),
    _dropdownSectionHtml(RENAME_ONLY_SECTION_LABEL, renameOnlyOptions, 'alt rename-only', totalDistinct),
  ].filter(Boolean);
  if (!sections.length || (valueOptions.length === 0 && renameOnlyOptions.length === 0 && topItems.length === 0)) {
    return `<div class="dd-empty">No standards match "${_escHtml(q)}"</div>`;
  }
  return sections.join('');
};

const _dropdownSectionHtml = (label, options, kind, totalDistinct) => {
  if (!options.length) return '';
  return `
    <section class="dd-section ${kind.includes('rename-only') ? 'dd-section--rename-only' : ''}">
      <div class="dd-section-label">${_escHtml(label)}</div>
      ${options.map((o) => _optHtml(o, kind, totalDistinct)).join('')}
    </section>
  `;
};

const _optHtml = (c, kind, totalDistinct) => {
  const showMatch = kind !== 'none';
  const matchCount = _isRenameOnly(c.type) ? 0 : c.matches ?? 0;
  const matchTip = _isRenameOnly(c.type) ? 'No permissible values to compare.' : MATCH_TIP;
  let matchHtml = '';
  if (showMatch) {
    if (_isRenameOnly(c.type)) {
      matchHtml = `<span class="count zero" title="${matchTip}">N/A</span>`;
    } else if (totalDistinct > 0) {
      const pct = _formatRatio(matchCount / totalDistinct);
      matchHtml = `<span class="count ${matchCount > 0 ? 'high' : 'zero'}" title="${matchTip}">${pct} value fit</span>`;
    } else {
      matchHtml = `<span class="count zero" title="${matchTip}">0% value fit</span>`;
    }
  }
  const aiBadge = kind === 'ai' ? `<span class="ai-badge">✦ AI rec</span>` : '';
  const typeBadge = c.type === 'numeric'
    ? `<span class="type-badge type-badge--numeric">Numeric</span>`
    : c.type === 'passthrough'
      ? `<span class="type-badge type-badge--passthrough">Pass-through</span>`
      : '';
  const desc = c.description ? `<div class="dd-desc">${_escHtml(c.description)}</div>` : '';
  return `
    <div class="dd-opt ${kind}" data-value="${_escAttr(c.key)}">
      <div class="dd-row">
        <span class="name" title="${_escAttr(c.label || c.key)}">${_escHtml(c.label || c.key)}</span>
        ${typeBadge}
        ${aiBadge}
        ${matchHtml}
      </div>
      ${desc}
    </div>
  `;
};

const _pickOverride = (colKey, value) => {
  const col = (state.payload?.columns ?? []).find((c) => _columnKey(c) === colKey);
  const aiKey = col ? _topAiCdeKey(col) : null;
  const normalizedValue = _normalizeOverrideValue(value);
  if (aiKey && normalizedValue === aiKey) {
    state.overrides.delete(colKey);
  } else {
    state.overrides.set(colKey, normalizedValue);
  }
  _persistReviewChoices();
};

/* ─── Event wiring ───────────────────────────────────────── */
const _bindTakeoverEvents = (col) => {
  const colKey = _columnKey(col);
  takeoverCardEl.querySelectorAll('[data-action]').forEach((el) => {
    el.addEventListener('click', (e) => {
      const action = el.dataset.action;
      if (action === 'close') closeTakeover();
      else if (action === 'prev') void navigate(-1);
      else if (action === 'next') void navigate(1);
      else if (action === 'toggle-rename') {
        const current = _isRenameActive(colKey);
        state.renameOverrides.set(colKey, !current);
        _closeRenameDropdown();
        _persistReviewChoices();
        renderTakeover();
        renderRows();
      } else if (action === 'open-rename-picker') {
        e.stopPropagation();
        if (state.renamePickerOpen) _closeRenameDropdown();
        else _openRenameDropdown(colKey);
      }
    });
  });
  const picker = document.getElementById('cdePicker');
  if (picker) {
    picker.addEventListener('click', (e) => { e.stopPropagation(); _togglePicker(); });
  }
};

document.addEventListener('click', (e) => {
  if (state.pickerOpen && !e.target.closest('#pickerDropdown') && !e.target.closest('#cdePicker')) {
    _closePicker();
  }
  if (state.renamePickerOpen && !e.target.closest('#renameDropdown') && !e.target.closest('[data-action="open-rename-picker"]')) {
    _closeRenameDropdown();
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (state.renamePickerOpen) { _closeRenameDropdown(); return; }
    if (state.filterSidebarOpen) { _closeFilterSidebar(); return; }
    if (state.pickerOpen) { _closePicker(); return; }
    if (state.takeoverKey) closeTakeover();
    return;
  }
  if (!state.takeoverKey || state.pickerOpen) return;
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowLeft') void navigate(-1);
  else if (e.key === 'ArrowRight') void navigate(1);
});

takeoverEl.querySelector('.takeover-backdrop').addEventListener('click', () => closeTakeover());

/* ─── Harmonize submission ───────────────────────────────── */
const _persistStageThreePayload = (body) => {
  const payloadForStageThree = {
    request: body,
    context: {
      fileName: state.payload?.file_name || 'Uploaded dataset',
      totalRows: state.payload?.total_rows ?? null,
      targetSchema: config.targetSchema,
      targetVersionNumber: state.payload?.target_version_number ?? targetVersionNumber,
    },
    manifest: state.payload?.manifest || null,
  };
  return writeToSession(STAGE_3_PAYLOAD_KEY, payloadForStageThree);
};

const _submitHarmonize = async () => {
  if (!state.payload || state.isSubmitting) return;
  state.isSubmitting = true;
  if (harmonizeButton) harmonizeButton.disabled = true;
  if (harmonizeButtonText) harmonizeButtonText.textContent = 'Preparing…';

  const overrides = _manualOverridesPayload();
  const columnRenames = _columnRenamesPayload();
  const manifest = state.payload?.manifest;
  if (!manifest || !manifest.column_mappings) {
    state.isSubmitting = false;
    if (harmonizeButton) harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    console.warn(MSG_MANIFEST_MISSING);
    return;
  }
  const fileId = state.payload.file_id;
  if (!isValidFileId(fileId)) {
    state.isSubmitting = false;
    if (harmonizeButton) harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    console.error(MSG_INVALID_FILE);
    return;
  }
  const body = {
    file_id: fileId,
    target_schema: config.targetSchema,
    target_version_number: state.payload?.target_version_number ?? targetVersionNumber,
    manual_overrides: overrides,
    column_renames: columnRenames,
    manifest,
  };
  removeFromSession(STAGE_3_JOB_KEY);
  const ok = _persistStageThreePayload({ ...body });
  if (!ok) {
    state.isSubmitting = false;
    if (harmonizeButton) harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    console.error(MSG_STORAGE_ERROR);
    return;
  }
  if (!isSafeRelativeUrl(stageThreeUrl)) {
    state.isSubmitting = false;
    if (harmonizeButton) harmonizeButton.disabled = false;
    if (harmonizeButtonText) harmonizeButtonText.textContent = HARMONIZE_BUTTON_LABEL;
    console.error('Invalid stage three URL');
    return;
  }
  const url = new URL(stageThreeUrl, window.location.origin);
  url.searchParams.set('file_id', fileId);
  url.searchParams.set('target_schema', config.targetSchema);
  if (body.target_version_number) url.searchParams.set('version_number', String(body.target_version_number));
  advanceMaxReachedStage('harmonize');
  window.location.assign(url.toString());
};

/* ─── Bootstrap ──────────────────────────────────────────── */
const _payloadIsCurrent = (payload) => payload != null && 'column_summaries' in payload;

const _fetchPayload = async (fileId, targetSchema) => {
  if (!fileId) return null;
  const response = await fetch(config.analyzeEndpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      file_id: fileId,
      target_schema: targetSchema || config.targetSchema,
      target_version_number: targetVersionNumber,
    }),
  });
  const payload = await response.json().catch((err) => {
    console.error('Failed to parse response JSON:', err);
    return {};
  });
  if (!response.ok) throw new Error(payload.detail || 'Unable to fetch mapping data');
  _savePayloadToStorage(payload);
  return payload;
};

const _ensureOverrideDetails = async () => {
  const cols = state.payload?.columns ?? [];
  const overriddenValueRows = cols.filter((col) => {
    const colKey = _columnKey(col);
    if (!state.overrides.has(colKey)) return false;
    const cde = _effectiveCde(col);
    const meta = cde ? cdeByKey.get(cde) : null;
    return meta && !_isRenameOnly(meta.type) && !state.detailByColumn.has(colKey);
  });
  await Promise.all(overriddenValueRows.map(async (col) => {
    await _ensureColumnDetail(_columnKey(col));
  }));
  if (overriddenValueRows.length) {
    renderFilterTrigger();
    renderRows();
  }
};

const _init = async () => {
  setActiveStage('mapping');
  initStepInstruction('mapping');
  initNavigationEvents();
  if (harmonizeButton) harmonizeButton.addEventListener('click', _submitHarmonize);

  let payload = _readPayloadFromStorage();
  const params = new URLSearchParams(window.location.search);
  const fileId = params.get('file_id') || payload?.file_id;
  const schema = params.get('schema') || config.targetSchema;
  if (!_payloadIsCurrent(payload)) {
    try {
      payload = await _fetchPayload(fileId, schema);
    } catch (err) {
      console.error(err);
    }
  }
  if (!payload) {
    renderFilterTrigger();
    renderRows();
    return;
  }
  state.payload = payload;
  state.overrides = new Map(
    Object.entries(payload.manual_overrides ?? {}).map(([key, value]) => [key, _normalizeOverrideValue(value)])
  );
  state.renameOverrides = new Map(
    Object.entries(payload.column_renames ?? {}).map(([key]) => [key, true])
  );
  state.renameTargets = new Map(
    Object.entries(payload.column_renames ?? {}).filter(([, value]) => typeof value === 'string')
  );
  renderFilterTrigger();
  renderRows();
  void _ensureOverrideDetails();
};

/* ─── Utils ──────────────────────────────────────────────── */
function _formatRatio(ratio) {
  return `${Math.round(ratio * 100)}%`;
}

function _escHtml(s) {
  return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function _escAttr(s) {
  return String(s ?? '').replace(/["'&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

void _init();
