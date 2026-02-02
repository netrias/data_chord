import { initStepInstruction, setActiveStage, initNavigationEvents, advanceMaxReachedStage } from '/assets/shared/step-instruction-ui.js';
import { STAGE_2_PAYLOAD_KEY, STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, CURRENT_FILE_SESSION_KEY, removeFromSession, writeToSession, readFromSession } from '/assets/shared/storage-keys.js';
import { showDataModelPopup } from './data_model_popup.js';

const config = window.stageOneUploadConfig ?? {};

const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('fileInput');
const analyzeButton = document.getElementById('analyzeButton');
const statusMessage = document.getElementById('statusMessage');
const dropzoneCopy = document.getElementById('dropzoneCopy');
const dropzoneFile = document.getElementById('dropzoneFile');
const dropzoneFileName = document.getElementById('dropzoneFileName');
const dropzoneFileSize = document.getElementById('dropzoneFileSize');
const dropzoneFileStatus = document.getElementById('dropzoneFileStatus');
const changeFileButton = document.getElementById('changeFileButton');
const analyzeOverlay = document.getElementById('analyzeOverlay');

const state = {
  file: null,
  uploaded: null,
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

const _setAnalyzeButtonVisible = (visible) => {
  if (!analyzeButton) return;
  analyzeButton.classList.toggle('reserve-space', !visible);
  analyzeButton.disabled = !visible;
};

const _showDropzoneCopy = () => {
  if (dropzoneCopy) dropzoneCopy.classList.remove('hidden');
  if (dropzoneFile) dropzoneFile.classList.add('hidden');
};

const _showDropzoneSummary = (file, statusText) => {
  if (dropzoneCopy) dropzoneCopy.classList.add('hidden');
  if (dropzoneFile) dropzoneFile.classList.remove('hidden');
  if (dropzoneFileName) dropzoneFileName.textContent = file.name;
  if (dropzoneFileSize) dropzoneFileSize.textContent = _formatBytes(file.size);
  if (dropzoneFileStatus) dropzoneFileStatus.textContent = statusText;
};

const _openFilePicker = () => {
  if (!fileInput) return;
  fileInput.value = '';
  fileInput.click();
};

const _resetUploadState = () => {
  state.file = null;
  state.uploaded = null;
  state.isUploading = false;
  state.isAnalyzing = false;
  if (fileInput) fileInput.value = '';
  _setAnalyzeButtonVisible(false);
  if (dropzone) dropzone.classList.remove('has-file');
  _showDropzoneCopy();
  _setStatus('');
  setActiveStage('upload');
};

const _validateFile = (file) => {
  const errors = [];
  if (!file) {
    errors.push('No file detected.');
  }
  if (file && !file.name.toLowerCase().endsWith('.csv')) {
    errors.push('Only CSV files are supported right now.');
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
  _setAnalyzeButtonVisible(false);
  _showDropzoneSummary(state.file, 'Uploading…');

  const formData = new FormData();
  formData.append('file', state.file);

  try {
    const response = await fetch(config.uploadEndpoint, {
      method: 'POST',
      body: formData,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || 'Upload failed.');
    }
    state.uploaded = payload;
    _persistFileSession(payload, state.file.name);
    if (dropzone) dropzone.classList.add('has-file');
    _showDropzoneSummary(state.file, 'Uploaded');
    _setAnalyzeButtonVisible(true);
  } catch (error) {
    console.error(error);
    _showDropzoneSummary(state.file, 'Upload failed');
    _setStatus(error.message, 'error');
  } finally {
    state.isUploading = false;
  }
};

const _clearStaleSessionData = () => {
  removeFromSession(STAGE_2_PAYLOAD_KEY);
  removeFromSession(STAGE_3_PAYLOAD_KEY);
  removeFromSession(STAGE_3_JOB_KEY);
  removeFromSession(CURRENT_FILE_SESSION_KEY);
};

/* why: persist file session to enable navigation back to Stage 1 with file context. */
const _persistFileSession = (uploadResponse, fileName) => {
  writeToSession(CURRENT_FILE_SESSION_KEY, {
    file_id: uploadResponse.file_id,
    original_name: fileName,
    uploaded_at: new Date().toISOString(),
    size_bytes: uploadResponse.size_bytes,
  });
};

/* why: restore file display from session when returning to Stage 1. */
const _hydrateFromSession = () => {
  const session = readFromSession(CURRENT_FILE_SESSION_KEY);
  if (!session || !session.file_id || !session.original_name) {
    return false;
  }

  state.uploaded = { file_id: session.file_id, size_bytes: session.size_bytes };
  state.file = { name: session.original_name, size: session.size_bytes };

  if (dropzone) dropzone.classList.add('has-file');
  _showDropzoneSummary(state.file, 'Uploaded');
  _setAnalyzeButtonVisible(true);
  return true;
};

const _persistStageTwoPayload = (payload) => {
  writeToSession(STAGE_2_PAYLOAD_KEY, payload);
};

const _navigateToStageTwo = (fileId, targetSchema, payload) => {
  _persistStageTwoPayload(payload);
  advanceMaxReachedStage('mapping');
  const search = new URLSearchParams({ file_id: fileId, schema: targetSchema });
  window.location.assign(`/stage-2?${search.toString()}`);
};

const CREDENTIAL_ERROR_MESSAGE =
  'AI mapping service unavailable. Please configure NETRIAS_API_KEY and restart the server.';

const _analyzeDataset = async () => {
  if (!state.uploaded || state.isAnalyzing) {
    _setStatus('Upload a file before analyzing.', 'error');
    return;
  }

  /* Show data model selection popup before starting analysis. */
  const selection = await showDataModelPopup();
  if (!selection) {
    /* User cancelled - stay on Stage 1. */
    return;
  }
  /* TEMP DEMO: versionLabel is captured but not yet sent to the backend.
     Will be included in the analyze request once the API supports versioned discovery. */

  state.isAnalyzing = true;
  if (analyzeButton) analyzeButton.disabled = true;
  _showDropzoneSummary(state.file, 'Analyzing columns…');
  if (analyzeOverlay) analyzeOverlay.classList.remove('hidden');

  try {
    const response = await fetch(config.analyzeEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        file_id: state.uploaded.file_id,
        target_schema: selection.dataModelKey,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || 'Analysis failed.');
    }
    if (payload.mapping_service_available === false) {
      throw new Error(CREDENTIAL_ERROR_MESSAGE);
    }
    // Keep overlay visible during navigation - browser will replace the page
    _navigateToStageTwo(state.uploaded.file_id, selection.dataModelKey, payload);
  } catch (error) {
    console.error(error);
    _setStatus(error.message, 'error');
    _setAnalyzeButtonVisible(true);
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

  if (analyzeButton) {
    analyzeButton.addEventListener('click', _analyzeDataset);
  }
};

_init();
