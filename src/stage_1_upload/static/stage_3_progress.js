const config = window.stageThreeConfig ?? {};
const progressSteps = document.querySelectorAll('.progress-tracker [data-stage]');
const jobIdValue = document.getElementById('jobIdValue');
const jobStatusValue = document.getElementById('jobStatusValue');
const jobDetailValue = document.getElementById('jobDetailValue');
const reviewButton = document.getElementById('reviewButton');

const setActiveStage = (stage) => {
  const order = ['upload', 'mapping', 'harmonize', 'review', 'export'];
  const targetIndex = order.indexOf(stage);
  progressSteps.forEach((step) => {
    const stepStage = step.dataset.stage;
    const stepIndex = order.indexOf(stepStage);
    const isActive = stepStage === stage;
    const isComplete = stepIndex >= 0 && stepIndex < targetIndex;
    step.classList.toggle('active', isActive);
    step.classList.toggle('complete', isComplete);
  });
};

const init = () => {
  setActiveStage('harmonize');
  const params = new URLSearchParams(window.location.search);
  const jobId = params.get('job_id') || 'pending';
  const status = params.get('status') || 'running';
  const detail = params.get('detail') || 'Harmonization in progress.';

  jobIdValue.textContent = jobId;
  jobStatusValue.textContent = status;
  jobDetailValue.textContent = detail;

  reviewButton.addEventListener('click', () => {
    const url = config.nextStageUrl || '/stage-4';
    window.location.assign(url);
  });
};

init();
