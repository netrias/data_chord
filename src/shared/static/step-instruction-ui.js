/**
 * Initializes step instruction UI for the progress tracker.
 * Shows short instruction text for active step, with hover tooltip for longer description.
 */
import { STEP_INSTRUCTIONS } from './step-instructions.js';

/**
 * Populates the step instruction element with content for the given stage.
 * Also adds tooltips to all step elements for hover context.
 * @param {string} activeStage - The currently active stage identifier (upload, mapping, harmonize, review, export)
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
  if (!instructionContainer) return;

  const instructions = STEP_INSTRUCTIONS[instructionKey];
  if (!instructions) return;

  const textEl = instructionContainer.querySelector('.step-instruction-text');
  const tooltipEl = instructionContainer.querySelector('.step-instruction-tooltip');

  if (textEl) {
    textEl.textContent = instructions.short;
    textEl.setAttribute('tabindex', '0');
    textEl.setAttribute('role', 'note');
    textEl.setAttribute('aria-describedby', 'stepInstructionTooltip');
  }

  if (tooltipEl) {
    tooltipEl.textContent = instructions.long;
    tooltipEl.id = 'stepInstructionTooltip';
    tooltipEl.setAttribute('role', 'tooltip');
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
