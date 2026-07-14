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
  return models
    .map((m) => ({
      ...m,
      versions: (m.versions || []).filter((v) => v.external_version_number),
    }))
    .filter((m) => m.versions.length > 0);
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
  return [...versions].sort((a, b) => (
    _compareVersions(b.external_version_number, a.external_version_number)
  ));
}

function _getLatestVersion(versions) {
  if (!versions || versions.length === 0) return '';
  return _sortVersionsDescending(versions)[0];
}

function _scheduleRetry() {
  if (_retryScheduled) return;
  _retryScheduled = true;
  setTimeout(() => {
    _retryScheduled = false;
    _preloadPromise = null;
    preloadDataModels();
  }, RETRY_DELAY_MS);
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

const _dropdownState = new WeakMap();

/**
 * Build a custom dropdown for model/version choices.
 * Replaces native <select> because macOS Chrome overlays the picker on the trigger
 * (centers the selected option on the trigger position). A custom dropdown lets the
 * panel always render below the trigger without covering it.
 */
function _createDropdown({
  className,
  triggerId,
  hiddenId,
  hiddenControl,
  items,
  selectedValue,
  valueFor,
  labelFor,
}) {
  const wrap = document.createElement('div');
  wrap.className = `data-model-dropdown ${className}`;

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.id = triggerId;
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

  const hidden = hiddenControl === 'select'
    ? document.createElement('select')
    : document.createElement('input');
  hidden.id = hiddenId;
  if (hiddenControl === 'select') {
    hidden.className = 'sr-only';
    hidden.tabIndex = -1;
    hidden.setAttribute('aria-hidden', 'true');
  } else {
    hidden.setAttribute('type', 'hidden');
  }

  wrap.appendChild(trigger);
  wrap.appendChild(panel);
  wrap.appendChild(hidden);

  _dropdownState.set(wrap, { valueFor, labelFor });
  _populateDropdown(wrap, items, selectedValue);
  _attachDropdownInteractions(wrap);
  return wrap;
}

function _createModelDropdown(dataModels, selectedKey) {
  return _createDropdown({
    className: 'data-model-dropdown--model',
    triggerId: 'dataModelDropdownTrigger',
    hiddenId: 'dataModelSelect',
    hiddenControl: 'select',
    items: dataModels,
    selectedValue: selectedKey,
    valueFor: (model) => model.data_model_key,
    labelFor: (model) => model.label || model.data_model_key,
  });
}

function _createVersionDropdown(versions, selectedVersion) {
  return _createDropdown({
    className: 'data-model-dropdown--version',
    triggerId: 'versionDropdownTrigger',
    hiddenId: 'versionSelect',
    hiddenControl: 'input',
    items: _sortVersionsDescending(versions),
    selectedValue: selectedVersion?.external_version_number ?? selectedVersion,
    valueFor: (version) => version.external_version_number,
    labelFor: _versionDisplayLabel,
  });
}

function _populateVersionDropdown(wrap, versions, selectedVersion) {
  _populateDropdown(
    wrap,
    _sortVersionsDescending(versions),
    selectedVersion?.external_version_number ?? selectedVersion,
  );
}

function _populateDropdown(wrap, items, selectedValue) {
  const state = _dropdownState.get(wrap);
  const triggerLabel = wrap.querySelector('.data-model-dropdown-label');
  const list = wrap.querySelector('.data-model-dropdown-list');
  const hidden = wrap.querySelector('input[type="hidden"], select');

  list.textContent = '';
  if (hidden.tagName === 'SELECT') hidden.textContent = '';

  const wantedValue = String(selectedValue ?? '');
  let selected = null;

  for (const itemData of items) {
    const value = String(state.valueFor(itemData));
    const label = state.labelFor(itemData);
    const item = document.createElement('li');
    item.className = 'data-model-dropdown-item';
    item.setAttribute('role', 'option');
    item.dataset.value = value;
    item.textContent = label;
    list.appendChild(item);

    if (hidden.tagName === 'SELECT') {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      hidden.appendChild(option);
    }
    if (value === wantedValue) selected = { value, label };
  }

  if (!selected && items.length > 0) {
    const first = items[0];
    selected = {
      value: String(state.valueFor(first)),
      label: state.labelFor(first),
    };
  }

  if (selected) {
    _setDropdownValue(wrap, selected.value, selected.label, { dispatchChange: false });
  } else {
    triggerLabel.textContent = '';
    hidden.value = '';
  }
}

function _setDropdownValue(wrap, value, label, { dispatchChange = true } = {}) {
  const triggerLabel = wrap.querySelector('.data-model-dropdown-label');
  const list = wrap.querySelector('.data-model-dropdown-list');
  const hidden = wrap.querySelector('input[type="hidden"], select');

  triggerLabel.textContent = label;
  hidden.value = value;
  list.querySelectorAll('[aria-selected="true"]').forEach((el) => el.removeAttribute('aria-selected'));
  const selectedItem = Array.from(list.children).find((item) => item.dataset.value === value);
  selectedItem?.setAttribute('aria-selected', 'true');
  if (dispatchChange) hidden.dispatchEvent(new Event('change', { bubbles: true }));
}

function _syncDropdownFromHidden(wrap) {
  const list = wrap.querySelector('.data-model-dropdown-list');
  const hidden = wrap.querySelector('input[type="hidden"], select');
  const selectedItem = Array.from(list.children).find((item) => item.dataset.value === hidden.value);
  if (selectedItem) {
    _setDropdownValue(
      wrap,
      selectedItem.dataset.value || '',
      selectedItem.textContent || '',
      { dispatchChange: false },
    );
  }
}

function _attachDropdownInteractions(wrap) {
  const trigger = wrap.querySelector('.data-model-dropdown-trigger');
  const panel = wrap.querySelector('.data-model-dropdown-panel');

  /* Panel uses position: fixed so it escapes the modal dialog's overflow bounds.
   * Reposition on open and on viewport changes so it tracks the trigger. */
  const reposition = () => {
    const rect = trigger.getBoundingClientRect();
    panel.style.top = `${rect.bottom + 4}px`;
    panel.style.left = `${rect.left}px`;
    panel.style.width = `${rect.width}px`;
  };
  const close = () => {
    if (panel.hidden) return;
    panel.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    window.removeEventListener('resize', reposition);
    window.removeEventListener('scroll', reposition, true);
    document.removeEventListener('pointerdown', outsidePointerDown, true);
  };
  const outsidePointerDown = (event) => {
    const path = event.composedPath();
    if (path.includes(wrap) || path.includes(panel)) return;
    close();
  };
  const items = () => Array.from(panel.querySelectorAll('.data-model-dropdown-item'));
  const selectedIndex = () => Math.max(0, items().findIndex((item) => item.getAttribute('aria-selected') === 'true'));
  const selectItem = (item) => {
    if (!item) return;
    _setDropdownValue(wrap, item.dataset.value || '', item.textContent || '');
    item.scrollIntoView({ block: 'nearest' });
  };
  const selectByOffset = (offset) => {
    const dropdownItems = items();
    if (!dropdownItems.length) return;
    const nextIndex = Math.min(dropdownItems.length - 1, Math.max(0, selectedIndex() + offset));
    selectItem(dropdownItems[nextIndex]);
  };
  const open = () => {
    document.querySelectorAll('.data-model-dropdown').forEach((dropdown) => {
      if (dropdown !== wrap) _closeDropdown(dropdown);
    });
    reposition();
    panel.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    window.addEventListener('resize', reposition);
    /* Capture phase catches scroll inside the dialog, not just on window. */
    window.addEventListener('scroll', reposition, true);
    document.addEventListener('pointerdown', outsidePointerDown, true);
  };

  _dropdownState.set(wrap, { ..._dropdownState.get(wrap), close });

  trigger.addEventListener('click', (event) => {
    event.stopPropagation();
    panel.hidden ? open() : close();
  });

  panel.addEventListener('click', (event) => {
    const item = event.target.closest('.data-model-dropdown-item');
    if (!item) return;
    _setDropdownValue(wrap, item.dataset.value || '', item.textContent || '');
    close();
  });

  trigger.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      close();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (panel.hidden) open();
      else selectByOffset(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      if (panel.hidden) return;
      event.preventDefault();
      const dropdownItems = items();
      selectItem(dropdownItems[event.key === 'Home' ? 0 : dropdownItems.length - 1]);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      panel.hidden ? open() : close();
    }
  });
}

function _closeDropdown(wrap) {
  _dropdownState.get(wrap)?.close?.();
}

function _versionDisplayLabel(version) {
  return version.external_version_number;
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
  modelLabel.htmlFor = 'dataModelDropdownTrigger';
  modelLabel.textContent = 'Data Model';
  const defaultModel = dataModels.find((m) => m.data_model_key === DEFAULT_DATA_MODEL) || dataModels[0];
  const modelDropdown = _createModelDropdown(dataModels, defaultModel?.data_model_key);
  modelField.appendChild(modelLabel);
  modelField.appendChild(modelDropdown);
  body.appendChild(modelField);

  const versionField = document.createElement('div');
  versionField.className = 'data-model-field';
  const versionLabel = document.createElement('label');
  versionLabel.htmlFor = 'versionDropdownTrigger';
  versionLabel.textContent = 'Version';

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
    dialog.querySelectorAll('.data-model-dropdown').forEach(_closeDropdown);
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
  const modelDropdown = dialog.querySelector('.data-model-dropdown--model');
  const versionDropdown = dialog.querySelector('.data-model-dropdown--version');

  modelSelect.addEventListener('change', () => {
    _syncDropdownFromHidden(modelDropdown);
    const selected = dataModels.find((m) => m.data_model_key === modelSelect.value);
    const versions = selected?.versions ?? [];
    const latestVersion = _getLatestVersion(versions);
    _populateVersionDropdown(versionDropdown, versions, latestVersion);
  });
}

/**
 * Show the data model selection popup.
 * @returns {Promise<{dataModelKey: string, externalVersionNumber: string} | null>}
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
      const externalVersionNumber = versionSelect.value;
      dialog.close();
      dialog.remove();
      resolve({ dataModelKey, externalVersionNumber });
    });
  });
}
