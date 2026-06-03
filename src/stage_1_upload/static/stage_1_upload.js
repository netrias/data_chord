import { initStepInstruction, setActiveStage, initNavigationEvents, advanceMaxReachedStage } from '/assets/shared/step-instruction-ui.js';
import { STAGE_2_PAYLOAD_KEY, STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, CURRENT_FILE_SESSION_KEY, removeFromSession, writeToSession, readFromSession } from '/assets/shared/storage-keys.js';
import { reportApiError, reportFetchFailure } from '/assets/shared/client-events.js';
import { showDataModelPopup, preloadDataModels } from './data_model_popup.js';

const config = window.stageOneUploadConfig ?? {};

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const analyzeButton = document.getElementById('analyzeButton');
const statusMessage = document.getElementById('statusMessage');
const dropzoneCopy = document.getElementById('dropzoneCopy');
const dropzoneUploading = document.getElementById('dropzoneUploading');
const uploadingFileName = document.getElementById('uploadingFileName');
const dropzoneFile = document.getElementById('dropzoneFile');
const dropzoneFileName = document.getElementById('dropzoneFileName');
const dropzoneFileSize = document.getElementById('dropzoneFileSize');
const dropzoneFileStatus = document.getElementById('dropzoneFileStatus');
const changeFileButton = document.getElementById('changeFileButton');
const analyzeOverlay = document.getElementById('analyzeOverlay');
const sheetSelectorPanel = document.getElementById('sheetSelectorPanel');
const sheetSelect = document.getElementById('sheetSelect');
const sheetTabsList = document.getElementById('sheetTabsList');
const sheetCountBadge = document.getElementById('sheetCountBadge');
const sheetPreviewPopover = document.getElementById('sheetPreviewPopover');
const sheetPreviewPopoverTitle = document.getElementById('sheetPreviewPopoverTitle');
const sheetPreviewPopoverBody = document.getElementById('sheetPreviewPopoverBody');

const state = {
  file: null,
  uploaded: null,
  sheetNames: [],
  sheetPreviews: {},
  selectedSheet: null,
  isUploading: false,
  isAnalyzing: false,
};

const _formatBytes = (bytes) => {
  if (!bytes && bytes !== 0) {
    return '—';
  }
  if (bytes < 0) {
    return '—';
  }
  if (bytes === 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(1)} ${units[idx]}`;
};

const _setStatus = (message = '', tone = '') => {
  if (!statusMessage) return;
  statusMessage.textContent = message;
  statusMessage.classList.remove('error', 'success');
  if (tone) {
    statusMessage.classList.add(tone);
  }
};

const _setAnalyzeButtonEnabled = (enabled) => {
  if (!analyzeButton) return;
  analyzeButton.disabled = !enabled;
};

const _showDropzoneCopy = () => {
  if (dropzoneCopy) dropzoneCopy.classList.remove('hidden');
  if (dropzoneUploading) {
    dropzoneUploading.classList.add('hidden');
    dropzoneUploading.setAttribute('aria-hidden', 'true');
  }
  if (dropzoneFile) dropzoneFile.classList.add('hidden');
};

/* ~45 chars fills one line at 1.4rem in the 600px dropzone. */
const LONG_NAME_THRESHOLD = 45;

const _showDropzoneUploading = (file) => {
  if (dropzoneCopy) dropzoneCopy.classList.add('hidden');
  if (dropzoneFile) dropzoneFile.classList.add('hidden');
  if (dropzoneUploading) {
    dropzoneUploading.classList.remove('hidden');
    dropzoneUploading.setAttribute('aria-hidden', 'false');
  }
  if (uploadingFileName) {
    uploadingFileName.textContent = file.name;
    uploadingFileName.classList.toggle('uploading-file-name--long', file.name.length > LONG_NAME_THRESHOLD);
  }
};

const _showDropzoneSummary = (file, statusText) => {
  if (dropzoneCopy) dropzoneCopy.classList.add('hidden');
  if (dropzoneUploading) {
    dropzoneUploading.classList.add('hidden');
    dropzoneUploading.setAttribute('aria-hidden', 'true');
  }
  if (dropzoneFile) dropzoneFile.classList.remove('hidden');
  if (dropzoneFileName) {
    dropzoneFileName.textContent = file.name;
    dropzoneFileName.title = file.name;
    dropzoneFileName.classList.toggle('file-name--long', file.name.length > LONG_NAME_THRESHOLD);
  }
  /* Hydrated files have humanSize (pre-formatted); real Files have size (bytes). */
  if (dropzoneFileSize) dropzoneFileSize.textContent = file.humanSize ?? _formatBytes(file.size);
  if (dropzoneFileStatus) dropzoneFileStatus.textContent = statusText;
};

const _openFilePicker = () => {
  if (!fileInput || state.isUploading) return;
  fileInput.value = '';
  fileInput.click();
};

const _resetUploadState = () => {
  state.file = null;
  state.uploaded = null;
  state.sheetNames = [];
  state.sheetPreviews = {};
  state.selectedSheet = null;
  state.isUploading = false;
  state.isAnalyzing = false;
  if (fileInput) fileInput.value = '';
  _setAnalyzeButtonEnabled(false);
  if (dropzone) {
    dropzone.classList.remove('has-file', 'is-uploading');
    dropzone.removeAttribute('aria-busy');
  }
  _showDropzoneCopy();
  _renderSheetSelector();
  _setStatus('');
  setActiveStage('upload');
};

const _validateFile = (file) => {
  const errors = [];
  if (!file) {
    errors.push('No file detected.');
  }
  if (file && !/\.(csv|tsv|xlsx)$/i.test(file.name)) {
    errors.push('Only CSV, TSV, or XLSX files are supported right now.');
  }
  if (file && config.maxBytes && file.size > Number(config.maxBytes)) {
    errors.push(`File exceeds the ${_formatBytes(Number(config.maxBytes))} limit.`);
  }
  return errors;
};

const _handleFileSelection = (file) => {
  /* Prevent race condition - ignore if already uploading. */
  if (state.isUploading) {
    return;
  }
  const issues = _validateFile(file);
  if (issues.length) {
    _showDropzoneCopy();
    _setStatus(issues.join(' '), 'error');
    return;
  }
  _clearStaleSessionData();
  state.file = file;
  state.uploaded = null;
  _showDropzoneSummary(file, 'Ready to upload');
  _setStatus('');
  _uploadDataset();
};

const _uploadDataset = async () => {
  if (!state.file || state.isUploading) {
    return;
  }
  state.isUploading = true;
  _setAnalyzeButtonEnabled(false);
  if (dropzone) {
    dropzone.classList.add('is-uploading');
    dropzone.setAttribute('aria-busy', 'true');
  }
  _showDropzoneUploading(state.file);

  const formData = new FormData();
  formData.append('file', state.file);

  try {
    let response;
    try {
      response = await fetch(config.uploadEndpoint, {
        method: 'POST',
        body: formData,
      });
    } catch (error) {
      reportFetchFailure({
        stage: 'stage_1',
        operation: 'upload',
        endpoint: config.uploadEndpoint,
        error,
      });
      throw error;
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      reportApiError({
        stage: 'stage_1',
        operation: 'upload',
        endpoint: config.uploadEndpoint,
        statusCode: response.status,
      });
      throw new Error(payload.detail || 'Upload failed.');
    }
    state.uploaded = payload;
    state.sheetNames = Array.isArray(payload.sheet_names) ? payload.sheet_names : [];
    state.sheetPreviews = _sheetPreviewsFromPayload(payload);
    state.selectedSheet = payload.selected_sheet ?? state.sheetNames[0] ?? null;
    _persistFileSession(payload, state.file.name);
    if (dropzone) dropzone.classList.add('has-file');
    _showDropzoneSummary(state.file, 'Uploaded');
    _renderSheetSelector();
    _setAnalyzeButtonEnabled(true);
  } catch (error) {
    console.error(error);
    _showDropzoneSummary(state.file, 'Upload failed');
    _setStatus(error.message, 'error');
  } finally {
    state.isUploading = false;
    if (dropzone) {
      dropzone.classList.remove('is-uploading');
      dropzone.removeAttribute('aria-busy');
    }
  }
};

const _clearStaleSessionData = () => {
  removeFromSession(STAGE_2_PAYLOAD_KEY);
  removeFromSession(STAGE_3_PAYLOAD_KEY);
  removeFromSession(STAGE_3_JOB_KEY);
  removeFromSession(CURRENT_FILE_SESSION_KEY);
};

/* Enables navigation back to Stage 1 with file context. */
const _persistFileSession = (uploadResponse, fileName) => {
  writeToSession(CURRENT_FILE_SESSION_KEY, {
    file_id: uploadResponse.file_id,
    original_name: fileName,
    uploaded_at: new Date().toISOString(),
    human_size: uploadResponse.human_size,
    sheet_names: uploadResponse.sheet_names ?? [],
    sheet_previews: uploadResponse.sheet_previews ?? {},
    selected_sheet: uploadResponse.selected_sheet ?? null,
  });
};

/* Restores file display from session when returning to Stage 1. */
const _hydrateFromSession = () => {
  const session = readFromSession(CURRENT_FILE_SESSION_KEY);
  if (!session || !session.file_id || !session.original_name) {
    return false;
  }

  state.uploaded = {
    file_id: session.file_id,
    human_size: session.human_size,
    sheet_previews: session.sheet_previews ?? {},
  };
  state.file = { name: session.original_name, humanSize: session.human_size };
  state.sheetNames = Array.isArray(session.sheet_names) ? session.sheet_names : [];
  state.sheetPreviews = _sheetPreviewsFromPayload(session);
  state.selectedSheet = session.selected_sheet ?? state.sheetNames[0] ?? null;

  if (dropzone) dropzone.classList.add('has-file');
  _showDropzoneSummary(state.file, 'Uploaded');
  _renderSheetSelector();
  _setAnalyzeButtonEnabled(true);
  return true;
};

/* Inline SVG check used inside each active tab; declared once to keep the
   render loop allocation-light. */
const _SHEET_CHECK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';

const _renderSheetSelector = () => {
  if (!sheetSelectorPanel || !sheetSelect) return;
  sheetSelect.replaceChildren();
  if (sheetTabsList) sheetTabsList.replaceChildren();
  /* Hide the picker for CSV/TSV (no sheets) and for single-sheet workbooks —
     there is nothing to choose, so the widget is just chrome. */
  if (state.sheetNames.length <= 1) {
    sheetSelectorPanel.classList.add('hidden');
    sheetSelectorPanel.setAttribute('aria-hidden', 'true');
    return;
  }

  /* Hidden <select> remains the source of truth for screen readers and
     Playwright's selectOption() — keep it in sync with the visual tabs. */
  for (const sheetName of state.sheetNames) {
    const option = document.createElement('option');
    option.value = sheetName;
    option.textContent = sheetName;
    option.selected = sheetName === state.selectedSheet;
    sheetSelect.append(option);
  }

  if (sheetTabsList) {
    state.sheetNames.forEach((sheetName, index) => {
      sheetTabsList.append(_buildSheetTab(sheetName, index));
    });
  }

  sheetSelectorPanel.classList.remove('hidden');
  sheetSelectorPanel.setAttribute('aria-hidden', 'false');
  _updateSheetSubtitle();
};

const _buildSheetTab = (sheetName, index) => {
  const tab = document.createElement('button');
  tab.type = 'button';
  tab.className = 'workbook-tab';
  tab.dataset.sheetName = sheetName;
  tab.dataset.sheetIndex = String(index);
  tab.setAttribute('role', 'tab');
  /* Browsers show native title="" tooltips after a long delay; we render a
     custom tooltip instead, so the attribute is intentionally omitted. */
  const isActive = sheetName === state.selectedSheet;
  tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
  tab.tabIndex = isActive ? 0 : -1;
  if (isActive) tab.classList.add('is-active');

  const indexChip = document.createElement('span');
  indexChip.className = 'workbook-tab-index';
  indexChip.textContent = String(index + 1).padStart(2, '0');

  const nameSpan = document.createElement('span');
  nameSpan.className = 'workbook-tab-name';
  nameSpan.textContent = sheetName;

  const check = document.createElement('span');
  check.className = 'workbook-tab-check';
  check.innerHTML = _SHEET_CHECK_SVG;

  tab.append(indexChip, nameSpan, check);
  tab.addEventListener('click', () => _selectSheet(sheetName, { focus: true }));
  tab.addEventListener('keydown', (event) => _handleSheetTabKeydown(event, index));
  tab.addEventListener('mouseenter', () => _showSheetPreviewPopover(tab));
  tab.addEventListener('mouseleave', _hideSheetPreviewPopover);
  tab.addEventListener('focus', () => _showSheetPreviewPopover(tab));
  tab.addEventListener('blur', _hideSheetPreviewPopover);
  return tab;
};

const _sheetPreviewsFromPayload = (payload) => {
  const previews = payload?.sheet_previews;
  return previews && typeof previews === 'object' && !Array.isArray(previews) ? previews : {};
};

const _showSheetPreviewPopover = (tab) => {
  if (!sheetPreviewPopover || !sheetPreviewPopoverTitle || !sheetPreviewPopoverBody) return;
  const sheetName = tab.dataset.sheetName ?? '';
  sheetPreviewPopoverTitle.textContent = sheetName;
  _renderSheetPreviewBody(sheetPreviewPopoverBody, state.sheetPreviews[sheetName]);
  sheetPreviewPopover.classList.add('is-visible');
  sheetPreviewPopover.setAttribute('aria-hidden', 'false');

  const tabRect = tab.getBoundingClientRect();
  const popoverRect = sheetPreviewPopover.getBoundingClientRect();
  let left = tabRect.left + tabRect.width / 2 - popoverRect.width / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - popoverRect.width - 8));
  const top = Math.max(8, tabRect.top - popoverRect.height - 10);
  sheetPreviewPopover.style.left = `${left}px`;
  sheetPreviewPopover.style.top = `${top}px`;
};

const _hideSheetPreviewPopover = () => {
  if (!sheetPreviewPopover) return;
  sheetPreviewPopover.classList.remove('is-visible');
  sheetPreviewPopover.setAttribute('aria-hidden', 'true');
};

const _renderSheetPreviewBody = (body, preview) => {
  body.replaceChildren();
  if (!preview || !Array.isArray(preview.headers)) {
    const message = document.createElement('p');
    message.className = 'sheet-preview-empty';
    message.textContent = 'Preview unavailable.';
    body.append(message);
    return;
  }
  if (preview.headers.length === 0) {
    const message = document.createElement('p');
    message.className = 'sheet-preview-empty';
    message.textContent = 'No rows found in this sheet.';
    body.append(message);
    return;
  }

  body.append(_buildSheetPreviewTable(preview));
  if (preview.truncated_rows || preview.truncated_columns) {
    const footer = document.createElement('p');
    footer.className = 'sheet-preview-footer';
    footer.textContent = 'Preview is capped to the first rows and columns.';
    body.append(footer);
  }
};

const _buildSheetPreviewTable = (preview) => {
  const table = document.createElement('table');
  table.className = 'sheet-preview-table';

  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  preview.headers.forEach((header) => {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = header;
    headerRow.append(th);
  });
  thead.append(headerRow);

  const tbody = document.createElement('tbody');
  const previewRows = Array.isArray(preview.rows) ? preview.rows : [];
  previewRows.forEach((row) => {
    const tr = document.createElement('tr');
    preview.headers.forEach((_, index) => {
      const td = document.createElement('td');
      td.textContent = Array.isArray(row) ? (row[index] ?? '') : '';
      tr.append(td);
    });
    tbody.append(tr);
  });

  table.append(thead, tbody);
  return table;
};

const _selectSheet = (sheetName, { focus = false } = {}) => {
  if (!state.sheetNames.includes(sheetName)) return;
  if (state.selectedSheet === sheetName) {
    if (focus) _focusActiveTab();
    return;
  }
  state.selectedSheet = sheetName;
  if (sheetSelect) sheetSelect.value = sheetName;
  if (sheetTabsList) {
    sheetTabsList.querySelectorAll('.workbook-tab').forEach((tab) => {
      const isActive = tab.dataset.sheetName === sheetName;
      tab.classList.toggle('is-active', isActive);
      tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
      tab.tabIndex = isActive ? 0 : -1;
    });
  }
  _updateSheetSubtitle();
  _persistSelectedSheet();
  if (focus) _focusActiveTab();
  /* Native change event keeps any incidental listeners (and Playwright) wired up. */
  if (sheetSelect) sheetSelect.dispatchEvent(new Event('change', { bubbles: true }));
};

const _persistSelectedSheet = () => {
  if (!state.uploaded || !state.file) return;
  _persistFileSession(
    {
      ...state.uploaded,
      sheet_names: state.sheetNames,
      selected_sheet: state.selectedSheet,
    },
    state.file.name,
  );
};

const _updateSheetSubtitle = () => {
  if (!sheetCountBadge) return;
  const total = state.sheetNames.length;
  if (!total) {
    sheetCountBadge.textContent = '';
    return;
  }
  const activeIndex = state.sheetNames.indexOf(state.selectedSheet ?? '');
  const sheetWord = total === 1 ? 'sheet' : 'sheets';
  if (activeIndex < 0) {
    sheetCountBadge.textContent = `${total} ${sheetWord} in this workbook`;
    return;
  }
  /* Build the styled subtitle from element nodes (not innerHTML) so user-derived
     sheet names can never escape into the HTML string. Only integers are interpolated. */
  const accent = document.createElement('span');
  accent.className = 'workbook-tabs-subtitle-active';
  accent.textContent = `Sheet ${activeIndex + 1} of ${total}`;
  sheetCountBadge.replaceChildren(
    accent,
    document.createTextNode(' · click another tab to change worksheets'),
  );
};

const _focusActiveTab = () => {
  if (!sheetTabsList) return;
  const activeTab = sheetTabsList.querySelector('.workbook-tab.is-active');
  if (activeTab instanceof HTMLElement) {
    activeTab.focus();
    /* Keep the focused tab in view when the wrapped strip is tall enough to scroll. */
    activeTab.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
};

const _handleSheetTabKeydown = (event, index) => {
  const total = state.sheetNames.length;
  if (!total) return;
  let nextIndex = null;
  switch (event.key) {
    case 'ArrowRight':
      nextIndex = (index + 1) % total;
      break;
    case 'ArrowLeft':
      nextIndex = (index - 1 + total) % total;
      break;
    case 'Home':
      nextIndex = 0;
      break;
    case 'End':
      nextIndex = total - 1;
      break;
    case 'Escape':
      _hideSheetPreviewPopover();
      return;
    case 'Enter':
    case ' ':
      event.preventDefault();
      _selectSheet(state.sheetNames[index], { focus: true });
      return;
    default:
      return;
  }
  if (nextIndex !== null) {
    event.preventDefault();
    _selectSheet(state.sheetNames[nextIndex], { focus: true });
  }
};

const _persistStageTwoPayload = (payload) => {
  writeToSession(STAGE_2_PAYLOAD_KEY, payload);
};

const _navigateToStageTwo = (fileId, targetSchema, targetExternalVersionNumber, payload) => {
  _persistStageTwoPayload(payload);
  advanceMaxReachedStage('mapping');
  const search = new URLSearchParams({ file_id: fileId, schema: targetSchema });
  if (targetExternalVersionNumber) search.set('external_version_number', targetExternalVersionNumber);
  window.location.assign(`/stage-2?${search.toString()}`);
};

const _analyzeDataset = async () => {
  if (!state.uploaded || state.isAnalyzing) {
    _setStatus('Upload a file before analyzing.', 'error');
    return;
  }

  /* Show data model selection popup before starting analysis. */
  let selection;
  try {
    selection = await showDataModelPopup();
  } catch (err) {
    _setStatus(err.message, 'error');
    return;
  }
  if (!selection) {
    /* User cancelled - stay on Stage 1. */
    return;
  }
  state.isAnalyzing = true;
  _setAnalyzeButtonEnabled(false);
  _showDropzoneSummary(state.file, 'Analyzing columns…');
  if (analyzeOverlay) analyzeOverlay.classList.remove('hidden');

  try {
    let response;
    try {
      response = await fetch(config.analyzeEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          file_id: state.uploaded.file_id,
          target_schema: selection.dataModelKey,
          target_external_version_number: selection.externalVersionNumber,
          sheet_name: state.selectedSheet,
        }),
      });
    } catch (error) {
      reportFetchFailure({
        stage: 'stage_1',
        operation: 'analyze',
        endpoint: config.analyzeEndpoint,
        fileId: state.uploaded.file_id,
        error,
      });
      throw error;
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      reportApiError({
        stage: 'stage_1',
        operation: 'analyze',
        endpoint: config.analyzeEndpoint,
        fileId: state.uploaded.file_id,
        statusCode: response.status,
      });
      throw new Error(payload.detail || 'Analysis failed.');
    }
    // Keep overlay visible during navigation - browser will replace the page
    _navigateToStageTwo(
      state.uploaded.file_id,
      selection.dataModelKey,
      selection.externalVersionNumber,
      payload,
    );
  } catch (error) {
    console.error(error);
    _setStatus(error.message, 'error');
    _setAnalyzeButtonEnabled(true);
    _showDropzoneSummary(state.file, 'Uploaded');
    // Only hide overlay and reset state on error
    state.isAnalyzing = false;
    if (analyzeOverlay) analyzeOverlay.classList.add('hidden');
  }
};

const _wireDragEvents = () => {
  if (!dropzone) return;

  let dragCounter = 0;

  const preventDefaults = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((eventName) => {
    dropzone.addEventListener(eventName, preventDefaults);
  });

  dropzone.addEventListener('dragenter', () => {
    dragCounter += 1;
    dropzone.classList.add('dragging');
  });

  dropzone.addEventListener('dragleave', () => {
    dragCounter -= 1;
    if (dragCounter === 0) {
      dropzone.classList.remove('dragging');
    }
  });

  dropzone.addEventListener('drop', (event) => {
    dragCounter = 0;
    dropzone.classList.remove('dragging');
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      _handleFileSelection(file);
    }
  });
};

const _init = () => {
  setActiveStage('upload');
  initStepInstruction('upload');
  initNavigationEvents();

  if (!_hydrateFromSession()) {
    _resetUploadState();
  }

  _wireDragEvents();

  if (dropzone) {
    dropzone.addEventListener('click', () => _openFilePicker());
    dropzone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        _openFilePicker();
      }
    });
  }

  if (changeFileButton) {
    changeFileButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      _resetUploadState();
      _openFilePicker();
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', (event) => {
      const files = event.target.files || [];
      const file = files[0];
      if (file) {
        _handleFileSelection(file);
      }
    });
  }

  /* Programmatic selectOption() (Playwright) bypasses the visual tabs, so we
     still need to react to native <select> changes and route them through the
     same selection path that updates tabs, indicator, and session storage. */
  if (sheetSelect) {
    sheetSelect.addEventListener('change', (event) => {
      if (!event.isTrusted && sheetSelect.value === state.selectedSheet) return;
      if (sheetSelect.value && sheetSelect.value !== state.selectedSheet) {
        _selectSheet(sheetSelect.value);
      }
    });
  }

  /* Hide the popover when the anchor tab moves under its fixed position. */
  const strip = document.getElementById('sheetTabsStrip');
  if (strip) strip.addEventListener('scroll', _hideSheetPreviewPopover, { passive: true });

  if (analyzeButton) {
    analyzeButton.addEventListener('click', _analyzeDataset);
  }

  preloadDataModels();
};

_init();
