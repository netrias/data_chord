import StageThreeMetricsDashboard from './metrics/dashboard.js';
import buildDashboardDataset from './metrics/manifest_adapter.js';
import { initStepInstruction, updateStepInstruction, setActiveStage, initNavigationEvents, isSafeRelativeUrl, advanceMaxReachedStage } from '/assets/shared/step-instruction-ui.js';
import { STAGE_3_PAYLOAD_KEY, STAGE_3_JOB_KEY, readFromSession, writeToSession, removeFromSession } from '/assets/shared/storage-keys.js';

const config = window.stageThreeConfig ?? {};
const harmonizeEndpoint = config.harmonizeEndpoint ?? '/stage-3/harmonize';
const nextStageUrl = config.nextStageUrl ?? '/stage-4';
const stageTwoUrl = config.stageTwoUrl ?? '/stage-2';
const JOB_POLL_INTERVAL_MS = 3000;

const loadingState = document.getElementById('loadingState');
const jobIdDisplay = document.getElementById('jobIdDisplay');
const reviewButton = document.getElementById('reviewButton');
const retryButton = document.getElementById('retryButton');
const emptyState = document.getElementById('stageThreeEmptyState');
const errorBanner = document.getElementById('stageThreeError');
const stageThreeTitle = document.getElementById('stageThreeTitle');
const returnToStageTwo = document.getElementById('returnToStageTwo');
const harmonizeAnimation = document.querySelector('#loadingState .harmonize-animation');
const harmonizeProgressMessage = document.getElementById('harmonizeProgressMessage');

const metricsDashboard = StageThreeMetricsDashboard.initFromDom();

const state = {
  payload: null,
  requestBody: null,
  job: null,
  isProcessing: false,
  pollTimer: null,
};

/* why: keep dashboard orchestration isolated from the job rendering logic. */
const _hideMetricsDashboard = () => {
  if (metricsDashboard) {
    metricsDashboard.hide();
  }
};

/* why: expose a single call site for wiring harmonizer telemetry into the UI. */
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

/* why: animation is purely decorative - failures should not break page functionality. */
const _toggleAnimation = (show) => {
  try {
    if (harmonizeAnimation) {
      harmonizeAnimation.classList.toggle('hidden', !show);
    }
  } catch {
    /* Animation toggle failed - page continues to work without it. */
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

const _jobWithCurrentFile = (job) => ({
  ...job,
  file_id: job.file_id ?? state.requestBody?.file_id ?? null,
});

/* why: update page title to reflect current job status. */
const _updateTitleForStatus = (status) => {
  const normalized = _normalizeStatus(status);
  if (_isFailedStatus(normalized)) {
    if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonization Failed';
    return;
  }
  if (_isCompleteStatus(normalized)) {
    if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonization Complete';
    return;
  }
  if (stageThreeTitle) stageThreeTitle.textContent = 'Harmonizing';
};

const _hideJobMeta = () => {
  if (jobIdDisplay) {
    jobIdDisplay.classList.add('hidden');
  }
};

const _formatElapsed = (elapsedSeconds) => {
  if (!Number.isFinite(elapsedSeconds) || elapsedSeconds < 0) {
    return null;
  }
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = Math.floor(elapsedSeconds % 60);
  if (minutes <= 0) {
    return `${seconds}s`;
  }
  return `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
};

const _updateProgressMessage = (job) => {
  if (!harmonizeProgressMessage) {
    return;
  }

  const elapsedSeconds = Number(job?.elapsed_seconds);
  const elapsedLabel = _formatElapsed(elapsedSeconds);
  if (elapsedSeconds >= 600 && elapsedLabel) {
    harmonizeProgressMessage.textContent = `Still running after ${elapsedLabel}. Large datasets can take a while; keep this tab open.`;
    return;
  }
  if (elapsedSeconds >= 120 && elapsedLabel) {
    harmonizeProgressMessage.textContent = `Still working after ${elapsedLabel}. Larger datasets can take several minutes.`;
    return;
  }
  harmonizeProgressMessage.textContent = 'This usually takes 1-2 minutes.';
};

const _clearPollTimer = () => {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
};

const _showJobId = (jobId) => {
  if (jobIdDisplay && jobId) {
    jobIdDisplay.textContent = `Job ID: ${jobId}`;
    jobIdDisplay.classList.remove('hidden');
  }
};

/* why: extract file_id from URL for job and payload validation. */
const _getFileIdFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('file_id');
};

const _getPayloadFileId = (payload) => payload?.request?.file_id ?? payload?.file_id ?? null;

const _payloadMatchesCurrentFile = (payload) => {
  const currentFileId = _getFileIdFromUrl();
  const payloadFileId = _getPayloadFileId(payload);
  return !currentFileId || !payloadFileId || currentFileId === payloadFileId;
};

/* why: update UI based on job status. */
const _renderJob = (job) => {
  if (!job) {
    return;
  }
  _clearPollTimer();
  const jobForSession = _jobWithCurrentFile(job);
  state.job = jobForSession;
  _persistJobMeta(jobForSession);
  _showJobId(jobForSession.job_id);

  /* Default to 'running' when status is missing - job is in progress. */
  const status = jobForSession.status ?? 'running';
  const normalized = _normalizeStatus(status);
  _updateTitleForStatus(status);
  _clearError();

  if (_isFailedStatus(normalized)) {
    _toggleLoadingState(true);
    _toggleAnimation(false);
    _hideMetricsDashboard();
    _showError(jobForSession.detail || 'Harmonization failed. Please retry.');
    reviewButton.disabled = true;
    retryButton.classList.remove('hidden');
  } else if (_isCompleteStatus(normalized)) {
    advanceMaxReachedStage('review');
    setActiveStage('harmonize');
    _toggleLoadingState(false);
    _renderMetricsDashboard(jobForSession);
    reviewButton.disabled = false;
    retryButton.classList.add('hidden');
    updateStepInstruction('harmonize_complete');
  } else {
    _toggleLoadingState(true);
    _toggleAnimation(true);
    _updateProgressMessage(jobForSession);
    _hideMetricsDashboard();
    reviewButton.disabled = true;
    retryButton.classList.add('hidden');
    _scheduleJobPoll(jobForSession.job_id);
  }
};

const _jobStatusEndpoint = (jobId) => {
  const endpoint = new URL(
    `${harmonizeEndpoint.replace(/\/harmonize$/, '')}/jobs/${encodeURIComponent(jobId)}`,
    window.location.origin,
  );
  const fileId = state.requestBody?.file_id ?? state.job?.file_id ?? _getFileIdFromUrl();
  if (fileId) {
    endpoint.searchParams.set('file_id', fileId);
  }
  return `${endpoint.pathname}${endpoint.search}`;
};

const _scheduleJobPoll = (jobId) => {
  if (!jobId) {
    return;
  }
  state.pollTimer = window.setTimeout(() => {
    _pollJob(jobId);
  }, JOB_POLL_INTERVAL_MS);
};

const _pollJob = async (jobId) => {
  try {
    const response = await fetch(_jobStatusEndpoint(jobId));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || 'Unable to check harmonization status.');
    }
    _renderJob(body);
  } catch (error) {
    console.error(error);
    _showError(error.message || 'Unable to check harmonization status.');
    _scheduleJobPoll(jobId);
  }
};

/* why: extract session payload for harmonization request. */
const _extractRequestPayload = () => {
  let payload = readFromSession(STAGE_3_PAYLOAD_KEY);
  if (payload && !_payloadMatchesCurrentFile(payload)) {
    removeFromSession(STAGE_3_PAYLOAD_KEY);
    payload = null;
  }
  let harmonizePayload = payload?.request ?? payload;
  if (!harmonizePayload) {
    const params = new URLSearchParams(window.location.search);
    const fileId = params.get('file_id');
    const targetSchema = params.get('target_schema') || config.targetSchema;
    const externalVersionNumber = params.get('external_version_number');
    const legacyVersionNumber = Number(params.get('version_number'));
    if (!fileId || !targetSchema) {
      return null;
    }
    harmonizePayload = {
      file_id: fileId,
      target_schema: targetSchema,
      target_external_version_number: externalVersionNumber || null,
      target_version_number: Number.isFinite(legacyVersionNumber) && legacyVersionNumber > 0 ? legacyVersionNumber : null,
      manual_overrides: {},
      manifest: null,
    };
  }
  state.payload = payload;
  state.requestBody = harmonizePayload;
  return harmonizePayload;
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

/* why: verify stored job belongs to current file_id to prevent stale state. */
const _hydrateFromStoredJob = () => {
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
      if (isSafeRelativeUrl(stageTwoUrl)) {
        window.location.assign(stageTwoUrl);
      }
    });
  }

  /* why: _renderJob handles visibility states, so no explicit toggle needed here. */
  if (_hydrateFromStoredJob()) {
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
