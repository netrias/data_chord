const config = window.stageThreeConfig ?? {};
const harmonizeEndpoint = config.harmonizeEndpoint ?? '/stage-3/harmonize';
const storageKey = config.storageKey ?? 'stage3HarmonizePayload';
const jobStorageKey = config.jobStorageKey ?? `${storageKey}:job`;
const nextStageUrl = config.nextStageUrl ?? '/stage-4';
const stageTwoUrl = config.stageTwoUrl ?? '/stage-2';

const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
const loadingState = document.getElementById('loadingState');
const loadingPrimaryText = document.getElementById('loadingPrimaryText');
const loadingSecondaryText = document.getElementById('loadingSecondaryText');
const jobIdValue = document.getElementById('jobIdValue');
const jobStatusValue = document.getElementById('jobStatusValue');
const reviewButton = document.getElementById('reviewButton');
const retryButton = document.getElementById('retryButton');
const emptyState = document.getElementById('stageThreeEmptyState');
const errorBanner = document.getElementById('stageThreeError');
const stageThreeTitle = document.getElementById('stageThreeTitle');
const stageThreeSubtitle = document.getElementById('stageThreeSubtitle');
const stageThreeMeta = document.getElementById('stageThreeMeta');
const returnToStageTwo = document.getElementById('returnToStageTwo');
const progressIndicators = [
  document.querySelector('#loadingState .loading-spinner'),
  document.querySelector('#loadingState .loading-pips'),
].filter(Boolean);

const state = {
  payload: null,
  requestBody: null,
  job: null,
  isProcessing: false,
};

const STAGE_ORDER = ['upload', 'mapping', 'harmonize', 'review', 'export'];

const setActiveStage = (stage) => {
  const targetIndex = STAGE_ORDER.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = STAGE_ORDER.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

const COMPLETE_STATUSES = new Set(['completed', 'succeeded', 'success', 'done']);
const FAILED_STATUSES = new Set(['failed', 'error', 'cancelled', 'canceled']);

const STATUS_LABEL_MAP = {
  queued: 'Harmonizing',
  running: 'Harmonizing',
  pending: 'Harmonizing',
  started: 'Harmonizing',
  harmonizing: 'Harmonizing',
  completed: 'Completed',
  succeeded: 'Completed',
  success: 'Completed',
  done: 'Completed',
  failed: 'Failed',
  error: 'Failed',
  cancelled: 'Failed',
  canceled: 'Failed',
};

const normalizeStatus = (status) => (status ?? '').toString().trim().toLowerCase();

const getDisplayStatus = (status) => {
  const normalized = normalizeStatus(status);
  return STATUS_LABEL_MAP[normalized] || (status ? status.toString() : 'Unknown');
};

const isCompleteStatus = (normalized) => COMPLETE_STATUSES.has(normalized);
const isFailedStatus = (normalized) => FAILED_STATUSES.has(normalized);

const safeJsonParse = (raw) => {
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn('Unable to parse JSON payload', error);
    return null;
  }
};

const readFromSession = (key) => {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? safeJsonParse(raw) : null;
  } catch (error) {
    console.warn('Unable to read sessionStorage', error);
    return null;
  }
};

const writeToSession = (key, value) => {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn('Unable to persist sessionStorage value', error);
  }
};

const removeFromSession = (key) => {
  try {
    sessionStorage.removeItem(key);
  } catch (error) {
    console.warn('Unable to remove sessionStorage value', error);
  }
};

const setLoadingCopy = (primary, secondary) => {
  if (primary) {
    loadingPrimaryText.textContent = primary;
  }
  if (secondary) {
    loadingSecondaryText.textContent = secondary;
  }
};

const toggleLoadingState = (show) => {
  loadingState.classList.toggle('hidden', !show);
};

const toggleEmptyState = (show) => {
  emptyState.classList.toggle('hidden', !show);
};

const toggleProgressIndicators = (show) => {
  // "why: ensure indeterminate spinner only appears while harmonization runs."
  progressIndicators.forEach((element) => {
    element.classList.toggle('hidden', !show);
  });
};

const updateMetadata = (context) => {
  if (!stageThreeMeta) {
    return;
  }
  if (!context) {
    stageThreeMeta.textContent = '';
    stageThreeMeta.classList.add('hidden');
    return;
  }
  const parts = [];
  if (context.fileName) {
    parts.push(context.fileName);
  }
  if (typeof context.totalRows === 'number') {
    const formatted = context.totalRows.toLocaleString();
    parts.push(`${formatted} rows`);
  }
  if (context.targetSchema) {
    parts.push(`Schema: ${context.targetSchema}`);
  }
  stageThreeMeta.textContent = parts.join(' · ');
  stageThreeMeta.classList.remove('hidden');
};

const clearError = () => {
  errorBanner.classList.add('hidden');
  errorBanner.textContent = '';
};

const showError = (message) => {
  errorBanner.textContent = message;
  errorBanner.classList.remove('hidden');
};

const persistJob = (job) => {
  writeToSession(jobStorageKey, job);
};

const handleContinue = () => {
  const nextUrl = state.job?.next_stage_url || nextStageUrl;
  window.location.assign(nextUrl);
};

const handleRetry = () => {
  if (!state.payload) {
    return;
  }
  state.job = null;
  persistJobMeta(null);
  reviewButton.disabled = true;
  retryButton.classList.add('hidden');
  clearError();
  hideJobMeta();
  startHarmonize();
};

const persistJobMeta = (job) => {
  if (job) {
    persistJob(job);
  } else {
    removeFromSession(jobStorageKey);
  }
};

const updateTitleForStatus = (status) => {
  const normalized = normalizeStatus(status);
  if (normalized === 'failed') {
    stageThreeTitle.textContent = 'Harmonization failed';
    stageThreeSubtitle.textContent = 'Retry the run or return to Stage 2 to adjust mappings.';
    return;
  }
  if (isCompleteStatus(normalized)) {
    stageThreeTitle.textContent = 'Harmonization complete';
    stageThreeSubtitle.textContent = 'Review the results to approve or override values.';
    return;
  }
  stageThreeTitle.textContent = 'Harmonization is running';
  stageThreeSubtitle.textContent = 'Feel free to monitor progress here while Netrias processes your dataset.';
};

const hideJobMeta = () => {
  if (jobStatusValue) {
    jobStatusValue.classList.add('hidden');
  }
  if (jobIdValue) {
    jobIdValue.classList.add('hidden');
  }
};

const updateStatusDisplay = (status, detail) => {
  const normalized = normalizeStatus(status);
  const label = getDisplayStatus(status);
  let secondary = detail;
  if (!secondary) {
    if (isFailedStatus(normalized)) {
      secondary = 'Harmonization failed. Please retry.';
    } else if (isCompleteStatus(normalized)) {
      secondary = 'Harmonization complete. Continue to review results.';
    } else {
      secondary = 'Hang tight while Netrias processes your dataset.';
    }
  }
  setLoadingCopy(`Status: ${label}`, secondary);
  if (jobStatusValue) {
    jobStatusValue.textContent = `Status: ${label}`;
    jobStatusValue.classList.remove('hidden');
  }
};

const updateJobIdDisplay = (job) => {
  if (!jobIdValue) {
    return;
  }
  if (job.job_id_available && job.job_id) {
    jobIdValue.textContent = `Job ID: ${job.job_id}`;
    jobIdValue.classList.remove('hidden');
  } else {
    jobIdValue.classList.add('hidden');
  }
};

const renderJob = (job) => {
  if (!job) {
    return;
  }
  state.job = job;
  persistJobMeta(job);
  const status = job.status ?? 'running';
  const normalized = normalizeStatus(status);
  updateTitleForStatus(status);
  updateStatusDisplay(status, job.detail);
  updateJobIdDisplay(job);
  const shouldShowLoader = !isCompleteStatus(normalized) && !isFailedStatus(normalized);
  toggleProgressIndicators(shouldShowLoader);
  clearError();
  if (isFailedStatus(normalized)) {
    showError(job.detail || 'Harmonization failed. Please retry.');
    reviewButton.disabled = true;
    retryButton.classList.remove('hidden');
  } else if (isCompleteStatus(normalized)) {
    reviewButton.disabled = false;
    retryButton.classList.add('hidden');
  } else {
    reviewButton.disabled = true;
    retryButton.classList.add('hidden');
  }
};

const extractRequestPayload = () => {
  let payload = readFromSession(storageKey);
  if (payload && payload.context) {
    updateMetadata(payload.context);
  }
  let harmonizePayload = payload?.request ?? payload;
  if (!harmonizePayload) {
    const params = new URLSearchParams(window.location.search);
    const fileId = params.get('file_id');
    const targetSchema = params.get('target_schema') || config.targetSchema;
    if (!fileId || !targetSchema) {
      return null;
    }
    harmonizePayload = {
      file_id: fileId,
      target_schema: targetSchema,
      manual_overrides: {},
      manifest: null,
    };
  }
  state.payload = payload;
  state.requestBody = harmonizePayload;
  return harmonizePayload;
};

const startHarmonize = async (payloadOverride = null) => {
  if (state.isProcessing) {
    return;
  }
  const payload = payloadOverride || state.requestBody || extractRequestPayload();
  if (!payload) {
    toggleLoadingState(false);
    toggleEmptyState(true);
    hideJobMeta();
    return;
  }

  hideJobMeta();
  state.requestBody = payload;

  clearError();
  toggleEmptyState(false);
  toggleLoadingState(true);
  reviewButton.disabled = true;
  retryButton.classList.add('hidden');
  setLoadingCopy('Harmonization in progress', '');

  state.isProcessing = true;
  try {
    const response = await fetch(harmonizeEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || 'Unable to start harmonization job.');
    }
    renderJob(body);
  } catch (error) {
    console.error(error);
    showError(error.message || 'Unexpected error while launching harmonization.');
    toggleLoadingState(false);
    hideJobMeta();
    retryButton.classList.remove('hidden');
  } finally {
    state.isProcessing = false;
  }
};

const hydrateFromStoredJob = () => {
  const job = readFromSession(jobStorageKey);
  if (job) {
    renderJob(job);
    return true;
  }
  return false;
};

const init = () => {
  setActiveStage('harmonize');

  if (reviewButton) {
    reviewButton.addEventListener('click', handleContinue);
  }
  if (retryButton) {
    retryButton.addEventListener('click', handleRetry);
  }
  if (returnToStageTwo) {
    returnToStageTwo.addEventListener('click', () => {
      window.location.assign(stageTwoUrl);
    });
  }

  if (hydrateFromStoredJob()) {
    toggleLoadingState(false);
    return;
  }

  const payload = extractRequestPayload();
  if (!payload) {
    toggleLoadingState(false);
    toggleEmptyState(true);
    return;
  }

  startHarmonize(payload);
};

init();
