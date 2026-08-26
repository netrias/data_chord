/** Stage 5 presents the current downloadable output and its separate decision history. */

import {
  initStepInstruction,
  setActiveStage,
  initNavigationEvents,
  isSafeRelativeUrl,
} from '/assets/shared/step-instruction-ui.js';
import { markAfterPaint, markTiming, measureTiming } from '/assets/shared/performance-timing.js';
import {
  isReviewStateRecovery,
  readResponseDetail,
  renderRecoveryError,
} from '/assets/shared/recovery-error.js';
import {
  clearWorkflowSession,
  isValidFileId,
  isSafeFilename,
} from '/assets/shared/storage-keys.js';

const _DEFAULT_SUMMARY_ENDPOINT = '/stage-5/summary';
const _DEFAULT_DOWNLOAD_ENDPOINT = '/stage-5/download';
const _DEFAULT_STAGE_FOUR_URL = '/stage-4';
const _DEFAULT_ZIP_FILENAME = 'harmonized_data.zip';
const _REVOKE_DELAY_MS = 100;
const _FILTERS = new Set(['needs_attention', 'changed', 'reviewer', 'all']);
const _SORT_KEYS = new Set(['column', 'original_value', 'final_value']);

const _config = window.stageFiveConfig ?? {};
const _summaryEndpoint = _config.summaryEndpoint ?? _DEFAULT_SUMMARY_ENDPOINT;
const _downloadEndpoint = _config.downloadEndpoint ?? _DEFAULT_DOWNLOAD_ENDPOINT;
const _stageFourUrl = _config.stageFourUrl ?? _DEFAULT_STAGE_FOUR_URL;

const _downloadBtn = document.getElementById('downloadResults');
const _downloadError = document.getElementById('downloadError');
const _certificateTitle = document.getElementById('certificateTitle');
const _datasetMetadata = document.getElementById('datasetMetadata');
const _certificateStatus = document.getElementById('certificateStatus');
const _summaryGrid = document.getElementById('summaryGrid');
const _startOverAction = document.getElementById('uploadNavAction');
const _startOverButton = document.getElementById('startOverButton');
const _startOverDialog = document.getElementById('startOverDialog');
const _startOverCancel = document.getElementById('startOverCancel');
const _startOverConfirm = document.getElementById('startOverConfirm');
const _changesTableSection = document.getElementById('changesTableSection');
const _changesTableBody = document.getElementById('changesTableBody');
const _changesTable = document.getElementById('changesTable');
const _mappingsFilter = document.getElementById('mappingsFilter');
const _conformanceWarningDialog = document.getElementById('conformanceWarningDialog');
const _conformanceWarningSummary = document.getElementById('conformanceWarningSummary');
const _conformanceWarningItems = document.getElementById('conformanceWarningItems');
const _conformanceReturnButton = document.getElementById('conformanceReturnButton');
const _conformanceProceedButton = document.getElementById('conformanceProceedButton');

const _state = {
  fileId: null,
  termMappings: [],
  sortColumn: null,
  sortDirection: 'asc',
  nonConformantCount: 0,
  filter: 'changed',
};

const _safeCount = (value) => {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.trunc(count) : 0;
};

const _extractFilename = (disposition) => {
  let filename = null;
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/);
  if (utf8Match) {
    try {
      filename = decodeURIComponent(utf8Match[1]);
    } catch {
      filename = null;
    }
  }
  if (!filename) {
    filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? null;
  }
  return filename && isSafeFilename(filename) ? filename : _DEFAULT_ZIP_FILENAME;
};

const _setDownloadButtonState = (isLoading) => {
  if (!_downloadBtn) return;
  _downloadBtn.disabled = isLoading;
  const textTarget = _downloadBtn.querySelector('.btn-3d-front') ?? _downloadBtn;
  textTarget.textContent = isLoading ? 'Preparing download...' : 'Download data';
};

const _triggerBrowserDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), _REVOKE_DELAY_MS);
};

const _showError = (message, includeRecoveryLink = false) => {
  if (!_downloadError) return;
  if (includeRecoveryLink) {
    renderRecoveryError(_downloadError, message, _state.fileId);
  } else {
    _downloadError.textContent = message;
  }
  _downloadError.classList.remove('hidden');
};

const _hideError = () => {
  _downloadError?.classList.add('hidden');
};

const _loadSourceContext = () => {
  const fileId = new URLSearchParams(window.location.search).get('file_id');
  return isValidFileId(fileId) ? { fileId } : null;
};

const _showStartOverAction = () => {
  _startOverAction?.classList.remove('hidden');
};

const _openStartOverDialog = () => {
  if (!_startOverDialog || _startOverDialog.hasAttribute('open')) return;
  if (_startOverDialog.showModal) {
    _startOverDialog.showModal();
  } else {
    _startOverDialog.setAttribute('open', '');
  }
};

const _closeStartOverDialog = () => {
  if (!_startOverDialog) return;
  if (_startOverDialog.close) {
    _startOverDialog.close();
  } else {
    _startOverDialog.removeAttribute('open');
  }
};

const _confirmStartOver = () => {
  const target = _startOverButton?.dataset.startOverTarget;
  if (!isSafeRelativeUrl(target)) return;
  clearWorkflowSession();
  window.location.assign(target);
};

const _closeConformanceWarning = () => {
  if (!_conformanceWarningDialog) return;
  if (_conformanceWarningDialog.close) {
    _conformanceWarningDialog.close();
  } else {
    _conformanceWarningDialog.removeAttribute('open');
  }
};

const _validConformanceItems = (items) => (
  Array.isArray(items)
    ? items.filter((item) => (
      typeof item?.column === 'string'
      && Number.isInteger(item?.source_column_index)
      && item.source_column_index >= 0
      && typeof item?.value === 'string'
    ))
    : []
);

const _returnToReview = () => {
  if (!_state.fileId || !isSafeRelativeUrl(_stageFourUrl)) return;
  window.location.assign(`${_stageFourUrl}?file_id=${encodeURIComponent(_state.fileId)}`);
};

const _showConformanceWarning = (items) => {
  if (
    !_conformanceWarningDialog
    || !_conformanceWarningSummary
    || !_conformanceWarningItems
    || items.length === 0
  ) return;

  const grouped = new Map();
  for (const item of items) {
    const group = grouped.get(item.source_column_index) ?? {
      column: item.column,
      values: [],
    };
    group.values.push(item.value);
    grouped.set(item.source_column_index, group);
  }
  const labelCounts = new Map();
  for (const { column } of grouped.values()) {
    labelCounts.set(column, (labelCounts.get(column) ?? 0) + 1);
  }

  _conformanceWarningSummary.textContent = items.length === 1
    ? '1 value does not match the permissible value set for its mapped ontology.'
    : `${items.length} values do not match the permissible value set for their mapped ontologies.`;
  _conformanceWarningItems.replaceChildren();
  for (const [sourceColumnIndex, { column, values }] of grouped) {
    const group = document.createElement('section');
    group.className = 'conformance-warning-group';
    const title = document.createElement('h3');
    const columnLabel = labelCounts.get(column) > 1
      ? `${column} · Column ${sourceColumnIndex + 1}`
      : column;
    title.textContent = `${columnLabel} (${values.length})`;
    group.appendChild(title);
    for (const value of values) {
      const row = document.createElement('p');
      row.className = 'conformance-warning-value';
      row.textContent = `"${value}"`;
      group.appendChild(row);
    }
    _conformanceWarningItems.appendChild(group);
  }

  if (_conformanceWarningDialog.showModal) {
    _conformanceWarningDialog.showModal();
  } else {
    _conformanceWarningDialog.setAttribute('open', '');
  }
};

const _handleDownload = async () => {
  if (!_state.fileId) {
    _showError('Unable to locate this file. Restart the harmonization workflow.');
    return;
  }

  _hideError();
  _setDownloadButtonState(true);
  markTiming('stage5.download.start');
  try {
    const response = await fetch(_downloadEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: _state.fileId }),
    });
    markTiming('stage5.download.response', { status: response.status });
    measureTiming('stage5.download.request', 'stage5.download.start', 'stage5.download.response');
    if (!response.ok) {
      const detail = await readResponseDetail(response, 'Download failed. Try again.');
      throw Object.assign(new Error(detail), { status: response.status });
    }

    const blob = await response.blob();
    markTiming('stage5.download.blob_ready', { bytes: blob.size });
    measureTiming('stage5.download.blob', 'stage5.download.response', 'stage5.download.blob_ready', {
      bytes: blob.size,
    });
    _triggerBrowserDownload(blob, _extractFilename(response.headers.get('Content-Disposition') ?? ''));
    _showStartOverAction();
  } catch (error) {
    console.error('Download failed:', error);
    const message = error.message || 'Download failed. Try again.';
    _showError(message, isReviewStateRecovery(message));
  } finally {
    _setDownloadButtonState(false);
    await markAfterPaint('stage5.download.usable');
    measureTiming('stage5.download_to_usable', 'stage5.download.start', 'stage5.download.usable');
  }
};

const _datasetDetails = (dataset) => {
  const parts = [];
  const model = [dataset?.data_model_key, dataset?.external_version_number]
    .filter(Boolean)
    .join(' ');
  if (model) parts.push(model);
  if (dataset?.tabular_format) parts.push(String(dataset.tabular_format).toUpperCase());
  return parts.join(' · ');
};

const _renderCertificate = (dataset, nonConformantCount) => {
  if (_certificateTitle) {
    _certificateTitle.textContent = dataset?.filename || 'Harmonized data';
  }
  if (_datasetMetadata) {
    _datasetMetadata.textContent = _datasetDetails(dataset) || 'Ready to download';
  }
  if (!_certificateStatus) return;
  if (nonConformantCount > 0) {
    _certificateStatus.className = 'certificate-status certificate-status--attention';
    _certificateStatus.textContent = `${nonConformantCount.toLocaleString()} ${nonConformantCount === 1 ? 'value needs' : 'values need'} review`;
  } else {
    _certificateStatus.className = 'certificate-status certificate-status--ready';
    _certificateStatus.textContent = 'Ready to download';
  }
};

const _appendImpactMetric = (ledger, key, value, label) => {
  const metric = document.createElement('div');
  metric.className = 'change-impact__metric';
  metric.dataset.impactMetric = key;

  const term = document.createElement('dt');
  term.className = 'change-impact__label';
  term.textContent = label;
  metric.appendChild(term);

  const count = document.createElement('dd');
  count.className = 'change-impact__value';
  count.textContent = value.toLocaleString();
  metric.appendChild(count);
  ledger.appendChild(metric);
};

const _renderSummary = (columnSummaries) => {
  if (!_summaryGrid) return;
  const totals = columnSummaries.reduce(
    (aggregate, column) => ({
      totalValues: aggregate.totalValues + _safeCount(column.changed_rows),
      uniqueValues: aggregate.uniqueValues + _safeCount(column.changed_distinct_values),
      manualValues: aggregate.manualValues + _safeCount(column.reviewer_edited_rows),
    }),
    { totalValues: 0, uniqueValues: 0, manualValues: 0 },
  );

  const ledger = document.createElement('dl');
  ledger.className = 'change-impact';
  ledger.setAttribute('aria-label', 'Changes');
  _appendImpactMetric(ledger, 'unique_values', totals.uniqueValues, 'Unique values changed');
  _appendImpactMetric(ledger, 'total_values', totals.totalValues, 'Total values changed');
  _appendImpactMetric(ledger, 'manual_values', totals.manualValues, 'Values manually changed');
  _summaryGrid.replaceChildren(ledger);
};

const _showSummaryError = (message, includeRecoveryLink = false) => {
  if (!_summaryGrid) return;
  _summaryGrid.replaceChildren();
  const error = document.createElement('p');
  error.className = 'summary-empty';
  if (includeRecoveryLink) {
    renderRecoveryError(error, message, _state.fileId);
  } else {
    error.textContent = message;
  }
  _summaryGrid.appendChild(error);
};

const _sourceLabel = (source) => {
  switch (source) {
    case 'original':
    case 'source': return 'Source value';
    case 'data_chord':
    case 'ai':
      return 'Data Chord';
    case 'reviewer':
    case 'user': return 'Reviewer';
    default: return 'Unknown';
  }
};

const _reviewStatusLabel = (status) => {
  switch (status) {
    case 'clear': return 'Matches approved values';
    case 'needs_attention': return 'Needs review';
    case 'not_checked': return 'Not checked against approved values';
    default: return 'Review status unavailable';
  }
};

const _formatTimestamp = (isoString) => {
  if (!isoString) return null;
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
};

const _createHistoryStep = (step) => {
  const item = document.createElement('li');
  item.className = 'history-step';
  item.dataset.source = step.source ?? '';

  const value = document.createElement('p');
  value.className = 'history-step__value';
  value.textContent = step.action === 'clear'
    ? 'Reviewer cleared the choice'
    : `"${step.value ?? ''}"`;
  item.appendChild(value);

  const detail = document.createElement('p');
  detail.className = 'history-step__detail';
  const actor = _sourceLabel(step.source);
  const timestamp = _formatTimestamp(step.timestamp);
  detail.textContent = timestamp ? `${actor} · ${timestamp}` : actor;
  item.appendChild(detail);

  const conformance = document.createElement('p');
  conformance.className = step.review_status === 'needs_attention'
    ? 'history-step__status history-step__status--attention'
    : 'history-step__status';
  conformance.textContent = _reviewStatusLabel(step.review_status);
  item.appendChild(conformance);
  return item;
};

const _showHistoryDialog = (mapping, trigger) => {
  const dialog = document.createElement('dialog');
  dialog.className = 'history-dialog';
  dialog.setAttribute('aria-labelledby', 'historyDialogTitle');

  const content = document.createElement('div');
  content.className = 'history-dialog-content';

  const header = document.createElement('header');
  header.className = 'history-dialog-header';
  const eyebrow = document.createElement('p');
  eyebrow.className = 'history-dialog-eyebrow';
  eyebrow.textContent = mapping.column ?? 'Source column';
  header.appendChild(eyebrow);
  const title = document.createElement('h2');
  title.id = 'historyDialogTitle';
  title.className = 'history-dialog-title';
  title.textContent = 'Current Output and Decision History';
  header.appendChild(title);
  content.appendChild(header);

  const current = document.createElement('section');
  current.className = 'history-current';
  const currentTitle = document.createElement('h3');
  currentTitle.textContent = 'Current output';
  current.appendChild(currentTitle);

  const values = document.createElement('div');
  values.className = 'history-current__values';
  for (const [label, value] of [
    ['Source', mapping.original_value],
    ['Output', mapping.final_value],
  ]) {
    const group = document.createElement('div');
    const labelElement = document.createElement('span');
    labelElement.className = 'history-current__label';
    labelElement.textContent = label;
    group.appendChild(labelElement);
    const valueElement = document.createElement('span');
    valueElement.className = 'history-current__value';
    valueElement.textContent = `"${value ?? ''}"`;
    group.appendChild(valueElement);
    values.appendChild(group);
  }
  current.appendChild(values);

  const currentMeta = document.createElement('p');
  currentMeta.className = mapping.review_status === 'needs_attention'
    ? 'history-current__meta history-current__meta--attention'
    : 'history-current__meta';
  currentMeta.textContent = `${_sourceLabel(mapping.final_value_source)} · ${_reviewStatusLabel(mapping.review_status)}`;
  current.appendChild(currentMeta);
  content.appendChild(current);

  const historySection = document.createElement('section');
  historySection.className = 'history-dialog-body';
  const historyTitle = document.createElement('h3');
  historyTitle.textContent = 'Decision history';
  historySection.appendChild(historyTitle);
  const history = document.createElement('ol');
  history.className = 'history-timeline';
  const steps = Array.isArray(mapping.history) ? mapping.history : [];
  if (steps.length === 0) {
    const empty = document.createElement('li');
    empty.className = 'history-empty';
    empty.textContent = 'No recorded decisions are available.';
    history.appendChild(empty);
  } else {
    for (const step of steps) history.appendChild(_createHistoryStep(step));
  }
  historySection.appendChild(history);
  content.appendChild(historySection);

  const footer = document.createElement('footer');
  footer.className = 'history-dialog-footer';
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'ghost-btn';
  close.textContent = 'Close';
  close.addEventListener('click', () => dialog.close());
  footer.appendChild(close);
  content.appendChild(footer);
  dialog.appendChild(content);

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => {
    dialog.remove();
    trigger?.focus();
  });
  document.body.appendChild(dialog);
  dialog.showModal();
};

const _appendCell = (row, text, className = '') => {
  const cell = document.createElement('td');
  cell.textContent = text;
  if (className) cell.className = className;
  row.appendChild(cell);
  return cell;
};

const _renderTableRows = (mappings) => {
  if (!_changesTableBody) return;
  _changesTableBody.replaceChildren();

  if (mappings.length === 0) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.className = 'changes-table-empty';
    cell.textContent = 'No values match this view.';
    row.appendChild(cell);
    _changesTableBody.appendChild(row);
    return;
  }

  for (const mapping of mappings) {
    const row = document.createElement('tr');
    row.dataset.historyRow = '';
    row.tabIndex = 0;
    row.setAttribute(
      'aria-label',
      `View decision history for ${mapping.column ?? 'source column'}`,
    );
    if (mapping.review_status === 'needs_attention') row.classList.add('needs-attention');
    _appendCell(row, mapping.column ?? 'Unnamed column', 'changes-table__column');
    _appendCell(row, mapping.original_value ?? '');

    const outputCell = _appendCell(row, mapping.final_value ?? '', 'changes-table__output');
    if (mapping.review_status === 'needs_attention') {
      const status = document.createElement('span');
      status.className = 'mapping-status mapping-status--attention';
      status.textContent = 'Needs review';
      outputCell.appendChild(status);
    }

    _appendCell(row, _sourceLabel(mapping.final_value_source));
    _appendCell(row, _safeCount(mapping.row_count).toLocaleString(), 'changes-table__count');
    const openHistory = () => _showHistoryDialog(mapping, row);
    row.addEventListener('click', openHistory);
    row.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      openHistory();
    });
    _changesTableBody.appendChild(row);
  }
};

const _getFilteredMappings = () => {
  switch (_state.filter) {
    case 'needs_attention':
      return _state.termMappings.filter((mapping) => mapping.review_status === 'needs_attention');
    case 'changed':
      return _state.termMappings.filter((mapping) => mapping.is_changed === true);
    case 'reviewer':
      return _state.termMappings.filter((mapping) => mapping.final_value_source === 'reviewer');
    case 'all':
    default:
      return _state.termMappings;
  }
};

const _sortMappings = (mappings, column, direction) => [...mappings].sort((left, right) => {
  const leftValue = String(left[column] ?? '');
  const rightValue = String(right[column] ?? '');
  const comparison = leftValue.localeCompare(rightValue, undefined, { sensitivity: 'base' });
  return direction === 'asc' ? comparison : -comparison;
});

const _visibleMappings = () => {
  const mappings = _getFilteredMappings();
  return _state.sortColumn
    ? _sortMappings(mappings, _state.sortColumn, _state.sortDirection)
    : mappings;
};

const _updateFilterButtons = () => {
  if (!_mappingsFilter) return;
  for (const button of _mappingsFilter.querySelectorAll('[data-filter]')) {
    const isActive = button.dataset.filter === _state.filter;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-pressed', String(isActive));
  }
};

const _updateSortButtons = () => {
  if (!_changesTable) return;
  for (const button of _changesTable.querySelectorAll('[data-sort-key]')) {
    const key = button.dataset.sortKey;
    const header = button.closest('th');
    const indicator = button.querySelector('.sort-indicator');
    const active = key === _state.sortColumn;
    header?.setAttribute('aria-sort', active ? `${_state.sortDirection}ending` : 'none');
    if (indicator) indicator.textContent = active ? (_state.sortDirection === 'asc' ? '▲' : '▼') : '↕';
  }
};

const _renderMappingView = () => {
  _updateFilterButtons();
  _updateSortButtons();
  _renderTableRows(_visibleMappings());
};

const _applyFilter = (filter) => {
  if (!_FILTERS.has(filter)) return;
  _state.filter = filter;
  _renderMappingView();
};

const _handleSort = (key) => {
  if (!_SORT_KEYS.has(key)) return;
  if (_state.sortColumn === key) {
    _state.sortDirection = _state.sortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    _state.sortColumn = key;
    _state.sortDirection = 'asc';
  }
  _renderMappingView();
};

const _setupMappingControls = () => {
  for (const button of _mappingsFilter?.querySelectorAll('[data-filter]') ?? []) {
    button.addEventListener('click', () => _applyFilter(button.dataset.filter));
  }
  for (const button of _changesTable?.querySelectorAll('[data-sort-key]') ?? []) {
    button.addEventListener('click', () => _handleSort(button.dataset.sortKey));
  }
};

const _renderMappings = (termMappings) => {
  if (!_changesTableSection) return;
  _state.termMappings = Array.isArray(termMappings) ? termMappings : [];
  _state.sortColumn = null;
  _state.sortDirection = 'asc';
  _renderMappingView();
  _changesTableSection.classList.remove('hidden');
};

const _fetchSummary = async () => {
  const context = _loadSourceContext();
  if (!context) {
    _showSummaryError('Unable to locate this harmonization workflow.');
    return;
  }
  _state.fileId = context.fileId;

  try {
    markTiming('stage5.summary.fetch.start');
    const response = await fetch(_summaryEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: context.fileId }),
    });
    markTiming('stage5.summary.fetch.response', { status: response.status });
    measureTiming('stage5.summary.request', 'stage5.summary.fetch.start', 'stage5.summary.fetch.response');
    if (!response.ok) {
      const detail = await readResponseDetail(response, 'Unable to load summary.');
      throw Object.assign(new Error(detail), { status: response.status });
    }

    const data = await response.json();
    markTiming('stage5.summary.fetch.parsed', {
      column_count: data.column_summaries?.length ?? 0,
      term_mapping_count: data.term_mappings?.length ?? 0,
    });
    measureTiming('stage5.summary.parse', 'stage5.summary.fetch.response', 'stage5.summary.fetch.parsed');
    markTiming('stage5.summary.render.start');

    const conformanceItems = _validConformanceItems(data.non_conformant_items);
    _state.nonConformantCount = conformanceItems.length;
    _state.filter = _state.nonConformantCount > 0 ? 'needs_attention' : 'changed';
    _renderCertificate(data.dataset, _state.nonConformantCount);
    _renderSummary(data.column_summaries ?? []);
    _renderMappings(data.term_mappings ?? []);
    _showConformanceWarning(conformanceItems);

    markTiming('stage5.summary.render.dom_complete');
    measureTiming('stage5.summary.render.dom', 'stage5.summary.render.start', 'stage5.summary.render.dom_complete', {
      term_mapping_count: data.term_mappings?.length ?? 0,
    });
    await markAfterPaint('stage5.usable');
    measureTiming(
      'stage5.summary.render_to_usable',
      'stage5.summary.render.dom_complete',
      'stage5.usable',
    );
    measureTiming('stage5.init_to_usable', 'stage5.init.start', 'stage5.usable');
  } catch (error) {
    console.error('Failed to fetch summary:', error);
    _showSummaryError(
      error.message || 'Unable to load the harmonization summary. Refresh the page to try again.',
      isReviewStateRecovery(error.message),
    );
  }
};

const _init = () => {
  markTiming('stage5.init.start');
  setActiveStage('review');
  initStepInstruction('review');
  initNavigationEvents();
  _setupMappingControls();

  _downloadBtn?.addEventListener('click', _handleDownload);
  _startOverButton?.addEventListener('click', _openStartOverDialog);
  _startOverCancel?.addEventListener('click', _closeStartOverDialog);
  _startOverConfirm?.addEventListener('click', _confirmStartOver);
  _conformanceReturnButton?.addEventListener('click', _returnToReview);
  _conformanceProceedButton?.addEventListener('click', _closeConformanceWarning);
  _conformanceWarningDialog?.addEventListener('cancel', (event) => event.preventDefault());
  void _fetchSummary();
};

_init();
