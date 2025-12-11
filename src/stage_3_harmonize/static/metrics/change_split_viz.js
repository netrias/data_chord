// """Render the changed versus unchanged visualization."""

const clampPercent = (value) => {
  // "why: guard the inline bar from crashing on malformed inputs."
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, value));
};

const formatNumber = (value) => {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }
  return value.toLocaleString();
};

export const renderChangeSplitWidget = ({
  container,
  changed = 0,
  unchanged = 0,
  noteText,
}) => {
  // "why: highlight how much of the dataset harmonization actually touched."
  if (!container) {
    return null;
  }

  const total = changed + unchanged || 1;
  const changedPct = clampPercent((changed / total) * 100);

  const card = document.createElement('article');
  card.className = 'metric-widget';

  const title = document.createElement('p');
  title.className = 'metric-widget__title';
  title.textContent = 'Items changed vs unchanged';
  card.appendChild(title);

  const split = document.createElement('div');
  split.className = 'metric-split';
  split.innerHTML = `<span>Changed · ${formatNumber(changed)}</span><span>Unchanged · ${formatNumber(
    unchanged,
  )}</span>`;
  card.appendChild(split);

  const progress = document.createElement('div');
  progress.className = 'metric-progress';

  const changedFill = document.createElement('div');
  changedFill.className = 'metric-progress__fill';
  changedFill.style.background = 'linear-gradient(90deg, #7c3aed, #6366f1)';
  changedFill.style.width = `${changedPct}%`;
  progress.appendChild(changedFill);

  card.appendChild(progress);

  const footer = document.createElement('p');
  footer.className = 'metric-widget__subtitle';
  footer.textContent =
    noteText || 'Changed is inferred from the harmonization diff payload once available.';
  card.appendChild(footer);

  container.appendChild(card);
  return card;
};

export default renderChangeSplitWidget;
