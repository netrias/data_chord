// """Render per-column breakdown with raw change counts and bar chart confidence."""

const _createTermsDisplay = (changedRows, totalRows) => {
  // "why: show term counts as 'X / Y terms changed (Z%)' with progress bar."
  const container = document.createElement('div');
  container.className = 'column-terms-display';

  const percent = totalRows > 0 ? Math.round((changedRows / totalRows) * 100) : 0;

  const countText = document.createElement('div');
  countText.className = 'column-terms-display__count';
  const strong = document.createElement('strong');
  strong.textContent = changedRows.toLocaleString();
  countText.appendChild(strong);
  countText.appendChild(document.createTextNode(` / ${totalRows.toLocaleString()} values changed (${percent}%)`));
  container.appendChild(countText);

  const bar = document.createElement('div');
  bar.className = 'column-terms-bar';

  const fill = document.createElement('div');
  fill.className = 'column-terms-bar__fill';
  fill.style.width = `${percent}%`;
  bar.appendChild(fill);

  container.appendChild(bar);

  return container;
};

const _createConfidenceDisplay = (confidenceBuckets, uniqueTerms) => {
  // "why: show total confidence breakdown for all terms in the column."
  if (uniqueTerms === 0) {
    return null;
  }

  const container = document.createElement('div');
  container.className = 'column-confidence-display';

  const header = document.createElement('div');
  header.className = 'column-confidence-header';
  header.textContent = `Confidence · ${uniqueTerms} terms`;
  container.appendChild(header);

  const columns = document.createElement('div');
  columns.className = 'column-confidence-columns';

  // "why: show Low → Medium → High order."
  const orderedBuckets = [...confidenceBuckets].reverse();

  orderedBuckets.forEach((bucket) => {
    const count = bucket.termCount || 0;
    const col = document.createElement('div');
    col.className = 'column-confidence-columns__item';

    const labelSpan = document.createElement('span');
    labelSpan.className = 'column-confidence-columns__label';
    labelSpan.textContent = bucket.label;
    col.appendChild(labelSpan);

    const countSpan = document.createElement('span');
    countSpan.className = 'column-confidence-columns__count';
    countSpan.textContent = `${count}`;
    col.appendChild(countSpan);

    columns.appendChild(col);
  });

  container.appendChild(columns);
  return container;
};

const _createColumnCard = (column) => {
  // "why: build a card for each column; minimal display if nothing changed."
  const card = document.createElement('article');
  const hasChanges = column.changedRows > 0;

  card.className = hasChanges
    ? 'card card--pad-md column-metric-card'
    : 'card card--inset card--pad-sm column-metric-card column-metric-card--no-changes';

  const title = document.createElement('h4');
  title.className = 'column-metric-card__title';
  title.textContent = column.label;
  card.appendChild(title);

  if (!hasChanges) {
    const noChanges = document.createElement('div');
    noChanges.className = 'column-metric-card__no-changes';
    noChanges.textContent = '0 items changed';
    card.appendChild(noChanges);
    return card;
  }

  const uniqueTerms = document.createElement('div');
  uniqueTerms.className = 'column-metric-card__unique-terms';
  uniqueTerms.textContent = `${column.uniqueTermsChanged} unique terms changed`;
  card.appendChild(uniqueTerms);

  const termsDisplay = _createTermsDisplay(column.changedRows, column.totalRows);
  card.appendChild(termsDisplay);

  const confidenceDisplay = _createConfidenceDisplay(column.confidenceBuckets, column.uniqueTerms);
  if (confidenceDisplay) {
    card.appendChild(confidenceDisplay);
  }

  return card;
};

export const renderColumnBreakdownWidget = ({ container, columns = [] }) => {
  // "why: display per-column metrics in a clean grid layout."
  if (!container || !columns.length) {
    return null;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'column-breakdown';

  const grid = document.createElement('div');
  grid.className = 'column-breakdown__grid';

  columns.forEach((column) => {
    const card = _createColumnCard(column);
    grid.appendChild(card);
  });

  wrapper.appendChild(grid);
  container.appendChild(wrapper);

  return wrapper;
};

export default renderColumnBreakdownWidget;
