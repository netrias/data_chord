import StageThreeMetricsDashboard from './metrics/dashboard.js';
import buildDashboardDataset from './metrics/manifest_adapter.js';
import { initStepInstruction, updateStepInstruction, setActiveStage, initNavigationEvents, isSafeRelativeUrl } from '/assets/shared/step-instruction-ui.js';
import { STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, readFromSession, writeToSession, removeFromSession } from '/assets/shared/storage-keys.js';

const config = window.stageThreeConfig ?? {};
const harmonizeEndpoint = config.harmonizeEndpoint ?? '/stage-3/harmonize';
const nextStageUrl = config.nextStageUrl ?? '/stage-4';
const stageTwoUrl = config.stageTwoUrl ?? '/stage-2';

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
  context: null,
  isProcessing: false,
};

// "why: keep dashboard orchestration isolated from the job rendering logic."
const _hideMetricsDashboard = () => {
  if (metricsDashboard) {
    metricsDashboard.hide();
  }
};

// "why: expose a single call site for wiring harmonizer telemetry into the UI."
const _renderMetricsDashboard = (job) => {
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

const COMPLETE_STATUSES = new Set(['completed', 'succeeded', 'success', 'done']);
const FAILED_STATUSES = new Set(['failed', 'error', 'cancelled', 'canceled']);

const _normalizeStatus = (status) => (status ?? '').toString().trim().toLowerCase();
const _isCompleteStatus = (normalized) => COMPLETE_STATUSES.has(normalized);
const _isFailedStatus = (normalized) => FAILED_STATUSES.has(normalized);

const _toggleLoadingState = (show) => {
  loadingState.classList.toggle('hidden', !show);
};

const _toggleEmptyState = (show) => {
  emptyState.classList.toggle('hidden', !show);
};

const _toggleSpinner = (show) => {
  if (loadingSpinner) {
    loadingSpinner.classList.toggle('hidden', !show);
  }
};

const _updateMetadata = (context) => {
  // "why: store context for later use when showing job summary."
  if (context) {
    state.context = context;
  }
};

const _updateMetadataBar = (job) => {
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

const _clearError = () => {
  errorBanner.classList.add('hidden');
  errorBanner.textContent = '';
};

const _showError = (message) => {
  errorBanner.textContent = message;
  errorBanner.classList.remove('hidden');
};

const _persistJob = (job) => {
  writeToSession(STAGE_3_JOB_KEY, job);
};

const _handleContinue = () => {
  const serverUrl = state.job?.next_stage_url;
  const nextUrl = isSafeRelativeUrl(serverUrl) ? serverUrl : nextStageUrl;
  window.location.assign(nextUrl);
};

const _handleRetry = () => {
  if (!state.payload && !state.requestBody) {
    return;
  }
  /* Preserve requestBody before clearing job - startHarmonize uses it. */
  const savedRequestBody = state.requestBody;
  state.job = null;
  _persistJobMeta(null);
  reviewButton.disabled = true;
  retryButton.classList.add('hidden');
  _clearError();
  _hideJobMeta();
  _startHarmonize(savedRequestBody);
};

const _persistJobMeta = (job) => {
  if (job) {
    _persistJob(job);
  } else {
    removeFromSession(STAGE_3_JOB_KEY);
  }
};

const _updateTitleForStatus = (status) => {
  const normalized = _normalizeStatus(status);
  if (normalized === 'failed') {
    if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonization failed';
    return;
  }
  if (_isCompleteStatus(normalized)) {
    if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonization complete';
    return;
  }
  if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonizing';
};

const _hideJobMeta = () => {
  if (jobIdDisplay) {
    jobIdDisplay.classList.add('hidden');
  }
};

const _showJobId = (jobId) => {
  if (jobIdDisplay && jobId) {
    jobIdDisplay.textContent = `Job ID: ${jobId}`;
    jobIdDisplay.classList.remove('hidden');
  }
};

const _renderJob = (job) => {
  // "why: update UI based on job status; metadata bar always visible."
  if (!job) {
    return;
  }
  state.job = job;
  _persistJobMeta(job);
  _updateMetadataBar(job);
  _showJobId(job.job_id);

  const status = job.status ?? 'running';
  const normalized = _normalizeStatus(status);
  _updateTitleForStatus(status);
  _clearError();

  if (_isFailedStatus(normalized)) {
    _toggleLoadingState(true);
    _toggleSpinner(false);
    _hideMetricsDashboard();
    _showError(job.detail || 'Harmonization failed. Please retry.');
    reviewButton.disabled = true;
    retryButton.classList.remove('hidden');
  } else if (_isCompleteStatus(normalized)) {
    _toggleLoadingState(false);
    _renderMetricsDashboard(job);
    reviewButton.disabled = false;
    retryButton.classList.add('hidden');
    updateStepInstruction('harmonize_complete');
  } else {
    _toggleLoadingState(true);
    _toggleSpinner(true);
    _hideMetricsDashboard();
    reviewButton.disabled = true;
    retryButton.classList.add('hidden');
  }
};

const _extractRequestPayload = () => {
  // "why: extract session payload and populate metadata bar early."
  let payload = readFromSession(STAGE_3_PAYLOAD_KEY);
  if (payload && payload.context) {
    _updateMetadata(payload.context);
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
  _updateMetadataBar(null);
  return harmonizePayload;
};

/** Get the current file_id from URL parameters. */
const _getFileIdFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('file_id');
};

const _startHarmonize = async (payloadOverride = null) => {
  if (state.isProcessing) {
    return;
  }
  const payload = payloadOverride || state.requestBody || _extractRequestPayload();
  if (!payload) {
    _toggleLoadingState(false);
    _toggleEmptyState(true);
    _hideJobMeta();
    _hideMetricsDashboard();
    return;
  }

  _hideJobMeta();
  state.requestBody = payload;

  _clearError();
  _hideMetricsDashboard();
  _toggleEmptyState(false);
  _toggleLoadingState(true);
  reviewButton.disabled = true;
  retryButton.classList.add('hidden');

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
    _renderJob(body);
  } catch (error) {
    console.error(error);
    _showError(error.message || 'Unexpected error while launching harmonization.');
    _toggleLoadingState(false);
    _hideJobMeta();
    retryButton.classList.remove('hidden');
  } finally {
    state.isProcessing = false;
  }
};

const _hydrateFromStoredJob = () => {
  // "why: verify stored job belongs to current file_id to prevent stale state."
  const job = readFromSession(STAGE_3_JOB_KEY);
  if (!job) {
    return false;
  }

  const currentFileId = _getFileIdFromUrl();
  const storedFileId = job.file_id ?? state.requestBody?.file_id;

  // If URL has file_id and it doesn't match stored job, clear stale job
  if (currentFileId && storedFileId && currentFileId !== storedFileId) {
    removeFromSession(STAGE_3_JOB_KEY);
    return false;
  }

  if (!state.payload) {
    _extractRequestPayload();
  }
  _renderJob(job);
  return true;
};

const _init = () => {
  setActiveStage('harmonize');
  initStepInstruction('harmonize');
  initNavigationEvents();

  if (reviewButton) {
    reviewButton.addEventListener('click', _handleContinue);
  }
  if (retryButton) {
    retryButton.addEventListener('click', _handleRetry);
  }
  if (returnToStageTwo) {
    returnToStageTwo.addEventListener('click', () => {
      window.location.assign(stageTwoUrl);
    });
  }

  if (_hydrateFromStoredJob()) {
    _toggleLoadingState(false);
    return;
  }

  const payload = _extractRequestPayload();
  if (!payload) {
    _toggleLoadingState(false);
    _toggleEmptyState(true);
    return;
  }

  _startHarmonize(payload);
};

_init();
