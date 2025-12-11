// """Render the total items processed visualization."""

const formatNumber = (value) => {
  // "why: keep formatting logic isolated for predictable card output."
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '—';
  }
  return value.toLocaleString();
};

const _createMetricCard = (title, pillLabel) => {
  // "why: construct a consistent container for metric widgets."
  const article = document.createElement('article');
  article.className = 'metric-widget';

  const header = document.createElement('div');
  header.className = 'metric-widget__title';
  header.textContent = title;

  const pill = document.createElement('span');
  pill.className = 'metric-widget__pill';
  pill.textContent = pillLabel;
  header.appendChild(pill);

  article.appendChild(header);
  return article;
};

export const renderTotalItemsWidget = ({
  container,
  totalItems = 0,
  pillLabel = 'Harmonized rows',
  noteText,
}) => {
  // "why: surface harmonization scope for a quick correctness check."
  if (!container) {
    return null;
  }

  const card = _createMetricCard('Total items processed', pillLabel);
  const value = document.createElement('p');
  value.className = 'metric-widget__value';
  value.textContent = formatNumber(totalItems);
  card.appendChild(value);

  const noteElement = document.createElement('p');
  noteElement.className = 'metric-widget__subtitle';
  noteElement.textContent =
    noteText || 'Counts reflect the submitted file after mapping.';
  card.appendChild(noteElement);

  container.appendChild(card);
  return card;
};

export default renderTotalItemsWidget;
