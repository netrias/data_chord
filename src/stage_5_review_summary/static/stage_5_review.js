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
  /* Delay revocation to allow browser to start download before URL is invalidated. */
  setTimeout(() => URL.revokeObjectURL(url), 100);
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

const _createStatElement = (label, value) => {
  const stat = document.createElement('div');
  stat.className = 'summary-stat';

  const dt = document.createElement('dt');
  dt.textContent = label;

  const dd = document.createElement('dd');
  dd.textContent = value;

  stat.appendChild(dt);
  stat.appendChild(dd);
  return stat;
};

const _createColumnCard = (col) => {
  const article = document.createElement('article');
  article.className = 'card card--inset card--pad-sm summary-column-card';

  const h4 = document.createElement('h4');
  h4.className = 'summary-column-name';
  h4.textContent = col.column;
  article.appendChild(h4);

  const dl = document.createElement('dl');
  dl.className = 'summary-stats';
  dl.appendChild(_createStatElement('Distinct terms', _safeNumber(col.distinct_terms).toLocaleString()));
  dl.appendChild(_createStatElement('AI harmonized', _safeNumber(col.ai_changes).toLocaleString()));
  dl.appendChild(_createStatElement('Manual overrides', _safeNumber(col.manual_changes).toLocaleString()));
  article.appendChild(dl);

  return article;
};

const _renderSummary = (columnSummaries) => {
  if (!_summaryGrid) return;

  const changed = columnSummaries.filter((col) => col.ai_changes > 0 || col.manual_changes > 0);

  _summaryGrid.replaceChildren();

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
    _renderSummary(data.column_summaries ?? []);
  } catch (error) {
    console.error('Failed to fetch summary:', error);
    _showEmptyMessage('Unable to load harmonization summary.');
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
