/**
 * Data Model Selection Popup module.
 * Shows a modal for selecting data model and version before starting mapping.
 * Fetches available data models from GET /stage-1/data-models.
 */

const DATA_MODELS_ENDPOINT = '/stage-1/data-models';
/** Server-provided default; falls back to first available model if missing. */
const DEFAULT_DATA_MODEL = window.stageOneUploadConfig?.defaultDataModel ?? null;

/* Preload state - enables instant popup after page load. */
let _cachedDataModels = null;
let _preloadPromise = null;
let _retryTimeoutId = null;
/** Single retry after 2s delay. Keeps implementation simple - if server is down, user must refresh anyway. */
const RETRY_DELAY_MS = 2000;
let _retryScheduled = false;

/** Backend may return arbitrary labels; fetch avoids hardcoding model list. */
async function _fetchDataModels() {
  const resp = await fetch(DATA_MODELS_ENDPOINT, { cache: 'no-store' });
  if (!resp.ok) {
    throw new Error(`Failed to fetch data models: ${resp.status}`);
  }
  const models = await resp.json();
  /* Filter out models without versions - they cannot be used for mapping. */
  return models.filter((m) => m.versions && m.versions.length > 0);
}

/**
 * Compare version strings using semantic versioning rules.
 * Handles formats like "1.0", "1.2.3", "v1.0", "2024-01-15".
 */
function _compareVersions(a, b) {
  /* Strip leading 'v' if present. */
  const cleanA = a.replace(/^v/i, '');
  const cleanB = b.replace(/^v/i, '');

  const partsA = cleanA.split(/[.\-]/).map((p) => (isNaN(p) ? p : Number(p)));
  const partsB = cleanB.split(/[.\-]/).map((p) => (isNaN(p) ? p : Number(p)));

  const len = Math.max(partsA.length, partsB.length);
  for (let i = 0; i < len; i++) {
    const pA = partsA[i] ?? 0;
    const pB = partsB[i] ?? 0;

    if (typeof pA === 'number' && typeof pB === 'number') {
      if (pA !== pB) return pA - pB;
    } else {
      /* Fall back to string comparison for non-numeric parts. */
      const cmp = String(pA).localeCompare(String(pB));
      if (cmp !== 0) return cmp;
    }
  }
  return 0;
}

function _getLatestVersion(versions) {
  if (!versions || versions.length === 0) return '';
  return [...versions].sort(_compareVersions).pop();
}

function _scheduleRetry() {
  if (_retryScheduled) return;
  _retryScheduled = true;
  _retryTimeoutId = setTimeout(() => {
    _retryTimeoutId = null;
    _retryScheduled = false;
    _preloadPromise = null;
    preloadDataModels();
  }, RETRY_DELAY_MS);
}

function _clearRetry() {
  if (_retryTimeoutId) {
    clearTimeout(_retryTimeoutId);
    _retryTimeoutId = null;
  }
  _retryScheduled = false;
}

/**
 * Preload data models on page init. Single retry after 2s on failure.
 * Call once from _init() in stage_1_upload.js.
 */
export function preloadDataModels() {
  if (_cachedDataModels || _preloadPromise) return;
  _preloadPromise = _fetchDataModels()
    .then((data) => {
      _cachedDataModels = data;
    })
    .catch((err) => {
      console.warn('Preload data models failed:', err);
      _scheduleRetry();
    })
    .finally(() => {
      /* Keep promise non-null during retry to prevent race with showDataModelPopup. */
      if (!_retryScheduled) {
        _preloadPromise = null;
      }
    });
}

function _buildOptionElements(selectEl, items, valueKey, labelKey, selectedValue) {
  selectEl.textContent = '';
  for (const item of items) {
    const value = item[valueKey];
    const label = item[labelKey] || value;
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (value === selectedValue) option.selected = true;
    selectEl.appendChild(option);
  }
}

function _buildVersionOptions(selectEl, versions, selectedVersion) {
  selectEl.textContent = '';
  for (const v of versions) {
    const option = document.createElement('option');
    option.value = v;
    option.textContent = v;
    if (v === selectedVersion) option.selected = true;
    selectEl.appendChild(option);
  }
}

function _buildDialogDOM(dataModels) {
  const content = document.createElement('div');
  content.className = 'data-model-dialog-content';

  /* Header */
  const header = document.createElement('div');
  header.className = 'data-model-dialog-header';
  const title = document.createElement('h2');
  title.className = 'data-model-dialog-title';
  title.textContent = 'Select Data Model';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'data-model-close-btn';
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Close');
  closeBtn.textContent = '\u00D7';
  header.appendChild(title);
  header.appendChild(closeBtn);
  content.appendChild(header);

  /* Body */
  const body = document.createElement('div');
  body.className = 'data-model-dialog-body';

  const modelField = document.createElement('div');
  modelField.className = 'data-model-field';
  const modelLabel = document.createElement('label');
  modelLabel.htmlFor = 'dataModelSelect';
  modelLabel.textContent = 'Data Model';
  const modelSelect = document.createElement('select');
  modelSelect.id = 'dataModelSelect';
  _buildOptionElements(modelSelect, dataModels, 'key', 'label', DEFAULT_DATA_MODEL);
  modelField.appendChild(modelLabel);
  modelField.appendChild(modelSelect);
  body.appendChild(modelField);

  const versionField = document.createElement('div');
  versionField.className = 'data-model-field';
  const versionLabel = document.createElement('label');
  versionLabel.htmlFor = 'versionSelect';
  versionLabel.textContent = 'Version';
  const versionSelect = document.createElement('select');
  versionSelect.id = 'versionSelect';

  const defaultModel = dataModels.find((m) => m.key === DEFAULT_DATA_MODEL) || dataModels[0];
  const versions = defaultModel?.versions ?? [];
  const defaultVersion = _getLatestVersion(versions);
  _buildVersionOptions(versionSelect, versions, defaultVersion);

  versionField.appendChild(versionLabel);
  versionField.appendChild(versionSelect);
  body.appendChild(versionField);
  content.appendChild(body);

  /* Footer */
  const footer = document.createElement('div');
  footer.className = 'data-model-dialog-footer';
  const confirmBtn = document.createElement('button');
  confirmBtn.className = 'data-model-confirm-btn btn-3d';
  confirmBtn.type = 'button';
  confirmBtn.innerHTML =
    '<span class="btn-3d-shadow" aria-hidden="true"></span>' +
    '<span class="btn-3d-edge" aria-hidden="true"></span>' +
    '<span class="btn-3d-front">Start Mapping</span>';
  footer.appendChild(confirmBtn);
  content.appendChild(footer);

  return content;
}

function _attachCloseHandlers(dialog, resolve) {
  let resolved = false;

  const closeWithNull = () => {
    if (resolved) return;
    resolved = true;
    dialog.close();
    dialog.remove();
    resolve(null);
  };

  const closeBtn = dialog.querySelector('.data-model-close-btn');
  closeBtn?.addEventListener('click', closeWithNull);

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      closeWithNull();
    }
  });

  dialog.addEventListener('close', () => {
    if (!resolved) {
      resolved = true;
      dialog.remove();
      resolve(null);
    }
  });

  return { markResolved: () => { resolved = true; } };
}

/** Wire data model <select> change to update the version <select>. */
function _setupModelChangeHandler(dialog, dataModels) {
  const modelSelect = dialog.querySelector('#dataModelSelect');
  const versionSelect = dialog.querySelector('#versionSelect');

  modelSelect.addEventListener('change', () => {
    const selected = dataModels.find((m) => m.key === modelSelect.value);
    const versions = selected?.versions ?? [];
    const latestVersion = _getLatestVersion(versions);
    _buildVersionOptions(versionSelect, versions, latestVersion);
    versionSelect.value = latestVersion;
  });
}

/**
 * Show the data model selection popup.
 * @returns {Promise<{dataModelKey: string, versionLabel: string} | null>}
 *   Resolves with selection on confirm, or null on cancel/close.
 * @throws {Error} If data models are unavailable (preload failed).
 */
export async function showDataModelPopup() {
  /* Wait for in-flight preload if user clicks before it completes. */
  if (_preloadPromise) {
    await _preloadPromise;
  }

  if (!_cachedDataModels || _cachedDataModels.length === 0) {
    throw new Error('Data models are currently unavailable. Please try again later.');
  }

  const dataModels = _cachedDataModels;

  return new Promise((resolve) => {
    const dialog = document.createElement('dialog');
    dialog.className = 'data-model-dialog';
    dialog.appendChild(_buildDialogDOM(dataModels));

    document.body.appendChild(dialog);
    dialog.showModal();

    const { markResolved } = _attachCloseHandlers(dialog, resolve);
    _setupModelChangeHandler(dialog, dataModels);

    const dataModelSelect = dialog.querySelector('#dataModelSelect');
    const versionSelect = dialog.querySelector('#versionSelect');
    const confirmBtn = dialog.querySelector('.data-model-confirm-btn');

    confirmBtn.addEventListener('click', () => {
      markResolved();
      const dataModelKey = dataModelSelect.value;
      const versionLabel = versionSelect.value;
      dialog.close();
      dialog.remove();
      resolve({ dataModelKey, versionLabel });
    });
  });
}
