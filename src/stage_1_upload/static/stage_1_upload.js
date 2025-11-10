const config = window.stageOneUploadConfig ?? {};
const STORAGE_KEY = 'stage2Payload';

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
const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
const analyzeOverlay = document.getElementById('analyzeOverlay');

const stageOrder = ['upload', 'mapping', 'harmonize', 'review', 'export'];

const state = {
  file: null,
  uploaded: null,
  isUploading: false,
  isAnalyzing: false,
};

const setActiveStage = (stage) => {
  const targetIndex = stageOrder.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = stageOrder.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

const formatBytes = (bytes) => {
  if (!bytes && bytes !== 0) {
    return '—';
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

const setStatus = (message = '', tone = '') => {
  statusMessage.textContent = message;
  statusMessage.classList.remove('error', 'success');
  if (tone) {
    statusMessage.classList.add(tone);
  }
};

const showDropzoneCopy = () => {
  dropzoneCopy.classList.remove('hidden');
  dropzoneFile.classList.add('hidden');
};

const showDropzoneSummary = (file, statusText) => {
  dropzoneCopy.classList.add('hidden');
  dropzoneFile.classList.remove('hidden');
  dropzoneFileName.textContent = file.name;
  dropzoneFileSize.textContent = formatBytes(file.size);
  dropzoneFileStatus.textContent = statusText;
};

const openFilePicker = () => {
  fileInput.value = '';
  fileInput.click();
};

const resetUploadState = () => {
  state.file = null;
  state.uploaded = null;
  state.isUploading = false;
  state.isAnalyzing = false;
  fileInput.value = '';
  analyzeButton.classList.add('hidden');
  analyzeButton.disabled = true;
  showDropzoneCopy();
  setStatus('');
  setActiveStage('upload');
};

const validateFile = (file) => {
  const errors = [];
  if (!file) {
    errors.push('No file detected.');
  }
  if (file && !file.name.toLowerCase().endsWith('.csv')) {
    errors.push('Only CSV files are supported right now.');
  }
  if (file && config.maxBytes && file.size > Number(config.maxBytes)) {
    errors.push(`File exceeds the ${formatBytes(Number(config.maxBytes))} limit.`);
  }
  return errors;
};

const handleFileSelection = (file) => {
  const issues = validateFile(file);
  if (issues.length) {
    showDropzoneCopy();
    setStatus(issues.join(' '), 'error');
    return;
  }
  state.file = file;
  state.uploaded = null;
  showDropzoneSummary(file, 'Ready to upload');
  setStatus('');
  uploadDataset();
};

const uploadDataset = async () => {
  if (!state.file || state.isUploading) {
    return;
  }
  state.isUploading = true;
  analyzeButton.classList.add('hidden');
  analyzeButton.disabled = true;
  showDropzoneSummary(state.file, 'Uploading…');

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
    showDropzoneSummary(state.file, 'Uploaded');
    analyzeButton.classList.remove('hidden');
    analyzeButton.disabled = false;
  } catch (error) {
    console.error(error);
    showDropzoneSummary(state.file, 'Upload failed');
    setStatus(error.message, 'error');
  } finally {
    state.isUploading = false;
  }
};

const persistStageTwoPayload = (payload) => {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch (error) {
    console.warn('Unable to persist stage 2 payload', error);
  }
};

const navigateToStageTwo = (fileId, targetSchema, payload) => {
  persistStageTwoPayload(payload);
  const search = new URLSearchParams({ file_id: fileId, schema: targetSchema });
  window.location.assign(`/stage-2?${search.toString()}`);
};

const analyzeDataset = async () => {
  if (!state.uploaded || state.isAnalyzing) {
    setStatus('Upload a file before analyzing.', 'error');
    return;
  }

  state.isAnalyzing = true;
  analyzeButton.disabled = true;
  showDropzoneSummary(state.file, 'Analyzing columns…');
  analyzeOverlay.classList.remove('hidden');

  try {
    const response = await fetch(config.analyzeEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        file_id: state.uploaded.file_id,
        target_schema: config.targetSchema,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || 'Analysis failed.');
    }
    setStatus('Columns analyzed. Redirecting…', 'success');
    navigateToStageTwo(state.uploaded.file_id, config.targetSchema, payload);
  } catch (error) {
    console.error(error);
    setStatus(error.message, 'error');
    analyzeButton.disabled = false;
    showDropzoneSummary(state.file, 'Uploaded');
  } finally {
    state.isAnalyzing = false;
    analyzeOverlay.classList.add('hidden');
  }
};

const wireDragEvents = () => {
  const preventDefaults = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((eventName) => {
    dropzone.addEventListener(eventName, preventDefaults);
  });

  ['dragenter', 'dragover'].forEach((eventName) => {
    dropzone.addEventListener(eventName, () => dropzone.classList.add('dragging'));
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    dropzone.addEventListener(eventName, () => dropzone.classList.remove('dragging'));
  });

  dropzone.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      handleFileSelection(file);
    }
  });
};

const init = () => {
  resetUploadState();
  wireDragEvents();

  dropzone.addEventListener('click', () => openFilePicker());
  dropzone.addEventListener('keyup', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openFilePicker();
    }
  });

  if (changeFileButton) {
    changeFileButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      resetUploadState();
      openFilePicker();
    });
  }

  fileInput.addEventListener('change', (event) => {
    const files = event.target.files || [];
    const file = files[0];
    if (file) {
      handleFileSelection(file);
    }
  });

  analyzeButton.addEventListener('click', analyzeDataset);
};

init();
