import StageThreeMetricsDashboard from './metrics/dashboard.js';
import buildDashboardDataset from './metrics/manifest_adapter.js';
import { initStepInstruction, updateStepInstruction } from '/assets/shared/step-instruction-ui.js';

const config = window.stageThreeConfig ?? {};
const harmonizeEndpoint = config.harmonizeEndpoint ?? '/stage-3/harmonize';
const storageKey = config.storageKey ?? 'stage3HarmonizePayload';
const jobStorageKey = config.jobStorageKey ?? 'stage3HarmonizeJob';
const nextStageUrl = config.nextStageUrl ?? '/stage-4';
const stageTwoUrl = config.stageTwoUrl ?? '/stage-2';

const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
const loadingState = document.getElementById('loadingState');
const jobIdDisplay = document.getElementById('jobIdDisplay');
const reviewButton = document.getElementById('reviewButton');
const retryButton = document.getElementById('retryButton');
const emptyState = document.getElementById('stageThreeEmptyState');
const errorBanner = document.getElementById('stageThreeError');
const stageThreeTitle = document.getElementById('stageThreeTitle');
const returnToStageTwo = document.getElementById('returnToStageTwo');
const metadataBar = document.getElementById('metadataBar');
const metadataPreview = document.getElementById('metadataPreview');
const metaFileName = document.getElementById('metaFileName');
const metaRowCount = document.getElementById('metaRowCount');
const metaSchemaValue = document.getElementById('metaSchemaValue');
const metaJobId = document.getElementById('metaJobId');
const loadingSpinner = document.querySelector('#loadingState .loading-spinner');

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

const normalizeStatus = (status) => (status ?? '').toString().trim().toLowerCase();
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

const toggleLoadingState = (show) => {
  loadingState.classList.toggle('hidden', !show);
};

const toggleEmptyState = (show) => {
  emptyState.classList.toggle('hidden', !show);
};

const toggleSpinner = (show) => {
  loadingSpinner.classList.toggle('hidden', !show);
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
    if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonization failed';
    return;
  }
  if (isCompleteStatus(normalized)) {
    if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonization complete';
    return;
  }
  if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonizing';
};

const hideJobMeta = () => {
  if (jobIdDisplay) {
    jobIdDisplay.classList.add('hidden');
  }
};

const showJobId = (jobId) => {
  if (jobIdDisplay && jobId) {
    jobIdDisplay.textContent = `Job ID: ${jobId}`;
    jobIdDisplay.classList.remove('hidden');
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
  showJobId(job.job_id);

  const status = job.status ?? 'running';
  const normalized = normalizeStatus(status);
  updateTitleForStatus(status);
  clearError();

  if (isFailedStatus(normalized)) {
    toggleLoadingState(true);
    toggleSpinner(false);
    hideMetricsDashboard();
    showError(job.detail || 'Harmonization failed. Please retry.');
    reviewButton.disabled = true;
    retryButton.classList.remove('hidden');
  } else if (isCompleteStatus(normalized)) {
    toggleLoadingState(false);
    renderMetricsDashboard(job);
    reviewButton.disabled = false;
    retryButton.classList.add('hidden');
    updateStepInstruction('harmonize_complete');
  } else {
    toggleLoadingState(true);
    toggleSpinner(true);
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
  console.log('[Stage3] startHarmonize called, isProcessing:', state.isProcessing);
  if (state.isProcessing) {
    console.log('[Stage3] already processing, returning');
    return;
  }
  const payload = payloadOverride || state.requestBody || extractRequestPayload();
  console.log('[Stage3] resolved payload:', payload);
  if (!payload) {
    console.log('[Stage3] no payload in startHarmonize, showing empty state');
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

  state.isProcessing = true;
  console.log('[Stage3] about to fetch:', harmonizeEndpoint);
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
  console.log('[Stage3] init() starting');
  setActiveStage('harmonize');
  initStepInstruction('harmonize');

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

  console.log('[Stage3] checking for stored job at key:', jobStorageKey);
  const storedJob = readFromSession(jobStorageKey);
  console.log('[Stage3] stored job:', storedJob);

  if (hydrateFromStoredJob()) {
    console.log('[Stage3] hydrated from stored job, returning early');
    toggleLoadingState(false);
    return;
  }

  const payload = extractRequestPayload();
  console.log('[Stage3] extracted payload:', payload);
  if (!payload) {
    console.log('[Stage3] no payload, showing empty state');
    toggleLoadingState(false);
    toggleEmptyState(true);
    return;
  }

  console.log('[Stage3] calling startHarmonize with payload');
  startHarmonize(payload);
};

init();
