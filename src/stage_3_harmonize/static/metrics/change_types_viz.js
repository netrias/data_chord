// """Render the change-type breakdown visualization."""

const COLORS = ['#7c3aed', '#2563eb', '#0891b2', '#059669', '#d97706', '#be185d'];

const computeMax = (items) => {
  // "why: normalize the micro-bars using the largest bucket."
  return items.reduce((max, item) => (item.count > max ? item.count : max), 0) || 1;
};

const formatLabel = (label) => label ?? 'Unknown';

export const renderChangeTypesWidget = ({ container, changeTypes = [], noteText }) => {
  // "why: surface which operations harmonization performed the most."
  if (!container) {
    return null;
  }

  const card = document.createElement('article');
  card.className = 'metric-widget';

  const title = document.createElement('p');
  title.className = 'metric-widget__title';
  title.textContent = 'Change types breakdown';
  card.appendChild(title);

  const chart = document.createElement('div');
  chart.className = 'metric-chart';

  const maxValue = computeMax(changeTypes);
  changeTypes.forEach((item, index) => {
    const row = document.createElement('div');
    row.className = 'metric-chart__row';

    const label = document.createElement('span');
    label.className = 'metric-chart__label';
    label.textContent = formatLabel(item.label);
    row.appendChild(label);

    const bar = document.createElement('span');
    bar.className = 'metric-chart__bar';
    const fill = document.createElement('span');
    fill.className = 'metric-chart__bar-fill';
    fill.style.backgroundColor = COLORS[index % COLORS.length];
    fill.style.width = `${Math.round(((item.count || 0) / maxValue) * 100)}%`;
    bar.appendChild(fill);
    row.appendChild(bar);

    const value = document.createElement('span');
    value.className = 'metric-chart__value';
    value.textContent = (item.count || 0).toLocaleString();
    row.appendChild(value);

    chart.appendChild(row);
  });

  card.appendChild(chart);

  const hint = document.createElement('p');
  hint.className = 'metric-widget__subtitle';
  hint.textContent = noteText || 'Counts are ready to wire into detailed run telemetry.';
  card.appendChild(hint);

  container.appendChild(card);
  return card;
};

export default renderChangeTypesWidget;
