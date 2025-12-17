/**
 * Stage 5 Download - Final step to download harmonized data with manual overrides applied.
 */

import { initStepInstruction } from '/assets/shared/step-instruction-ui.js';

const _DEFAULT_STAGE_THREE_KEY = 'stage3HarmonizePayload';
const _DEFAULT_SUMMARY_ENDPOINT = '/stage-5/summary';
const _DEFAULT_DOWNLOAD_ENDPOINT = '/stage-5/download';
const _DEFAULT_ZIP_FILENAME = 'harmonized_data.zip';
const _STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'review', 'export'];

const _config = window.stageFiveConfig ?? {};
const _stageThreePayloadKey = _config.stageThreePayloadKey ?? _DEFAULT_STAGE_THREE_KEY;
const _summaryEndpoint = _config.summaryEndpoint ?? _DEFAULT_SUMMARY_ENDPOINT;
const _downloadEndpoint = _config.downloadEndpoint ?? _DEFAULT_DOWNLOAD_ENDPOINT;

const _downloadBtn = document.getElementById('downloadResults');
const _downloadError = document.getElementById('downloadError');
const _summaryGrid = document.getElementById('summaryGrid');

const _state = {
  fileId: null,
};

const _escapeHtml = (str) => {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
};

const _safeNumber = (value) => {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
};

const _extractFilename = (disposition) => {
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/);
  if (utf8Match) {
    return decodeURIComponent(utf8Match[1]);
  }
  const basicMatch = disposition.match(/filename="([^"]+)"/);
  if (basicMatch) {
    return basicMatch[1];
  }
  return _DEFAULT_ZIP_FILENAME;
};

const _setDownloadButtonState = (isLoading) => {
  if (!_downloadBtn) return;
  _downloadBtn.disabled = isLoading;
  _downloadBtn.textContent = isLoading ? 'Preparing download...' : 'Download harmonized data';
};

const _triggerBrowserDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
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

const _setActiveStage = (stage) => {
  const targetIndex = _STAGE_ORDER.indexOf(stage);
  document.querySelectorAll('.progress-tracker [data-stage]').forEach((step) => {
    const stepIndex = _STAGE_ORDER.indexOf(step.dataset.stage);
    step.classList.toggle('active', step.dataset.stage === stage);
    step.classList.toggle('complete', stepIndex >= 0 && stepIndex < targetIndex);
  });
};

const _loadSourceContext = () => {
  try {
    const raw = sessionStorage.getItem(_stageThreePayloadKey);
    const stored = raw ? JSON.parse(raw) : null;
    const id = stored?.request?.file_id;
    if (!id) return null;
    return { fileId: id };
  } catch (error) {
    console.warn('Failed to load source context:', error);
    return null;
  }
};

const _handleDownload = async () => {
  if (!_state.fileId) {
    _showError('Unable to locate file. Please restart the harmonization process.');
    return;
  }

  _hideError();
  _setDownloadButtonState(true);

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

const _createColumnCard = (col) => {
  const columnName = _escapeHtml(col.column);
  const distinctTerms = _safeNumber(col.distinct_terms).toLocaleString();
  const aiChanges = _safeNumber(col.ai_changes).toLocaleString();
  const manualChanges = _safeNumber(col.manual_changes).toLocaleString();

  return `
    <article class="summary-column-card">
      <h4 class="summary-column-name">${columnName}</h4>
      <dl class="summary-stats">
        <div class="summary-stat"><dt>Distinct terms</dt><dd>${distinctTerms}</dd></div>
        <div class="summary-stat"><dt>AI harmonized</dt><dd>${aiChanges}</dd></div>
        <div class="summary-stat"><dt>Manual overrides</dt><dd>${manualChanges}</dd></div>
      </dl>
    </article>
  `;
};

const _renderSummary = (columnSummaries) => {
  if (!_summaryGrid) return;

  const changed = columnSummaries.filter((col) => col.ai_changes > 0 || col.manual_changes > 0);

  if (changed.length === 0) {
    _summaryGrid.innerHTML = '<p class="summary-empty">No columns were modified during harmonization.</p>';
    return;
  }

  _summaryGrid.innerHTML = changed.map(_createColumnCard).join('');
};

const _fetchSummary = async () => {
  const context = _loadSourceContext();
  if (!context) {
    if (_summaryGrid) {
      _summaryGrid.innerHTML = '<p class="summary-empty">Unable to locate harmonization context.</p>';
    }
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
    _renderSummary(data.column_summaries ?? []);
  } catch (error) {
    console.error('Failed to fetch summary:', error);
    if (_summaryGrid) {
      _summaryGrid.innerHTML = '<p class="summary-empty">Unable to load harmonization summary.</p>';
    }
  }
};

const _attachNavigationEvents = () => {
  document.querySelectorAll('.progress-tracker .step[data-url]').forEach((el) => {
    el.addEventListener('click', () => {
      if (el.dataset.url) {
        window.location.assign(el.dataset.url);
      }
    });
  });

  document.querySelectorAll('[data-nav-target]').forEach((el) => {
    el.addEventListener('click', () => {
      if (el.dataset.navTarget) {
        window.location.assign(el.dataset.navTarget);
      }
    });
  });
};

const _init = () => {
  _setActiveStage('export');
  initStepInstruction('export');
  _attachNavigationEvents();

  if (_downloadBtn) {
    _downloadBtn.addEventListener('click', _handleDownload);
  }

  _fetchSummary();
};

_init();
