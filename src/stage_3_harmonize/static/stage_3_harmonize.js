import { initStepInstruction, updateStepInstruction, setActiveStage, initNavigationEvents, isSafeRelativeUrl, advanceMaxReachedStage } from '/assets/shared/step-instruction-ui.js';
import { STAGE_3_JOB_KEY, readFromSession, writeToSession, removeFromSession } from '/assets/shared/storage-keys.js';
import { renderColumnOutcomeTable } from '/assets/shared/column-outcome-table.js';

const config = window.stageThreeConfig ?? {};
const harmonizeEndpoint = config.harmonizeEndpoint ?? '/stage-3/harmonize';
const nextStageUrl = config.nextStageUrl ?? '/stage-4';
const stageTwoUrl = config.stageTwoUrl ?? '/stage-2';
const JOB_POLL_INTERVAL_MS = 3000;

const loadingState = document.getElementById('loadingState');
const reviewButton = document.getElementById('reviewButton');
const retryButton = document.getElementById('retryButton');
const emptyState = document.getElementById('stageThreeEmptyState');
const errorBanner = document.getElementById('stageThreeError');
const stageThreeTitle = document.getElementById('stageThreeTitle');
const stageThreeHeader = document.getElementById('stageThreeHeader');
const returnToStageTwo = document.getElementById('returnToStageTwo');
const harmonizeAnimation = document.querySelector('#loadingState .harmonize-animation');
const harmonizeProgressMessage = document.getElementById('harmonizeProgressMessage');
const columnOutcomePanel = document.querySelector('[data-column-outcome-panel]');
const columnOutcomeContainer = document.querySelector('[data-column-outcome-table]');
const stageThreeDial = document.getElementById('stageThreeDial');
const stageThreeCheckedCount = document.getElementById('stageThreeCheckedCount');
const stageThreeLegend = document.getElementById('stageThreeLegend');
const stageThreeHeadline = document.getElementById('stageThreeHeadline');
const stageThreeResultMessage = document.getElementById('stageThreeResultMessage');
const stageThreeDatasetContext = document.getElementById('stageThreeDatasetContext');
const stageThreeSourceFile = document.getElementById('stageThreeSourceFile');
const stageThreeRowCount = document.getElementById('stageThreeRowCount');
const stageThreeModelContext = document.getElementById('stageThreeModelContext');
const remainingColumns = document.querySelector('[data-remaining-columns]');
const remainingTitle = document.querySelector('[data-remaining-title]');
const remainingSummary = document.querySelector('[data-remaining-summary]');
const remainingBody = document.querySelector('[data-remaining-body]');

const state = {
  requestBody: null,
  job: null,
  isProcessing: false,
  pollTimer: null,
};

const _hideMetricsDashboard = () => {
  columnOutcomeContainer?.replaceChildren();
  columnOutcomePanel?.classList.add('hidden');
  stageThreeHeader?.classList.remove('hidden');
};

const _safeCount = (value) => {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? count : 0;
};

const _columnOutcome = (column) => ({
  columnKey: column.column_key ?? '',
  sourceColumnIndex: Number.isInteger(column.source_column_index)
    ? column.source_column_index
    : null,
  label: column.label ?? column.column_name ?? 'Unnamed column',
  changedDistinctValues: _safeCount(column.unique_terms_changed),
  successfullyHarmonizedValues: Number.isFinite(Number(column.successfully_harmonized_terms))
    && column.successfully_harmonized_terms !== null
    ? _safeCount(column.successfully_harmonized_terms)
    : null,
  totalDistinctValues: _safeCount(column.unique_terms),
  changedRows: _safeCount(column.changed_rows),
  totalRows: _safeCount(column.total_rows),
  reviewerEditedRows: 0,
  nonConformantValues: _safeCount(column.non_conformant_terms),
  reviewStatus: column.review_status ?? null,
});

const _plural = (count, singular, plural = `${singular}s`) => (
  count === 1 ? singular : plural
);

const _formatCount = (count) => count.toLocaleString();

const _appendLegendItem = (colorClass, count, text) => {
  if (!stageThreeLegend || count === 0) return;
  const item = document.createElement('li');
  const marker = document.createElement('span');
  marker.className = `stage-three-legend__marker ${colorClass}`;
  marker.setAttribute('aria-hidden', 'true');
  const copy = document.createElement('span');
  const strong = document.createElement('strong');
  strong.textContent = _formatCount(count);
  copy.append(strong, ` ${text}`);
  item.append(marker, copy);
  stageThreeLegend.appendChild(item);
};

const _renderDial = ({ checked, harmonized, matched, unresolved }) => {
  if (!stageThreeDial || !stageThreeCheckedCount || !stageThreeLegend) return;
  stageThreeCheckedCount.textContent = _formatCount(checked);
  stageThreeLegend.replaceChildren();

  if (checked === 0) {
    stageThreeDial.classList.add('stage-three-dial--empty');
    stageThreeDial.style.removeProperty('--harmonized-end');
    stageThreeDial.style.removeProperty('--matched-end');
    stageThreeDial.setAttribute('aria-label', 'No unique values were checked against an approved list.');
    return;
  }

  stageThreeDial.classList.remove('stage-three-dial--empty');
  const harmonizedEnd = (harmonized / checked) * 100;
  const matchedEnd = ((harmonized + matched) / checked) * 100;
  stageThreeDial.style.setProperty('--harmonized-end', `${harmonizedEnd}%`);
  stageThreeDial.style.setProperty('--matched-end', `${matchedEnd}%`);
  stageThreeDial.setAttribute(
    'aria-label',
    `Of ${_formatCount(checked)} unique ${_plural(checked, 'value')} checked: `
      + `${_formatCount(harmonized)} ${harmonized === 1 ? 'was' : 'were'} successfully harmonized; `
      + `${_formatCount(matched)} already matched; `
      + `${_formatCount(unresolved)} could not be harmonized.`,
  );

  _appendLegendItem(
    'stage-three-legend__marker--harmonized',
    harmonized,
    `Data Chord successfully harmonized ${harmonized === 1 ? 'value' : 'values'}`,
  );
  _appendLegendItem(
    'stage-three-legend__marker--matched',
    matched,
    `${matched === 1 ? 'value already matched' : 'values already matched'} the approved list`,
  );
  _appendLegendItem(
    'stage-three-legend__marker--unresolved',
    unresolved,
    `${unresolved === 1 ? 'value could' : 'values could'} not be harmonized`,
  );
};

const _setDisplayContext = (summary, columns) => {
  const sourceFileName = summary.source_file_name?.trim();
  const totalRows = columns.reduce((largest, column) => Math.max(largest, column.totalRows), 0);
  if (sourceFileName && stageThreeDatasetContext && stageThreeSourceFile && stageThreeRowCount) {
    stageThreeSourceFile.textContent = sourceFileName;
    stageThreeRowCount.textContent = totalRows > 0 ? ` · ${_formatCount(totalRows)} rows` : '';
    stageThreeDatasetContext.classList.remove('hidden');
  } else {
    stageThreeDatasetContext?.classList.add('hidden');
  }

  const modelLabel = summary.reference_model_label?.trim();
  const modelVersion = summary.reference_model_version?.trim();
  if (modelLabel && stageThreeModelContext) {
    stageThreeModelContext.textContent = `Checked against ${modelLabel}${modelVersion ? ` ${modelVersion}` : ''}`;
    stageThreeModelContext.classList.remove('hidden');
  } else {
    stageThreeModelContext?.classList.add('hidden');
  }
};

const _remainingGroup = (heading, columns, className) => {
  if (!remainingBody || columns.length === 0) return;
  const group = document.createElement('section');
  group.className = 'remaining-columns__group';
  const title = document.createElement('h3');
  title.textContent = heading;
  const list = document.createElement('ul');
  list.className = 'remaining-columns__list';
  for (const column of columns) {
    const item = document.createElement('li');
    item.className = className;
    const label = document.createElement('strong');
    label.textContent = column.label;
    const count = document.createElement('span');
    count.textContent = `${_formatCount(column.totalDistinctValues)} different ${_plural(column.totalDistinctValues, 'value')}`;
    item.append(label, count);
    list.appendChild(item);
  }
  group.append(title, list);
  remainingBody.appendChild(group);
};

const _renderRemainingColumns = (columns) => {
  if (!remainingColumns || !remainingTitle || !remainingSummary || !remainingBody) return;
  remainingColumns.removeAttribute('open');
  remainingBody.replaceChildren();
  if (columns.length === 0) {
    remainingColumns.classList.add('hidden');
    return;
  }

  const matched = columns.filter((column) => column.reviewStatus === 'clear');
  const unchecked = columns.filter((column) => column.reviewStatus === 'not_checked');
  const matchedValues = matched.reduce(
    (total, column) => total + column.totalDistinctValues,
    0,
  );
  remainingTitle.textContent = `${_formatCount(columns.length)} ${_plural(columns.length, 'column')} passed through unchanged`;
  remainingSummary.textContent = 'Nothing in them needed rewriting.';
  _remainingGroup(
    `${_formatCount(matchedValues)} ${_plural(matchedValues, 'value')} already matched the approved list`,
    matched,
    'remaining-columns__item--matched',
  );
  _remainingGroup(
    `${_formatCount(unchecked.length)} ${_plural(unchecked.length, 'column')} ${unchecked.length === 1 ? 'has' : 'have'} no approved list to check against`,
    unchecked,
    'remaining-columns__item--unchecked',
  );
  remainingColumns.classList.remove('hidden');
};

const _renderMetricsDashboard = (job) => {
  if (!columnOutcomePanel || !columnOutcomeContainer) {
    return;
  }
  const breakdowns = job?.manifest_summary?.column_breakdowns;
  if (!Array.isArray(breakdowns) || breakdowns.length === 0) {
    _hideMetricsDashboard();
    return;
  }
  const columns = breakdowns.map(_columnOutcome);
  const checkedColumns = columns.filter((column) => column.reviewStatus !== 'not_checked');
  const exactGroupsAvailable = checkedColumns.every(
    (column) => column.successfullyHarmonizedValues !== null,
  );
  const checked = checkedColumns.reduce((total, column) => total + column.totalDistinctValues, 0);
  const harmonized = checkedColumns.reduce(
    (total, column) => total + (column.successfullyHarmonizedValues ?? 0),
    0,
  );
  const unresolved = checkedColumns.reduce(
    (total, column) => total + column.nonConformantValues,
    0,
  );
  const matched = Math.max(checked - harmonized - unresolved, 0);
  const actionColumns = columns
    .filter((column) => column.changedDistinctValues > 0 || column.nonConformantValues > 0)
    .sort((left, right) => (
      Number(right.nonConformantValues > 0) - Number(left.nonConformantValues > 0)
      || (left.sourceColumnIndex ?? Number.MAX_SAFE_INTEGER)
        - (right.sourceColumnIndex ?? Number.MAX_SAFE_INTEGER)
    ));
  const remaining = columns.filter(
    (column) => column.changedDistinctValues === 0 && column.nonConformantValues === 0,
  );

  stageThreeDial?.parentElement?.classList.toggle('hidden', !exactGroupsAvailable);
  document.querySelector('.stage-three-summary')?.classList.toggle(
    'stage-three-summary--legacy',
    !exactGroupsAvailable,
  );
  if (exactGroupsAvailable) {
    _renderDial({ checked, harmonized, matched, unresolved });
  }
  if (stageThreeHeadline) {
    stageThreeHeadline.textContent = !exactGroupsAvailable || checked === 0
      ? 'Harmonization complete'
      : `Data Chord successfully harmonized ${_formatCount(harmonized)} ${_plural(harmonized, 'value')}!`;
  }
  if (stageThreeResultMessage) {
    stageThreeResultMessage.textContent = !exactGroupsAvailable
      ? 'Detailed value groups are not available for this earlier result. Continue to Verify to review the changes.'
      : checked === 0
      ? 'No unique values were checked against an approved list.'
      : unresolved === 0
        ? 'Every checked value now matches the approved list.'
        : `${_formatCount(unresolved)} ${_plural(unresolved, 'value')} could not be harmonized. Continue to Verify to review ${unresolved === 1 ? 'it' : 'them'}.`;
  }
  _setDisplayContext(job.manifest_summary, columns);
  renderColumnOutcomeTable({
    container: columnOutcomeContainer,
    columns: actionColumns,
  });
  _renderRemainingColumns(remaining);
  stageThreeHeader?.classList.add('hidden');
  columnOutcomePanel.classList.remove('hidden');
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
  const fileId = state.requestBody?.file_id ?? state.job?.file_id ?? _getFileIdFromUrl();
  if (!fileId) {
    return;
  }
  state.job = null;
  _persistJobMeta(null);
  reviewButton.disabled = true;
  retryButton.classList.add('hidden');
  _clearError();
  _startHarmonize({ file_id: fileId });
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

/* why: extract file_id from URL for job and payload validation. */
const _getFileIdFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('file_id');
};

const _getJobIdFromUrl = () => {
  const params = new URLSearchParams(window.location.search);
  return params.get('job_id');
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

const _startHarmonize = async (payloadOverride = null) => {
  if (state.isProcessing) {
    return;
  }
  const fileId = payloadOverride?.file_id ?? state.requestBody?.file_id ?? _getFileIdFromUrl();
  if (!fileId) {
    _toggleLoadingState(false);
    _toggleEmptyState(true);
    _hideMetricsDashboard();
    return;
  }
  const payload = { file_id: fileId };

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
  const requestedJobId = _getJobIdFromUrl();
  const storedFileId = job.file_id;

  if (
    (currentFileId && storedFileId && currentFileId !== storedFileId)
    || (requestedJobId && job.job_id !== requestedJobId)
  ) {
    removeFromSession(STAGE_3_JOB_KEY);
    return false;
  }

  state.requestBody = { file_id: currentFileId ?? storedFileId };
  _renderJob(_jobWithCurrentFile(job));
  return true;
};

const _resumeJobFromUrl = () => {
  const fileId = _getFileIdFromUrl();
  const jobId = _getJobIdFromUrl();
  if (!fileId || !jobId) return false;

  state.requestBody = { file_id: fileId };
  _toggleEmptyState(false);
  _toggleLoadingState(true);
  void _pollJob(jobId);
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
        const url = new URL(stageTwoUrl, window.location.origin);
        const fileId = _getFileIdFromUrl();
        if (fileId) url.searchParams.set('file_id', fileId);
        window.location.assign(`${url.pathname}${url.search}`);
      }
    });
  }

  /* why: _renderJob handles visibility states, so no explicit toggle needed here. */
  if (_hydrateFromStoredJob()) {
    return;
  }

  if (_resumeJobFromUrl()) {
    return;
  }

  _toggleLoadingState(false);
  _toggleEmptyState(true);
};

_init();
