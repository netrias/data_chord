/**
 * Data Model Selection Popup module.
 * Shows a modal for selecting data model and version before starting mapping.
 * Fetches available data models from GET /stage-1/data-models.
 */

const DATA_MODELS_ENDPOINT = '/stage-1/data-models';
/** Server-provided default; falls back to first available model if missing. */
const DEFAULT_DATA_MODEL = window.stageOneUploadConfig?.defaultDataModel ?? null;

/** Backend may return arbitrary labels; fetch avoids hardcoding model list. */
async function _fetchDataModels() {
  const resp = await fetch(DATA_MODELS_ENDPOINT);
  if (!resp.ok) {
    throw new Error(`Failed to fetch data models: ${resp.status}`);
  }
  return resp.json();
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
  const defaultVersion = versions.length > 0 ? versions[versions.length - 1] : '';
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
    _buildVersionOptions(versionSelect, versions, '');
    /* Auto-select latest version. */
    if (versions.length > 0) {
      versionSelect.value = versions[versions.length - 1];
    }
  });
}

/**
 * Show the data model selection popup.
 * @returns {Promise<{dataModelKey: string, versionLabel: string} | null>}
 *   Resolves with selection on confirm, or null on cancel/close.
 */
export async function showDataModelPopup() {
  let dataModels;
  try {
    dataModels = await _fetchDataModels();
  } catch (err) {
    console.error('Failed to load data models:', err);
    return null;
  }

  if (!dataModels || dataModels.length === 0) {
    console.error('No data models available');
    return null;
  }

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
