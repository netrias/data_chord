/**
 * Initializes step instruction UI for the progress tracker.
 * Shows short instruction text for active step, with hover tooltip for longer description.
 */
import { STEP_INSTRUCTIONS, STAGE_ORDER } from './step-instructions.js';

/**
 * Update progress tracker UI to reflect the active stage.
 * Marks earlier stages as complete, current stage as active.
 * @param {string} stage - The currently active stage identifier
 */
export function setActiveStage(stage) {
  const targetIndex = STAGE_ORDER.indexOf(stage);
  if (targetIndex === -1) {
    console.warn(`[step-instruction-ui] Unknown stage: ${stage}. Valid stages: ${STAGE_ORDER.join(', ')}`);
    return;
  }
  const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = STAGE_ORDER.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
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

/**
 * Attach click handlers for progress tracker step navigation.
 * Enables clicking on steps to navigate to their URLs.
 */
export function initNavigationEvents() {
  document.querySelectorAll('.progress-tracker .step[data-url]').forEach((step) => {
    step.addEventListener('click', () => {
      const url = step.dataset.url;
      if (isSafeRelativeUrl(url)) {
        window.location.href = url;
      }
    });
  });

  document.querySelectorAll('[data-nav-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.navTarget;
      if (isSafeRelativeUrl(target)) {
        window.location.assign(target);
      }
    });
  });
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

/** Populate the main instruction banner below the progress tracker. */
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

/** Add data-tooltip attributes to each step for hover tooltips (using long description). */
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
