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

/**
 * Sort versions newest-first.
 * Newest must be index 0 so it appears at the top of the <select> dropdown:
 * Chrome on macOS aligns the picker on the currently-selected option, so if
 * the default (latest) sits last in the list the picker opens upward.
 */
function _sortVersionsDescending(versions) {
  return [...versions].sort((a, b) => {
    const aNumber = Number(a.version_number);
    const bNumber = Number(b.version_number);
    if (Number.isFinite(aNumber) && Number.isFinite(bNumber)) return bNumber - aNumber;
    return _compareVersions(b.version_label, a.version_label);
  });
}

function _getLatestVersion(versions) {
  if (!versions || versions.length === 0) return '';
  return _sortVersionsDescending(versions)[0];
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

/**
 * Build a custom dropdown for versions.
 * Replaces native <select> because macOS Chrome overlays the picker on the trigger
 * (centers the selected option on the trigger position). A custom dropdown lets the
 * panel always render below the trigger without covering it.
 * Wrap exposes a hidden <input id="versionSelect"> so downstream `.value` reads work unchanged.
 */
function _createVersionDropdown(versions, selectedVersion) {
  const wrap = document.createElement('div');
  wrap.className = 'data-model-dropdown';

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.id = 'versionDropdownTrigger';
  trigger.className = 'data-model-dropdown-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  const triggerLabel = document.createElement('span');
  triggerLabel.className = 'data-model-dropdown-label';
  trigger.appendChild(triggerLabel);

  /* Outer panel clips the rounded corner; inner list holds the scrollbar. */
  const panel = document.createElement('div');
  panel.className = 'data-model-dropdown-panel';
  panel.hidden = true;
  const list = document.createElement('ul');
  list.className = 'data-model-dropdown-list';
  list.setAttribute('role', 'listbox');
  panel.appendChild(list);

  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.id = 'versionSelect';

  wrap.appendChild(trigger);
  wrap.appendChild(panel);
  wrap.appendChild(hidden);

  _populateVersionDropdown(wrap, versions, selectedVersion);
  _attachDropdownInteractions(wrap);
  return wrap;
}

function _populateVersionDropdown(wrap, versions, selectedVersion) {
  const triggerLabel = wrap.querySelector('.data-model-dropdown-label');
  const list = wrap.querySelector('.data-model-dropdown-list');
  const hidden = wrap.querySelector('input[type="hidden"]');

  list.textContent = '';
  const sorted = _sortVersionsDescending(versions);
  const wantedNumber = String(selectedVersion?.version_number ?? selectedVersion ?? '');
  let matched = false;

  for (const v of sorted) {
    const versionNumber = String(v.version_number);
    const item = document.createElement('li');
    item.className = 'data-model-dropdown-item';
    item.setAttribute('role', 'option');
    item.dataset.value = versionNumber;
    item.textContent = _versionDisplayLabel(v);
    if (versionNumber === wantedNumber) {
      item.setAttribute('aria-selected', 'true');
      triggerLabel.textContent = item.textContent;
      hidden.value = versionNumber;
      matched = true;
    }
    list.appendChild(item);
  }

  /* Fall back to first option when the requested selection isn't present (model swap clears it). */
  if (!matched && sorted.length > 0) {
    const first = sorted[0];
    triggerLabel.textContent = _versionDisplayLabel(first);
    hidden.value = String(first.version_number);
    list.firstElementChild?.setAttribute('aria-selected', 'true');
  } else if (sorted.length === 0) {
    triggerLabel.textContent = '';
    hidden.value = '';
  }
}

function _attachDropdownInteractions(wrap) {
  const trigger = wrap.querySelector('.data-model-dropdown-trigger');
  const panel = wrap.querySelector('.data-model-dropdown-panel');
  const triggerLabel = wrap.querySelector('.data-model-dropdown-label');
  const hidden = wrap.querySelector('input[type="hidden"]');

  /* Panel uses position: fixed so it escapes the modal dialog's overflow bounds.
   * Reposition on open and on viewport changes so it tracks the trigger. */
  const reposition = () => {
    const rect = trigger.getBoundingClientRect();
    panel.style.top = `${rect.bottom + 4}px`;
    panel.style.left = `${rect.left}px`;
    panel.style.width = `${rect.width}px`;
  };
  const close = () => {
    panel.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    window.removeEventListener('resize', reposition);
    window.removeEventListener('scroll', reposition, true);
  };
  const open = () => {
    reposition();
    panel.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    window.addEventListener('resize', reposition);
    /* Capture phase catches scroll inside the dialog, not just on window. */
    window.addEventListener('scroll', reposition, true);
  };

  trigger.addEventListener('click', (event) => {
    event.stopPropagation();
    panel.hidden ? open() : close();
  });

  panel.addEventListener('click', (event) => {
    const item = event.target.closest('.data-model-dropdown-item');
    if (!item) return;
    panel.querySelectorAll('[aria-selected="true"]').forEach((el) => el.removeAttribute('aria-selected'));
    item.setAttribute('aria-selected', 'true');
    triggerLabel.textContent = item.textContent;
    hidden.value = item.dataset.value || '';
    close();
  });

  /* Close on outside click or Escape. */
  document.addEventListener('click', (event) => {
    if (!wrap.contains(event.target)) close();
  });
  trigger.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
  });
}

function _versionDisplayLabel(version) {
  if (version.external_version_number) {
    return version.external_version_number;
  }
  return version.version_label || `v${version.version_number}`;
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
  versionLabel.htmlFor = 'versionDropdownTrigger';
  versionLabel.textContent = 'Version';

  const defaultModel = dataModels.find((m) => m.key === DEFAULT_DATA_MODEL) || dataModels[0];
  const versions = defaultModel?.versions ?? [];
  const defaultVersion = _getLatestVersion(versions);
  const versionDropdown = _createVersionDropdown(versions, defaultVersion);

  versionField.appendChild(versionLabel);
  versionField.appendChild(versionDropdown);
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

/** Wire data model <select> change to repopulate the version dropdown. */
function _setupModelChangeHandler(dialog, dataModels) {
  const modelSelect = dialog.querySelector('#dataModelSelect');
  const versionDropdown = dialog.querySelector('.data-model-dropdown');

  modelSelect.addEventListener('change', () => {
    const selected = dataModels.find((m) => m.key === modelSelect.value);
    const versions = selected?.versions ?? [];
    const latestVersion = _getLatestVersion(versions);
    _populateVersionDropdown(versionDropdown, versions, latestVersion);
  });
}

/**
 * Show the data model selection popup.
 * @returns {Promise<{dataModelKey: string, versionNumber: number} | null>}
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

    const dataModelSelect = /** @type {HTMLSelectElement|null} */ (dialog.querySelector('#dataModelSelect'));
    const versionSelect = /** @type {HTMLSelectElement|null} */ (dialog.querySelector('#versionSelect'));
    const confirmBtn = dialog.querySelector('.data-model-confirm-btn');

    confirmBtn?.addEventListener('click', () => {
      if (!dataModelSelect || !versionSelect) return;
      markResolved();
      const dataModelKey = dataModelSelect.value;
      const versionNumber = Number(versionSelect.value);
      dialog.close();
      dialog.remove();
      resolve({ dataModelKey, versionNumber });
    });
  });
}
