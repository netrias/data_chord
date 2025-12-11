// """Render the confidence bucket visualization with mock data support."""

const DEFAULT_COLORS = {
  high: '#16a34a',
  medium: '#f97316',
  low: '#dc2626',
};

const computeTotal = (buckets) => buckets.reduce((sum, bucket) => sum + (bucket.count || 0), 0) || 1;

const formatPercent = (value, total) => {
  // "why: keep the label formatting predictable while mocks exist."
  if (!total) {
    return '0%';
  }
  const pct = Math.round(((value || 0) / total) * 100);
  return `${pct}%`;
};

export const renderConfidenceBucketsWidget = ({
  container,
  buckets = [],
  isMocked = false,
  noteText,
}) => {
  // "why: communicate where manual review is likely needed while noting mock data."
  if (!container) {
    return null;
  }

  const card = document.createElement('article');
  card.className = 'metric-widget';

  const title = document.createElement('p');
  title.className = 'metric-widget__title';
  title.textContent = 'Confidence buckets';
  card.appendChild(title);

  const total = computeTotal(buckets);

  const chart = document.createElement('div');
  chart.className = 'metric-chart';

  buckets.forEach((bucket) => {
    const count = typeof bucket.count === 'number' ? bucket.count : 0;
    const row = document.createElement('div');
    row.className = 'metric-chart__row';

    const label = document.createElement('span');
    label.className = 'metric-chart__label';
    label.textContent = bucket.label ?? 'Bucket';
    row.appendChild(label);

    const bar = document.createElement('span');
    bar.className = 'metric-chart__bar';

    const fill = document.createElement('span');
    fill.className = 'metric-chart__bar-fill';
    const fillColor = bucket.color || DEFAULT_COLORS[bucket.id] || '#64748b';
    fill.style.backgroundColor = fillColor;
    fill.style.width = `${Math.round(((count || 0) / total) * 100)}%`;
    bar.appendChild(fill);
    row.appendChild(bar);

    const value = document.createElement('span');
    value.className = 'metric-chart__value';
    value.textContent = `${count.toLocaleString()} · ${formatPercent(count, total)}`;
    row.appendChild(value);

    chart.appendChild(row);
  });

  card.appendChild(chart);

  if (isMocked) {
    const note = document.createElement('p');
    note.className = 'metric-note';
    note.textContent =
      noteText || 'Confidence scores are currently mocked; plug in harmonizer output once available.';
    card.appendChild(note);
  } else if (noteText) {
    const secondary = document.createElement('p');
    secondary.className = 'metric-widget__subtitle';
    secondary.textContent = noteText;
    card.appendChild(secondary);
  }

  container.appendChild(card);
  return card;
};

export default renderConfidenceBucketsWidget;
