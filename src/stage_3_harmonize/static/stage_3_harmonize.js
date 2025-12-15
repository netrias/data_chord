import StageThreeMetricsDashboard from './metrics/dashboard.js';
import buildDashboardDataset from './metrics/manifest_adapter.js';

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
const jobStatusValue = document.getElementById('jobStatusValue');
const reviewButton = document.getElementById('reviewButton');
const retryButton = document.getElementById('retryButton');
const emptyState = document.getElementById('stageThreeEmptyState');
const errorBanner = document.getElementById('stageThreeError');
const stageThreeTitle = document.getElementById('stageThreeTitle');
const stageThreeSubtitle = document.getElementById('stageThreeSubtitle');
const returnToStageTwo = document.getElementById('returnToStageTwo');
const metadataBar = document.getElementById('metadataBar');
const metadataPreview = document.getElementById('metadataPreview');
const metaFileName = document.getElementById('metaFileName');
const metaRowCount = document.getElementById('metaRowCount');
const metaSchemaValue = document.getElementById('metaSchemaValue');
const metaJobId = document.getElementById('metaJobId');
const progressIndicators = [
  document.querySelector('#loadingState .loading-spinner'),
  document.querySelector('#loadingState .loading-pips'),
].filter(Boolean);

const metricsDashboard = StageThreeMetricsDashboard.initFromDom();

const state = {
  payload: null,
  requestBody: null,
  job: null,
  isProcessing: false,
};

// "why: keep dashboard orchestration isolated from the job rendering logic."
const hideMetricsDashboard = () => {
  if (metricsDashboard) {
    metricsDashboard.hide();
  }
};

// "why: expose a single call site for wiring harmonizer telemetry into the UI."
const renderMetricsDashboard = (job) => {
  if (!metricsDashboard) {
    return;
  }
  const dataset = buildDashboardDataset({ job, payload: state.payload });
  if (!dataset) {
    metricsDashboard.hide();
    return;
  }
  metricsDashboard.render(dataset);
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
  // "why: store context for later use when showing job summary."
  if (context) {
    state.context = context;
  }
};

const updateMetadataBar = (job) => {
  // "why: populate the collapsible metadata bar with session context."
  const context = state.context ?? state.payload?.context ?? {};
  const fileName = context.fileName;
  const rowCount = typeof context.totalRows === 'number' ? context.totalRows.toLocaleString() : null;
  const schema = context.targetSchema;
  const jobId = job?.job_id;

  if (metaFileName) {
    metaFileName.textContent = fileName ?? '—';
  }
  if (metaRowCount) {
    metaRowCount.textContent = rowCount ?? '—';
  }
  if (metaSchemaValue) {
    metaSchemaValue.textContent = schema ?? '—';
  }
  if (metaJobId) {
    metaJobId.textContent = jobId ?? '—';
  }

  // "why: update preview text to show key info at a glance."
  if (metadataPreview) {
    const parts = [];
    if (fileName) parts.push(fileName);
    if (rowCount) parts.push(`${rowCount} rows`);
    metadataPreview.textContent = parts.length ? parts.join(' · ') : 'Session info';
  }
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
    stageThreeSubtitle.classList.remove('hidden');
    return;
  }
  if (isCompleteStatus(normalized)) {
    stageThreeTitle.textContent = 'Harmonization complete';
    stageThreeSubtitle.classList.add('hidden');
    return;
  }
  stageThreeTitle.textContent = 'Harmonization is running';
  stageThreeSubtitle.textContent = 'Feel free to monitor progress here while Netrias processes your dataset.';
  stageThreeSubtitle.classList.remove('hidden');
};

const hideJobMeta = () => {
  if (jobStatusValue) {
    jobStatusValue.classList.add('hidden');
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

const renderJob = (job) => {
  // "why: update UI based on job status; metadata bar always visible."
  if (!job) {
    return;
  }
  state.job = job;
  persistJobMeta(job);
  updateMetadataBar(job);

  const status = job.status ?? 'running';
  const normalized = normalizeStatus(status);
  updateTitleForStatus(status);
  clearError();

  if (isFailedStatus(normalized)) {
    toggleLoadingState(true);
    toggleProgressIndicators(false);
    updateStatusDisplay(status, job.detail);
    hideMetricsDashboard();
    showError(job.detail || 'Harmonization failed. Please retry.');
    reviewButton.disabled = true;
    retryButton.classList.remove('hidden');
  } else if (isCompleteStatus(normalized)) {
    toggleLoadingState(false);
    renderMetricsDashboard(job);
    reviewButton.disabled = false;
    retryButton.classList.add('hidden');
  } else {
    toggleLoadingState(true);
    toggleProgressIndicators(true);
    updateStatusDisplay(status, job.detail);
    hideMetricsDashboard();
    reviewButton.disabled = true;
    retryButton.classList.add('hidden');
  }
};

const extractRequestPayload = () => {
  // "why: extract session payload and populate metadata bar early."
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
  updateMetadataBar(null);
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
    hideMetricsDashboard();
    return;
  }

  hideJobMeta();
  state.requestBody = payload;

  clearError();
  hideMetricsDashboard();
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
    if (!state.payload) {
      extractRequestPayload();
    }
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
