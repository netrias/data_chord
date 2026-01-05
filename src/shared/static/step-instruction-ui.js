/**
 * Initializes step instruction UI for the progress tracker.
 * Shows short instruction text for active step, with hover tooltip for longer description.
 */
import { STEP_INSTRUCTIONS, STAGE_ORDER } from './step-instructions.js';
import { CURRENT_FILE_SESSION_KEY, MAX_REACHED_STAGE_KEY, readFromSession, writeToSession, removeFromSession } from './storage-keys.js';

/**
 * Update progress tracker UI to reflect the active stage.
 * Marks earlier stages as complete, current stage as active, future stages as unreachable.
 * @param {string} stage - The currently active stage identifier
 */
export function setActiveStage(stage) {
  const targetIndex = STAGE_ORDER.indexOf(stage);
  if (targetIndex === -1) {
    console.warn(`[step-instruction-ui] Unknown stage: ${stage}. Valid stages: ${STAGE_ORDER.join(', ')}`);
    return;
  }

  const maxReachedIndex = _getMaxReachedStageIndex();
  const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');

  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = STAGE_ORDER.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    const isUnreachable = stepIndex > maxReachedIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
    step.classList.toggle('unreachable', isUnreachable);
  });
}

/** Validate URL is safe for navigation (relative path or same-origin absolute). */
export function isSafeRelativeUrl(url) {
  if (!url || typeof url !== 'string') return false;
  if (url.startsWith('/') && !url.startsWith('//')) return true;
  try {
    const parsed = new URL(url);
    return parsed.origin === window.location.origin;
  } catch {
    return false;
  }
}

/* why: preserve file_id when navigating between stages to maintain session context. */
function _getFileIdForNavigation() {
  const urlParams = new URLSearchParams(window.location.search);
  const fromUrl = urlParams.get('file_id');
  if (fromUrl) return fromUrl;

  const session = readFromSession(CURRENT_FILE_SESSION_KEY);
  return session?.file_id || null;
}

/* why: append file_id to navigation URLs so stages can access the active session. */
function _buildNavUrl(baseUrl) {
  const fileId = _getFileIdForNavigation();
  if (!fileId) return baseUrl;

  const url = new URL(baseUrl, window.location.origin);
  url.searchParams.set('file_id', fileId);
  return url.pathname + url.search;
}

/**
 * Attach click handlers for progress tracker step navigation.
 * Enables clicking on steps to navigate to their URLs.
 * Unreachable stages are blocked from navigation.
 */
export function initNavigationEvents() {
  document.querySelectorAll('.progress-tracker .step[data-url]').forEach((step) => {
    step.addEventListener('click', () => {
      if (step.classList.contains('unreachable')) {
        return;
      }
      const url = step.dataset.url;
      if (isSafeRelativeUrl(url)) {
        window.location.href = _buildNavUrl(url);
      }
    });
  });

  document.querySelectorAll('[data-nav-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.navTarget;
      if (isSafeRelativeUrl(target)) {
        /* Reset progress when navigating to stage 1 (starting new workflow). */
        if (_isStageOneUrl(target)) {
          resetMaxReachedStage();
        }
        window.location.assign(target);
      }
    });
  });
}

/* why: detect stage-1 URL using pathname parsing rather than fragile substring match. */
function _isStageOneUrl(url) {
  if (url === '/' || url === '/stage-1') return true;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.pathname === '/' || parsed.pathname === '/stage-1';
  } catch {
    return false;
  }
}

/**
 * Populates the step instruction element with content for the given stage.
 * Also adds tooltips to all step elements for hover context.
 * @param {string} activeStage - The currently active stage identifier (upload, mapping, harmonize, verify, review)
 */
export function initStepInstruction(activeStage) {
  _populateInstructionBanner(activeStage);
  _populateStepTooltips();
}

/**
 * Updates the instruction banner to show a different instruction key.
 * Also updates the step tooltip if the key maps to a known stage.
 * @param {string} instructionKey - Key from STEP_INSTRUCTIONS (e.g., 'harmonize_complete')
 * @param {string} [targetStage] - Stage whose tooltip should update (defaults to prefix of instructionKey)
 */
export function updateStepInstruction(instructionKey, targetStage) {
  _populateInstructionBanner(instructionKey);

  const stage = targetStage || instructionKey.split('_')[0];
  const stepEl = document.querySelector(`.progress-tracker .step[data-stage="${stage}"]`);
  const instructions = STEP_INSTRUCTIONS[instructionKey];
  if (stepEl && instructions) {
    stepEl.setAttribute('data-tooltip', instructions.long);
  }
}

/* why: update the instruction banner to guide users through the current stage. */
function _populateInstructionBanner(instructionKey) {
  const instructionContainer = document.getElementById('stepInstruction');
  if (!instructionContainer) {
    console.warn('[step-instruction-ui] stepInstruction container not found');
    return;
  }

  const instructions = STEP_INSTRUCTIONS[instructionKey];
  if (!instructions) {
    console.warn(`[step-instruction-ui] No instructions found for key: ${instructionKey}`);
    return;
  }

  /* Toggle loading class based on instruction key suffix */
  const isLoading = instructionKey.endsWith('_loading');
  instructionContainer.classList.toggle('step-instruction--loading', isLoading);

  const textEl = instructionContainer.querySelector('.step-instruction-text');
  const tooltipEl = instructionContainer.querySelector('.step-instruction-tooltip');

  if (textEl) {
    textEl.textContent = instructions.short;
    textEl.setAttribute('tabindex', '0');
    textEl.setAttribute('role', 'note');
    textEl.setAttribute('aria-describedby', 'stepInstructionTooltip');
  } else {
    console.warn('[step-instruction-ui] .step-instruction-text element not found');
  }

  if (tooltipEl) {
    tooltipEl.textContent = instructions.long;
    tooltipEl.id = 'stepInstructionTooltip';
    tooltipEl.setAttribute('role', 'tooltip');
  } else {
    console.warn('[step-instruction-ui] .step-instruction-tooltip element not found');
  }
}

/* why: add tooltips to progress tracker steps for context on hover. */
function _populateStepTooltips() {
  const steps = document.querySelectorAll('.progress-tracker .step[data-stage]');
  steps.forEach((step) => {
    const stage = step.dataset.stage;
    const instructions = STEP_INSTRUCTIONS[stage];
    if (instructions) {
      step.setAttribute('data-tooltip', instructions.long);
    }
  });
}

/* why: track highest stage reached to block skipping forward in progress tracker. */
function _getMaxReachedStageIndex() {
  const stored = readFromSession(MAX_REACHED_STAGE_KEY);
  if (stored) {
    const index = STAGE_ORDER.indexOf(stored);
    if (index >= 0) return index;
  }
  return 0;
}

/**
 * Update the max reached stage if the new stage is further than previously reached.
 * Call this when advancing to a new stage via the forward button.
 * @param {string} stage - The stage being advanced to
 */
export function advanceMaxReachedStage(stage) {
  const newIndex = STAGE_ORDER.indexOf(stage);
  if (newIndex === -1) return;

  const currentMax = _getMaxReachedStageIndex();
  if (newIndex > currentMax) {
    writeToSession(MAX_REACHED_STAGE_KEY, stage);
  }
}

/**
 * Reset the max reached stage to the beginning (upload).
 * Call this when starting a new workflow.
 */
export function resetMaxReachedStage() {
  removeFromSession(MAX_REACHED_STAGE_KEY);
}
