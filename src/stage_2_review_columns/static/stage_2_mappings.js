/**
 * Stage 2 — column-mapping list with row-click takeover modal.
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
const TAG = { REC: 'rec', OVR: 'ovr', NONE: 'none' };
const MAPPING_KIND = { VALUE_MAPPING: 'value_mapping', RENAME_ONLY: 'rename_only', UNMAPPED: 'unmapped' };
// Source filter is single-select: clicking a chip filters the list to that
// category, clicking ALL shows everything. The chips themselves serve as the
// legend, so each category chip carries the same status icon used on the row.
// Values span two axes — mapping source (REC/OVR/NONE) and target CDE behavior
// (WILL_CHANGE). REC/OVR/NONE partition the columns by where the mapping came
// from; WILL_CHANGE cuts across them and surfaces only rows whose values are
// subject to harmonization (PV-typed targets — pass-through and unmapped rows
// won't change values, so they fall out). Each chip's count is accurate for
// "rows shown when selected"; the implicit "counts sum to total" property is
// broken, and that property was never user-visible.
const SOURCE_FILTER = { ALL: 'all', REC: TAG.REC, OVR: TAG.OVR, NONE: TAG.NONE, WILL_CHANGE: 'will_change' };
const STATUS = { [TAG.REC]: '✦', [TAG.OVR]: '✎', [TAG.NONE]: '○' };
const STATUS_CLASS = {
  [TAG.REC]: 'mapping-ico--rec',
  [TAG.OVR]: 'mapping-ico--ovr',
  [TAG.NONE]: 'mapping-ico--none',
};
const STATUS_TIP = {
  [TAG.REC]: 'AI recommendation',
  [TAG.OVR]: 'Manual override',
  [TAG.NONE]: 'No mapping',
};
const FILTER_DESC = {
  [SOURCE_FILTER.ALL]: 'Show all columns regardless of mapping status.',
  [SOURCE_FILTER.REC]: 'Columns mapped to a standard suggested by the AI model.',
  [SOURCE_FILTER.OVR]: 'Columns where you\'ve manually selected a different standard than the AI suggested.',
  [SOURCE_FILTER.NONE]: 'Columns with no target standard selected. These will be skipped during harmonization.',
  [SOURCE_FILTER.WILL_CHANGE]: 'Columns mapped to standards with permissible values — only these will have their values rewritten during harmonization.',
};
const MATCH_TIP = "Distinct values in your column that exactly match a permissible value of this CDE.";
const PASSTHROUGH_FIT_TIP = "This standard has no permissible value list — values will pass through unchanged.";
// Single source of truth for the pass-through glyph — same symbol on the
// filter chip, the row's fit cell, and the takeover conform pill, so users
// learn one icon for the concept across surfaces.
const PASSTHROUGH_GLYPH = '↪';
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
const VALUE_FIT_SORT = { NONE: 'none', ASC: 'asc', DESC: 'desc' };

const state = {
  payload: null,                  // Stage 1 analyze payload from session storage
  filters: {
    sources: new Set([TAG.REC, TAG.OVR, TAG.NONE]),
    willChangeOnly: false,
    hideEmpty: false,
  },
  filterModalOpen: false,
  filterText: '',
  overrides: new Map(),           // columnKey -> cdeKey | null
  takeoverKey: null,              // column key whose takeover is open
  pickerOpen: false,
  detailByColumn: new Map(),      // columnKey -> ColumnDetailResponse (cached)
  isSubmitting: false,
  // Tri-state cycle: NONE (CSV order, default) → ASC (worst fit first) → DESC
  // → NONE. Sort is opt-in because CSV order is meaningful — users often know
  // their dataset by column position.
  valueFitSort: VALUE_FIT_SORT.NONE,
};

/* ─── Storage helpers ────────────────────────────────────── */
const _savePayloadToStorage = (payload) => writeToSession(STAGE_2_PAYLOAD_KEY, payload);
const _readPayloadFromStorage = () => readFromSession(STAGE_2_PAYLOAD_KEY);

const _persistOverrides = () => {
  if (!state.payload) return;
  const overrides = {};
  for (const [k, v] of state.overrides.entries()) {
    overrides[k] = v;
  }
  state.payload = { ...state.payload, manual_overrides: overrides };
  _savePayloadToStorage(state.payload);
};

/* ─── Domain helpers ─────────────────────────────────────── */
const _columnSuggestions = (column) => {
  const key = column.column_key ?? column.column_name;
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
  const colKey = column.column_key ?? column.column_name;
  if (state.overrides.has(colKey)) {
    const v = state.overrides.get(colKey);
    if (_isNoMapValue(v) || !cdeByKey.has(v)) return null;
    return v;
  }
  return _topAiCdeKey(column);
};

// REC means the rendered CDE is one the model suggested (top OR any other AI
// candidate the user picked). OVR means the user chose something the AI did
// not suggest. Status is derived from the rendered CDE — not from the mere
// presence of an override — so a deliberate pick of a 2nd-ranked AI candidate
// reads as "aligned with the AI," not as a divergence.
const _effectiveStatus = (column) => {
  const cde = _effectiveCde(column);
  if (!cde) return TAG.NONE;
  return _aiCdeKeys(column).includes(cde) ? TAG.REC : TAG.OVR;
};

const _mappingKindFor = (column) => {
  const cde = _effectiveCde(column);
  if (!cde) return MAPPING_KIND.UNMAPPED;
  const meta = cdeByKey.get(cde);
  if (!meta) return MAPPING_KIND.UNMAPPED;
  return _isRenameOnly(meta.type) ? MAPPING_KIND.RENAME_ONLY : MAPPING_KIND.VALUE_MAPPING;
};

// Catalog (cdeByKey) is authoritative for CDE type on the list view, since
// per-column detail responses are loaded lazily on takeover open and not
// available for unopened rows. Delegates to _effectiveCde so user overrides
// (including override-to-NoMapping) are honored without special cases in the
// predicate. PV is the only type that triggers value-level harmonization;
// pass-through, numeric, and unmapped columns are all "values flow through
// unchanged" from the user's standpoint.
const _isWillChangeValuesColumn = (column) => {
  const cde = _effectiveCde(column);
  return !!cde && cdeByKey.get(cde)?.type === 'pv';
};

const _hasValues = (column) => {
  const colKey = column.column_key ?? column.column_name;
  const profile = state.payload?.column_profiles?.[colKey];
  if (!profile) return true;
  return (profile.total_distinct ?? profile.distinct_values?.length ?? 0) > 0;
};

const _passesFilters = (column) => {
  if (!state.filters.sources.has(_effectiveStatus(column))) return false;
  if (state.filters.willChangeOnly && !_isWillChangeValuesColumn(column)) return false;
  if (state.filters.hideEmpty && !_hasValues(column)) return false;
  return true;
};

const _overlapRatioFor = (column) => {
  const colKey = column.column_key ?? column.column_name;
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
    if (state.filterText && !(c.header ?? c.column_name ?? '').toLowerCase().includes(state.filterText)) {
      return false;
    }
    return true;
  });
  if (state.valueFitSort === VALUE_FIT_SORT.NONE) return filtered;
  // Stable sort. Rows with no numeric fit (pass-through, unmapped, or missing
  // ratio) always sink to the bottom regardless of direction so the actionable
  // ordered group stays at the top.
  const sign = state.valueFitSort === VALUE_FIT_SORT.ASC ? 1 : -1;
  return [...filtered].sort((a, b) => {
    const ka = _overlapRatioFor(a);
    const kb = _overlapRatioFor(b);
    const aHas = Number.isFinite(ka);
    const bHas = Number.isFinite(kb);
    if (!aHas && !bHas) return 0;
    if (!aHas) return 1;
    if (!bHas) return -1;
    return sign * (ka - kb);
  });
};

/* ─── Filter modal (centered popup, multi-select) ────────── */

const _isDefaultFilters = () =>
  state.filters.sources.size === 3 && !state.filters.willChangeOnly && !state.filters.hideEmpty;

const _activeFilterCount = () => {
  let n = 0;
  if (state.filters.sources.size < 3) n += 1;
  if (state.filters.willChangeOnly) n += 1;
  if (state.filters.hideEmpty) n += 1;
  return n;
};

const FILTER_ICON_SVG = `<svg class="filter-modal-trigger-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M1.5 2h13a.5.5 0 01.09.99L14.5 3h-.09L10 8.28V14a.5.5 0 01-.22.42l-2 1.33a.5.5 0 01-.78-.42V8.28L2.59 3H2.5l-.09-.01A.5.5 0 011.5 2z"/></svg>`;

const renderFilterTrigger = () => {
  const total = (state.payload?.columns ?? []).length;
  const shown = _filteredColumns().length;
  const isDefault = _isDefaultFilters();
  const activeN = _activeFilterCount();
  sourceFilterEl.innerHTML = isDefault
    ? `<button class="filter-modal-trigger" id="filterModalTrigger">
        ${FILTER_ICON_SVG}
        <span>Filters</span>
      </button>`
    : `<button class="filter-modal-trigger filter-modal-trigger--active" id="filterModalTrigger">
        ${FILTER_ICON_SVG}
        <span>Filters</span>
        <span class="filter-modal-trigger-badge">${activeN}</span>
        <span class="filter-modal-trigger-ratio">${shown} / ${total}</span>
      </button>`;
};

const _openFilterModal = () => {
  state.filterModalOpen = true;
  _renderFilterModal();
};

const _closeFilterModal = () => {
  state.filterModalOpen = false;
  const el = document.getElementById('filterModal');
  if (el) el.remove();
};

const _filterCounts = () => {
  const cols = state.payload?.columns ?? [];
  return {
    total: cols.length,
    [TAG.REC]: cols.filter((c) => _effectiveStatus(c) === TAG.REC).length,
    [TAG.OVR]: cols.filter((c) => _effectiveStatus(c) === TAG.OVR).length,
    [TAG.NONE]: cols.filter((c) => _effectiveStatus(c) === TAG.NONE).length,
    willChange: cols.filter(_isWillChangeValuesColumn).length,
    hasValues: cols.filter(_hasValues).length,
    empty: cols.filter((c) => !_hasValues(c)).length,
  };
};

const _sourceCheckHtml = (tag, label, icon, iconCls, count, desc) => {
  const checked = state.filters.sources.has(tag) ? 'checked' : '';
  return `
    <label class="fm-check" data-source="${tag}">
      <input type="checkbox" ${checked} />
      <span class="fm-check-box"></span>
      <div class="fm-check-content">
        <div class="fm-check-head">
          <span class="mapping-filter-icon ${iconCls}">${icon}</span>
          <span class="fm-check-label">${label}</span>
          <span class="fm-check-count">${count}</span>
        </div>
        <p class="fm-check-desc">${desc}</p>
      </div>
    </label>
  `;
};

const _toggleCheckHtml = (id, label, checked, count, desc) => `
  <label class="fm-check" data-toggle="${id}">
    <input type="checkbox" ${checked ? 'checked' : ''} />
    <span class="fm-check-box"></span>
    <div class="fm-check-content">
      <div class="fm-check-head">
        <span class="fm-check-label">${label}</span>
        <span class="fm-check-count">${count}</span>
      </div>
      <p class="fm-check-desc">${desc}</p>
    </div>
  </label>
`;

const _renderFilterModal = () => {
  let el = document.getElementById('filterModal');
  if (el) el.remove();
  const counts = _filterCounts();
  el = document.createElement('div');
  el.className = 'filter-modal';
  el.id = 'filterModal';
  el.innerHTML = `
    <div class="filter-modal-backdrop"></div>
    <div class="filter-modal-card">
      <header class="filter-modal-head">
        <h2 class="filter-modal-title">Filters</h2>
        <button class="filter-modal-close" data-action="close-filter" aria-label="Close">✕</button>
      </header>
      <div class="filter-modal-body">
        <section class="fm-section">
          <h3 class="fm-section-title">Mapping source</h3>
          ${_sourceCheckHtml(TAG.REC, 'AI Recommendation', STATUS[TAG.REC], STATUS_CLASS[TAG.REC], counts[TAG.REC], FILTER_DESC[SOURCE_FILTER.REC])}
          ${_sourceCheckHtml(TAG.OVR, 'Override', STATUS[TAG.OVR], STATUS_CLASS[TAG.OVR], counts[TAG.OVR], FILTER_DESC[SOURCE_FILTER.OVR])}
          ${_sourceCheckHtml(TAG.NONE, 'No Mapping', STATUS[TAG.NONE], STATUS_CLASS[TAG.NONE], counts[TAG.NONE], FILTER_DESC[SOURCE_FILTER.NONE])}
        </section>
        <section class="fm-section">
          <h3 class="fm-section-title">Harmonization</h3>
          ${_toggleCheckHtml('willChange', 'Only columns that will change values', state.filters.willChangeOnly, counts.willChange, FILTER_DESC[SOURCE_FILTER.WILL_CHANGE])}
        </section>
        <section class="fm-section">
          <h3 class="fm-section-title">Data content</h3>
          ${_toggleCheckHtml('hideEmpty', 'Hide empty columns', state.filters.hideEmpty, counts.empty, 'Exclude columns where all rows are blank or null — these columns have no data to harmonize.')}
        </section>
      </div>
      <footer class="filter-modal-foot">
        <button class="filter-modal-reset" data-action="reset-filters" ${_isDefaultFilters() ? 'disabled' : ''}>Reset all</button>
        <span class="filter-modal-showing">Showing <b>${_filteredColumns().length}</b> of <b>${counts.total}</b> columns</span>
      </footer>
    </div>
  `;
  document.body.appendChild(el);
  _bindFilterModalEvents(el);
};

const _bindFilterModalEvents = (el) => {
  el.querySelector('.filter-modal-backdrop').addEventListener('click', _closeFilterModal);
  el.querySelector('[data-action="close-filter"]').addEventListener('click', _closeFilterModal);
  el.querySelector('[data-action="reset-filters"]').addEventListener('click', () => {
    state.filters.sources = new Set([TAG.REC, TAG.OVR, TAG.NONE]);
    state.filters.willChangeOnly = false;
    state.filters.hideEmpty = false;
    _refreshAfterFilterChange();
  });
  el.querySelectorAll('[data-source]').forEach((label) => {
    label.querySelector('input').addEventListener('change', (e) => {
      const tag = label.dataset.source;
      if (e.target.checked) state.filters.sources.add(tag);
      else state.filters.sources.delete(tag);
      _refreshAfterFilterChange();
    });
  });
  el.querySelectorAll('[data-toggle]').forEach((label) => {
    label.querySelector('input').addEventListener('change', (e) => {
      const id = label.dataset.toggle;
      if (id === 'willChange') state.filters.willChangeOnly = e.target.checked;
      if (id === 'hideEmpty') state.filters.hideEmpty = e.target.checked;
      _refreshAfterFilterChange();
    });
  });
};

const _refreshAfterFilterChange = () => {
  renderRows();
  renderFilterTrigger();
  if (state.filterModalOpen) _renderFilterModal();
};

sourceFilterEl.addEventListener('click', (e) => {
  if (e.target.closest('#filterModalTrigger')) {
    if (state.filterModalOpen) _closeFilterModal();
    else _openFilterModal();
  }
});

colSearchEl.addEventListener('input', (e) => {
  state.filterText = e.target.value.toLowerCase();
  renderRows();
});

/* ─── Value-fit sort header ──────────────────────────────── */
const VALUE_FIT_SORT_GLYPH = {
  [VALUE_FIT_SORT.NONE]: '⇅',  // both arrows = unsorted, click to sort
  [VALUE_FIT_SORT.ASC]: '↑',   // ascending = worst fit first (the actionable end)
  [VALUE_FIT_SORT.DESC]: '↓',
};
const sortHeadEl = document.getElementById('valueFitSortBtn');

const renderValueFitSortHead = () => {
  if (!sortHeadEl) return;
  const arrowEl = sortHeadEl.querySelector('.mapping-list-head-sort-arrow');
  if (arrowEl) arrowEl.textContent = VALUE_FIT_SORT_GLYPH[state.valueFitSort];
  sortHeadEl.classList.toggle('mapping-list-head-sort--active', state.valueFitSort !== VALUE_FIT_SORT.NONE);
};

const _cycleValueFitSort = () => {
  // None → Asc → Desc → None. Asc is hit first because the productive use of
  // the sort is to surface the worst fits (the rows most likely to be wrong).
  const next = {
    [VALUE_FIT_SORT.NONE]: VALUE_FIT_SORT.ASC,
    [VALUE_FIT_SORT.ASC]: VALUE_FIT_SORT.DESC,
    [VALUE_FIT_SORT.DESC]: VALUE_FIT_SORT.NONE,
  }[state.valueFitSort];
  state.valueFitSort = next;
  renderValueFitSortHead();
  renderRows();
};

if (sortHeadEl) sortHeadEl.addEventListener('click', _cycleValueFitSort);

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
  const status = _effectiveStatus(col);
  const colKey = col.column_key ?? col.column_name;
  const override = state.overrides.get(colKey);
  let target;
  if (_isNoMapValue(override)) {
    target = `<span class="mapping-row-target mapping-row-target--none">No Mapping</span>`;
  } else {
    const cde = _effectiveCde(col);
    target = cde
      ? `<span class="mapping-row-target" title="${_escAttr(cde)}">${_escHtml(cde)}</span>`
      : `<span class="mapping-row-target mapping-row-target--empty">— no mapping yet</span>`;
  }
  const fit = _overlapCellHtml(col);
  return `
    <div class="mapping-row" data-key="${_escAttr(colKey)}">
      <div class="mapping-row-col" title="${_escAttr(col.header ?? col.column_name)}">${_escHtml(col.header ?? col.column_name)}</div>
      <div class="mapping-row-status ${STATUS_CLASS[status]}" data-fast-tooltip="${_escAttr(STATUS_TIP[status])}">${STATUS[status]}</div>
      ${target}
      ${fit}
      <div class="mapping-row-chev">›</div>
    </div>
  `;
};

const _overlapCellHtml = (col) => {
  const kind = _mappingKindFor(col);
  if (kind === MAPPING_KIND.RENAME_ONLY) {
    const cdeType = cdeByKey.get(_effectiveCde(col))?.type;
    if (cdeType === 'passthrough') {
      // Passthrough CDEs have no PV list — a 0% match would misleadingly imply
      // bad fit. Glyph is shared with the filter chip and the takeover conform
      // pill so the same symbol means "pass-through" across surfaces.
      return `
        <div class="mapping-row-fit mapping-row-fit--passthrough" data-fast-tooltip="${_escAttr(PASSTHROUGH_FIT_TIP)}" tabindex="0">
          <span aria-hidden="true">${PASSTHROUGH_GLYPH}</span>
          <span class="sr-only">${_escHtml(PASSTHROUGH_FIT_TIP)}</span>
        </div>
      `;
    }
    return `<div class="mapping-row-fit mapping-row-fit--ratio" title="No permissible values to compare">0%</div>`;
  }
  if (kind === MAPPING_KIND.VALUE_MAPPING) {
    const ratio = _overlapRatioFor(col);
    if (ratio !== null) {
      return `<div class="mapping-row-fit mapping-row-fit--ratio" title="Distinct value overlap">${_formatRatio(ratio)}</div>`;
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
  state.takeoverKey = null;
  state.pickerOpen = false;
  takeoverEl.classList.add('hidden');
  document.body.classList.remove('lock');
};

const navigate = async (delta) => {
  const list = _filteredColumns();
  const i = list.findIndex((c) => (c.column_key ?? c.column_name) === state.takeoverKey);
  const ni = i + delta;
  if (ni < 0 || ni >= list.length) return;
  const next = list[ni];
  state.takeoverKey = next.column_key ?? next.column_name;
  state.pickerOpen = false;
  renderTakeover();
  await _ensureColumnDetail(state.takeoverKey);
  if (state.takeoverKey === (next.column_key ?? next.column_name)) renderTakeover();
};

const _ensureColumnDetail = async (colKey) => {
  if (state.detailByColumn.has(colKey)) return;
  const col = (state.payload?.columns ?? []).find((c) => (c.column_key ?? c.column_name) === colKey);
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

const renderTakeover = () => {
  const colKey = state.takeoverKey;
  if (!colKey) { closeTakeover(); return; }
  const col = (state.payload?.columns ?? []).find((c) => (c.column_key ?? c.column_name) === colKey);
  if (!col) { closeTakeover(); return; }

  const list = _filteredColumns();
  const idx = list.findIndex((c) => (c.column_key ?? c.column_name) === colKey);
  const status = _effectiveStatus(col);
  const cde = _effectiveCde(col);
  const detail = state.detailByColumn.get(colKey) ?? null;
  const profile = detail?.profile ?? state.payload.column_profiles?.[colKey] ?? null;
  // Per-value PV match lookup. Strict equality (case/whitespace-sensitive) per the
  // "all character differences are semantically significant" domain rule.
  const cdeType = detail?.cde_types?.[cde] ?? cdeByKey.get(cde)?.type ?? 'pv';
  const pvSet = (cde && cdeType === 'pv' && Array.isArray(detail?.selected_pvs))
    ? new Set(detail.selected_pvs)
    : null;

  takeoverCardEl.innerHTML = `
    <header class="takeover-head">
      <div class="takeover-head-left">
        <span class="takeover-head-status ${STATUS_CLASS[status]}">${STATUS[status]}</span>
        <h2 class="takeover-head-name" title="${_escAttr(col.header ?? col.column_name)}">${_escHtml(col.header ?? col.column_name)}</h2>
      </div>
      <div class="takeover-head-center">${_conformPillHtml(col, cde, detail, profile)}</div>
      <div class="takeover-head-right">
        <span class="takeover-counter">${idx + 1} of ${list.length}</span>
        <button class="takeover-btn" data-action="prev" ${idx <= 0 ? 'disabled' : ''}>← Prev</button>
        <button class="takeover-btn" data-action="next" ${idx >= list.length - 1 ? 'disabled' : ''}>Next →</button>
        <button class="takeover-btn takeover-btn--icon takeover-btn--close" data-action="close" aria-label="Close">✕</button>
      </div>
    </header>
    <div class="takeover-body">
      <section class="takeover-pane takeover-pane--data">${_dataPaneHtml(col, profile, pvSet)}</section>
      <section class="takeover-pane takeover-pane--target">${_targetPaneHtml(col, cde, detail)}</section>
    </div>
  `;
  _bindTakeoverEvents(col);
};

const _conformPillHtml = (col, cde, detail, profile) => {
  if (!cde) return '';
  const totalDistinct = profile?.total_distinct ?? profile?.distinct_values?.length ?? 0;
  const cdeType = detail?.cde_types?.[cde] ?? cdeByKey.get(cde)?.type ?? 'pv';
  const matchN = detail?.match_counts?.[cde] ?? 0;

  if (cdeType === 'passthrough') {
    // No tooltip / help cursor — the inline label already says everything.
    return `
      <div class="conform-pill conform-pill--neutral">
        <span class="ico">${PASSTHROUGH_GLYPH}</span>
        <span>Pass-through field — values will not be changed</span>
      </div>
    `;
  }
  if (cdeType === 'numeric') {
    const tip = "Distinct values in your column that parse as numbers.";
    if (matchN > 0) {
      return `
        <div class="conform-pill" title="${tip}">
          <span class="ico">✓</span>
          <span><b>${matchN.toLocaleString()}</b> of <b>${totalDistinct.toLocaleString()}</b> distinct values are numeric</span>
        </div>
      `;
    }
    return `
      <div class="conform-pill conform-pill--warning" title="${tip}">
        <span class="ico">⚠</span>
        <span><b>0</b> of <b>${totalDistinct.toLocaleString()}</b> distinct values are numeric</span>
      </div>
    `;
  }
  if (matchN > 0) {
    return `
      <div class="conform-pill" title="${MATCH_TIP}">
        <span class="ico">✓</span>
        <span><b>${matchN.toLocaleString()}</b> of <b>${totalDistinct.toLocaleString()}</b> distinct values match</span>
      </div>
    `;
  }
  // PV with zero matches — surface as a warning so users notice the bad fit
  if (detail) {
    return `
      <div class="conform-pill conform-pill--warning" title="${MATCH_TIP}">
        <span class="ico">⚠</span>
        <span><b>0</b> of <b>${totalDistinct.toLocaleString()}</b> distinct values match</span>
      </div>
    `;
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

const _targetPaneHtml = (col, cde, detail) => {
  const colKey = col.column_key ?? col.column_name;
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
      <span class="cde-picker-caret" style="margin-left:auto">▾</span>
    `;
  }

  const head = `
    <div class="takeover-pane-head">
      <h3 class="takeover-pane-title">Target standard</h3>
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
  const colKey = state.takeoverKey;
  const col = (state.payload?.columns ?? []).find((c) => (c.column_key ?? c.column_name) === colKey);
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

  const wrap = document.getElementById('pickerWrap');
  const dd = document.createElement('div');
  dd.className = 'dropdown';
  dd.id = 'pickerDropdown';
  dd.innerHTML = `
    <div class="dd-search"><input type="search" id="ddSearch" placeholder="Filter standards by name or description…" autocomplete="off" /></div>
    <div class="dd-list" id="ddList">${_renderDropdownItems(aiOptions, opts, '')}</div>
  `;
  wrap.appendChild(dd);
  state.pickerOpen = true;
  setTimeout(() => dd.querySelector('input').focus(), 0);
  dd.querySelector('input').addEventListener('input', (e) => {
    document.getElementById('ddList').innerHTML = _renderDropdownItems(aiOptions, opts, e.target.value);
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

const _renderDropdownItems = (aiOptions, opts, q) => {
  const lq = q.toLowerCase();
  const matches = (c) => !lq
    || (c.label || c.key).toLowerCase().includes(lq)
    || (c.description || '').toLowerCase().includes(lq);
  const topItems = [];
  // Render every AI candidate as its own ✦ AI rec row, top-first (similarity
  // order). Search filter still applies — non-matching candidates drop out.
  for (const ai of aiOptions) {
    if (matches(ai)) topItems.push(_optHtml(ai, 'ai'));
  }
  if (!q) {
    topItems.push(_optHtml({ key: NO_MAP_OPTION_VALUE, label: NO_MAPPING_OPTION, description: NO_MAP_DESC }, 'none'));
  }
  const valueOptions = opts.filter((o) => !_isRenameOnly(o.type) && matches(o));
  const renameOnlyOptions = opts.filter((o) => _isRenameOnly(o.type) && matches(o));
  renameOnlyOptions.sort((a, b) => a.label.localeCompare(b.label));
  const sections = [
    ...topItems,
    topItems.length ? `<div class="dd-divider"></div>` : '',
    _dropdownSectionHtml(VALUE_MAPPING_SECTION_LABEL, valueOptions, 'alt'),
    _dropdownSectionHtml(RENAME_ONLY_SECTION_LABEL, renameOnlyOptions, 'alt rename-only'),
  ].filter(Boolean);
  if (!sections.length || (valueOptions.length === 0 && renameOnlyOptions.length === 0 && topItems.length === 0)) {
    return `<div class="dd-empty">No standards match "${_escHtml(q)}"</div>`;
  }
  return sections.join('');
};

const _dropdownSectionHtml = (label, options, kind) => {
  if (!options.length) return '';
  return `
    <section class="dd-section ${kind.includes('rename-only') ? 'dd-section--rename-only' : ''}">
      <div class="dd-section-label">${_escHtml(label)}</div>
      ${options.map((o) => _optHtml(o, kind)).join('')}
    </section>
  `;
};

const _optHtml = (c, kind) => {
  const showMatch = kind !== 'none';
  const matchCount = _isRenameOnly(c.type) ? 0 : c.matches ?? 0;
  const matchTip = _isRenameOnly(c.type) ? 'No permissible values to compare.' : MATCH_TIP;
  const matchHtml = showMatch ? `
    <span class="count ${matchCount > 0 ? 'high' : 'zero'}" title="${matchTip}">
      ${matchCount > 0 ? `<b>${matchCount.toLocaleString()}</b> matches` : '0 matches'}
    </span>` : '';
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
  const col = (state.payload?.columns ?? []).find((c) => (c.column_key ?? c.column_name) === colKey);
  const aiKey = col ? _topAiCdeKey(col) : null;
  const normalizedValue = _normalizeOverrideValue(value);
  if (aiKey && normalizedValue === aiKey) {
    state.overrides.delete(colKey);
  } else {
    state.overrides.set(colKey, normalizedValue);
  }
  _persistOverrides();
};

/* ─── Event wiring ───────────────────────────────────────── */
const _bindTakeoverEvents = (col) => {
  takeoverCardEl.querySelectorAll('[data-action]').forEach((el) => {
    el.addEventListener('click', () => {
      const action = el.dataset.action;
      if (action === 'close') closeTakeover();
      else if (action === 'prev') void navigate(-1);
      else if (action === 'next') void navigate(1);
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
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (state.filterModalOpen) { _closeFilterModal(); return; }
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

/* ─── Harmonize submission (unchanged shape) ─────────────── */
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

  const overrides = {};
  for (const [k, v] of state.overrides.entries()) overrides[k] = v;
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
    const colKey = col.column_key ?? col.column_name;
    if (!state.overrides.has(colKey)) return false;
    const cde = _effectiveCde(col);
    const meta = cde ? cdeByKey.get(cde) : null;
    return meta && !_isRenameOnly(meta.type) && !state.detailByColumn.has(colKey);
  });
  await Promise.all(overriddenValueRows.map(async (col) => {
    const colKey = col.column_key ?? col.column_name;
    await _ensureColumnDetail(colKey);
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
  renderFilterTrigger();
  renderRows();
  void _ensureOverrideDetails();
};

/* ─── Utils ──────────────────────────────────────────────── */
function _formatRatio(ratio) {
  const pct = ratio * 100;
  if (Number.isInteger(pct)) return `${pct}%`;
  return `${pct.toFixed(1)}%`;
}

function _escHtml(s) {
  return String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function _escAttr(s) {
  return String(s ?? '').replace(/["'&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

void _init();
