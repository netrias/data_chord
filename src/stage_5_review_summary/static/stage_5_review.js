/**
 * Stage 5 Download - Final step to download harmonized data with manual overrides applied.
 */

import { initStepInstruction, setActiveStage, initNavigationEvents } from '/assets/shared/step-instruction-ui.js';
import { STAGE_3_PAYLOAD_KEY, isValidFileId, isSafeFilename, readFromSession } from '/assets/shared/storage-keys.js';

const _DEFAULT_SUMMARY_ENDPOINT = '/stage-5/summary';
const _DEFAULT_DOWNLOAD_ENDPOINT = '/stage-5/download';
const _DEFAULT_ZIP_FILENAME = 'harmonized_data.zip';
/** Delay before revoking blob URL to allow browser to initiate download. */
const _REVOKE_DELAY_MS = 100;
/** Allowed segment types for bar visualization and labels. */
const _VALID_SEGMENT_TYPES = ['ai', 'manual', 'unchanged'];

const _config = window.stageFiveConfig ?? {};
const _summaryEndpoint = _config.summaryEndpoint ?? _DEFAULT_SUMMARY_ENDPOINT;
const _downloadEndpoint = _config.downloadEndpoint ?? _DEFAULT_DOWNLOAD_ENDPOINT;

const _downloadBtn = document.getElementById('downloadResults');
const _downloadError = document.getElementById('downloadError');
const _summaryGrid = document.getElementById('summaryGrid');
const _uploadNavAction = document.getElementById('uploadNavAction');
const _changesTableSection = document.getElementById('changesTableSection');
const _changesTableBody = document.getElementById('changesTableBody');
const _changesTable = document.getElementById('changesTable');

const _state = {
  fileId: null,
  termMappings: [],
  sortColumn: null,
  sortDirection: 'asc',
  nonConformantCount: 0,
};

const _safeNumber = (value) => {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
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
    const basicMatch = disposition.match(/filename="([^"]+)"/);
    if (basicMatch) {
      filename = basicMatch[1];
    }
  }

  if (filename && isSafeFilename(filename)) {
    return filename;
  }

  return _DEFAULT_ZIP_FILENAME;
};

const _setDownloadButtonState = (isLoading) => {
  if (!_downloadBtn) return;
  _downloadBtn.disabled = isLoading;
  const frontEl = _downloadBtn.querySelector('.btn-3d-front');
  const textTarget = frontEl ?? _downloadBtn;
  textTarget.textContent = isLoading ? 'Preparing download...' : 'Download harmonized data';
};

const _triggerBrowserDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), _REVOKE_DELAY_MS);
};

const _showError = (message) => {
  if (!_downloadError) return;
  _downloadError.textContent = message;
  _downloadError.classList.remove('hidden');
};

const _hideError = () => {
  if (_downloadError) {
    _downloadError.classList.add('hidden');
  }
};

const _loadSourceContext = () => {
  const stored = readFromSession(STAGE_3_PAYLOAD_KEY);
  if (!stored) return null;
  const id = stored?.request?.file_id;
  if (!id || !isValidFileId(id)) return null;
  return { fileId: id };
};

const _showUploadNav = () => {
  if (_uploadNavAction) {
    _uploadNavAction.classList.remove('hidden');
  }
};

const _handleDownload = async () => {
  if (!_state.fileId) {
    _showError('Unable to locate file. Please restart the harmonization process.');
    return;
  }

  _hideError();
  _setDownloadButtonState(true);
  _showUploadNav();

  try {
    const response = await fetch(_downloadEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: _state.fileId }),
    });

    if (!response.ok) {
      throw new Error('Download failed.');
    }

    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') ?? '';
    const filename = _extractFilename(disposition);

    _triggerBrowserDownload(blob, filename);
  } catch (error) {
    console.error('Download failed:', error);
    _showError('Download failed. Please try again.');
  } finally {
    _setDownloadButtonState(false);
  }
};

const _createBarSegment = (type, percent) => {
  const safeType = _VALID_SEGMENT_TYPES.includes(type) ? type : 'unchanged';
  const segment = document.createElement('div');
  segment.className = `summary-bar__segment summary-bar__segment--${safeType}`;
  segment.style.width = `${percent}%`;
  return segment;
};

const _createLabel = (type, text, count) => {
  const safeType = _VALID_SEGMENT_TYPES.includes(type) ? type : 'unchanged';
  const label = document.createElement('div');
  label.className = 'summary-label';

  const indicator = document.createElement('span');
  indicator.className = `summary-label__indicator summary-label__indicator--${safeType}`;
  label.appendChild(indicator);

  const textSpan = document.createElement('span');
  textSpan.className = 'summary-label__text';
  textSpan.textContent = text;
  label.appendChild(textSpan);

  const countSpan = document.createElement('span');
  countSpan.className = 'summary-label__count';
  countSpan.textContent = _safeNumber(count).toLocaleString();
  label.appendChild(countSpan);

  return label;
};

const _createColumnCard = (col) => {
  const article = document.createElement('article');
  article.className = 'summary-column-card';

  const aiCount = _safeNumber(col.ai_changes);
  const manualCount = _safeNumber(col.manual_changes);
  const unchangedCount = _safeNumber(col.unchanged);
  const total = _safeNumber(col.distinct_terms);

  const aiPercent = total > 0 ? (aiCount / total) * 100 : 0;
  const manualPercent = total > 0 ? (manualCount / total) * 100 : 0;
  /* Calculate unchanged as remainder to ensure percentages sum to 100% and avoid floating-point gaps in the bar. */
  const unchangedPercent = total > 0 ? 100 - aiPercent - manualPercent : 0;

  const header = document.createElement('div');
  header.className = 'summary-column-header';

  const h4 = document.createElement('h4');
  h4.className = 'summary-column-name';
  h4.textContent = col.column;
  header.appendChild(h4);

  const termsSpan = document.createElement('span');
  termsSpan.className = 'summary-column-terms';
  termsSpan.textContent = `${total.toLocaleString()} terms`;
  header.appendChild(termsSpan);

  article.appendChild(header);

  const barContainer = document.createElement('div');
  barContainer.className = 'summary-bar-container';

  const bar = document.createElement('div');
  bar.className = 'summary-bar';

  if (aiPercent > 0) {
    bar.appendChild(_createBarSegment('ai', aiPercent));
  }
  if (manualPercent > 0) {
    bar.appendChild(_createBarSegment('manual', manualPercent));
  }
  if (unchangedPercent > 0) {
    bar.appendChild(_createBarSegment('unchanged', unchangedPercent));
  }

  barContainer.appendChild(bar);
  article.appendChild(barContainer);

  const labels = document.createElement('div');
  labels.className = 'summary-labels';

  if (aiCount > 0) {
    labels.appendChild(_createLabel('ai', 'AI Harmonized:', aiCount));
  }
  if (manualCount > 0) {
    labels.appendChild(_createLabel('manual', 'Manual Override:', manualCount));
  }
  if (unchangedCount > 0) {
    labels.appendChild(_createLabel('unchanged', 'Unchanged:', unchangedCount));
  }

  article.appendChild(labels);

  return article;
};

const _createNonConformantBanner = (count) => {
  const banner = document.createElement('div');
  banner.className = 'non-conformant-banner';

  const icon = document.createElement('span');
  icon.className = 'non-conformant-banner__icon';
  icon.textContent = '⚠';
  icon.setAttribute('aria-hidden', 'true');
  banner.appendChild(icon);

  const text = document.createElement('p');
  text.className = 'non-conformant-banner__text';
  const countSpan = document.createElement('span');
  countSpan.className = 'non-conformant-banner__count';
  countSpan.textContent = count.toLocaleString();
  text.appendChild(countSpan);
  text.appendChild(document.createTextNode(
    ` value${count === 1 ? '' : 's'} do not match permissible values for the target ontology.`
  ));
  banner.appendChild(text);

  return banner;
};

const _renderSummary = (columnSummaries, nonConformantCount) => {
  if (!_summaryGrid) return;

  _summaryGrid.replaceChildren();

  if (nonConformantCount > 0) {
    _summaryGrid.appendChild(_createNonConformantBanner(nonConformantCount));
  }

  const changed = columnSummaries.filter((col) => col.ai_changes > 0 || col.manual_changes > 0);

  if (changed.length === 0) {
    const emptyMsg = document.createElement('p');
    emptyMsg.className = 'summary-empty';
    emptyMsg.textContent = 'No columns were modified during harmonization.';
    _summaryGrid.appendChild(emptyMsg);
    return;
  }

  for (const col of changed) {
    _summaryGrid.appendChild(_createColumnCard(col));
  }
};

const _showEmptyMessage = (message) => {
  if (!_summaryGrid) return;
  _summaryGrid.replaceChildren();
  const emptyMsg = document.createElement('p');
  emptyMsg.className = 'summary-empty';
  emptyMsg.textContent = message;
  _summaryGrid.appendChild(emptyMsg);
};

const _SORT_KEYS = ['column', 'original_value', 'final_value'];

const _SOURCE_LABELS = {
  original: 'Original Value',
  ai: 'AI Suggestion',
  user: 'User Override',
  system: 'System Adjustment',
};

const _escapeHtml = (text) => {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
};

const _formatTimestamp = (isoString) => {
  if (!isoString) return null;
  try {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return null;
    return date.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return null;
  }
};

const _showHistoryDialog = (mapping) => {
  const dialog = document.createElement('dialog');
  dialog.className = 'history-dialog';

  const history = mapping.history ?? [];

  let stepsHtml = '';
  if (history.length === 0) {
    stepsHtml = '<p class="history-empty">No transformation history available.</p>';
  } else {
    for (const step of history) {
      const safeSource = _escapeHtml(step.source);
      const label = _SOURCE_LABELS[step.source] ?? safeSource;
      const value = _escapeHtml(step.value);
      const timestamp = _formatTimestamp(step.timestamp);
      const userId = step.user_id;

      const timestampHtml = timestamp
        ? `<span class="history-step__timestamp">${timestamp}</span>`
        : '';

      const userHtml = userId
        ? `<div class="history-step__user">${_escapeHtml(userId)}</div>`
        : '';

      stepsHtml += `
        <div class="history-step" data-source="${safeSource}">
          <div class="history-step__header">
            <span class="history-step__source">${label}</span>
            ${timestampHtml}
          </div>
          <div class="history-step__value">"${value}"</div>
          ${userHtml}
        </div>
      `;
    }
  }

  dialog.innerHTML = `
    <div class="history-dialog-content">
      <div class="history-dialog-header">
        <h3 class="history-dialog-title">Transformation History</h3>
        <div class="history-dialog-subtitle">${_escapeHtml(mapping.column)}</div>
      </div>
      <div class="history-dialog-body">
        <div class="history-timeline">
          ${stepsHtml}
        </div>
      </div>
      <div class="history-dialog-footer">
        <button class="btn-secondary" data-action="close">Close</button>
      </div>
    </div>
  `;

  dialog.querySelector('[data-action="close"]').addEventListener('click', () => {
    dialog.close();
    dialog.remove();
  });

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      dialog.close();
      dialog.remove();
    }
  });

  document.body.appendChild(dialog);
  dialog.showModal();
};

const _sortMappings = (mappings, column, direction) => {
  const sorted = [...mappings];
  sorted.sort((a, b) => {
    const aVal = (a[column] ?? '').toLowerCase();
    const bVal = (b[column] ?? '').toLowerCase();
    const cmp = aVal.localeCompare(bVal);
    return direction === 'asc' ? cmp : -cmp;
  });
  return sorted;
};

const _renderTableRows = (mappings) => {
  if (!_changesTableBody) return;

  _changesTableBody.replaceChildren();

  for (const mapping of mappings) {
    const row = document.createElement('tr');
    row.classList.add('clickable-row');
    row.setAttribute('tabindex', '0');
    row.addEventListener('click', () => _showHistoryDialog(mapping));
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        _showHistoryDialog(mapping);
      }
    });

    if (mapping.is_pv_conformant === false) {
      row.classList.add('non-conformant');
    }

    const colCell = document.createElement('td');
    colCell.textContent = mapping.column;
    row.appendChild(colCell);

    const origCell = document.createElement('td');
    origCell.textContent = mapping.original_value;
    row.appendChild(origCell);

    const finalCell = document.createElement('td');

    if (mapping.is_pv_conformant === false) {
      const warningIcon = document.createElement('span');
      warningIcon.className = 'pv-warning-icon';
      warningIcon.textContent = '⚠';
      warningIcon.dataset.tooltip = 'This value is not in the permissible values list for this field.';
      warningIcon.setAttribute('aria-label', 'Warning: value not in permissible values');
      finalCell.appendChild(warningIcon);
    }

    const textNode = document.createTextNode(mapping.final_value);
    finalCell.appendChild(textNode);

    row.appendChild(finalCell);

    _changesTableBody.appendChild(row);
  }
};

const _updateSortIndicators = () => {
  if (!_changesTable) return;

  const headers = _changesTable.querySelectorAll('th');
  headers.forEach((th, index) => {
    const key = _SORT_KEYS[index];
    th.classList.remove('sorted', 'sorted-asc', 'sorted-desc');

    if (_state.sortColumn === key) {
      th.classList.add('sorted', `sorted-${_state.sortDirection}`);
    }
  });
};

const _handleSort = (columnIndex) => {
  const key = _SORT_KEYS[columnIndex];

  if (_state.sortColumn === key) {
    _state.sortDirection = _state.sortDirection === 'asc' ? 'desc' : 'asc';
  } else {
    _state.sortColumn = key;
    _state.sortDirection = 'asc';
  }

  const sorted = _sortMappings(_state.termMappings, _state.sortColumn, _state.sortDirection);
  _renderTableRows(sorted);
  _updateSortIndicators();
};

const _setupSortableHeaders = () => {
  if (!_changesTable) return;

  const headers = _changesTable.querySelectorAll('th');
  headers.forEach((th, index) => {
    /* Guard against duplicate indicators if _setupSortableHeaders is called multiple times. */
    if (!th.querySelector('.sort-indicator')) {
      const indicator = document.createElement('span');
      indicator.className = 'sort-indicator';
      th.appendChild(indicator);
    }

    th.addEventListener('click', () => _handleSort(index));
  });
};

const _renderChangesTable = (termMappings) => {
  if (!_changesTableSection || !_changesTableBody) return;

  if (!termMappings || termMappings.length === 0) {
    return;
  }

  _state.termMappings = termMappings;
  _state.sortColumn = null;
  _state.sortDirection = 'asc';

  _renderTableRows(termMappings);
  _setupSortableHeaders();
  _changesTableSection.classList.remove('hidden');
};

const _fetchSummary = async () => {
  const context = _loadSourceContext();
  if (!context) {
    _showEmptyMessage('Unable to locate harmonization context.');
    return;
  }

  _state.fileId = context.fileId;

  try {
    const response = await fetch(_summaryEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: context.fileId }),
    });

    if (!response.ok) {
      throw new Error('Unable to load summary.');
    }

    const data = await response.json();
    _state.nonConformantCount = data.non_conformant_count ?? 0;
    _renderSummary(data.column_summaries ?? [], _state.nonConformantCount);
    _renderChangesTable(data.term_mappings ?? []);
  } catch (error) {
    console.error('Failed to fetch summary:', error);
    _showEmptyMessage('Unable to load harmonization summary.');
  }
};

const _init = () => {
  setActiveStage('review');
  initStepInstruction('review');
  initNavigationEvents();

  if (_downloadBtn) {
    _downloadBtn.addEventListener('click', _handleDownload);
  }

  _fetchSummary();
};

_init();
