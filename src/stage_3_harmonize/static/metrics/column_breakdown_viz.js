// """Render per-column breakdown with raw change counts and bar chart confidence."""

const CONFIDENCE_TOOLTIP = 'Confidence estimates the likelihood that the change is correct. The higher the confidence, the more likely it is that the transformation is correct. A high confidence score is not a guarantee, and all changed data should be verified for correctness on the next page.';

const _createInfoIcon = (tooltipText) => {
  // "why: create info icon with hover tooltip for additional context."
  const wrapper = document.createElement('span');
  wrapper.className = 'info-icon-wrapper';

  const icon = document.createElement('span');
  icon.className = 'info-icon';
  icon.textContent = 'ⓘ';
  icon.setAttribute('aria-label', 'More information');
  icon.setAttribute('role', 'button');
  icon.setAttribute('tabindex', '0');
  wrapper.appendChild(icon);

  const tooltip = document.createElement('span');
  tooltip.className = 'info-tooltip';
  tooltip.textContent = tooltipText;
  wrapper.appendChild(tooltip);

  return wrapper;
};

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

const _createConfidenceDisplay = (confidenceBucketsChanged, uniqueTermsChanged) => {
  // "why: show confidence breakdown for changed terms only."
  if (uniqueTermsChanged === 0) {
    return null;
  }

  const container = document.createElement('div');
  container.className = 'column-confidence-display';

  const headerRow = document.createElement('div');
  headerRow.className = 'column-confidence-header-row';

  const header = document.createElement('span');
  header.className = 'column-confidence-header';
  header.textContent = 'Confidence';
  headerRow.appendChild(header);

  headerRow.appendChild(_createInfoIcon(CONFIDENCE_TOOLTIP));
  container.appendChild(headerRow);

  const columns = document.createElement('div');
  columns.className = 'column-confidence-columns';

  // "why: show Low → Medium → High order."
  const orderedBuckets = [...confidenceBucketsChanged].reverse();

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

const _createUniqueTermsDisplay = (column) => {
  // "why: show 'X / Y unique terms changed' with info tooltip for unchanged terms."
  const container = document.createElement('div');
  container.className = 'column-metric-card__unique-terms-row';

  const text = document.createElement('span');
  text.className = 'column-metric-card__unique-terms';
  text.textContent = `${column.uniqueTermsChanged} / ${column.uniqueTerms} unique terms changed`;
  container.appendChild(text);

  if (column.uniqueTermsUnchanged > 0) {
    const unchangedCount = column.uniqueTermsUnchanged;
    const tooltipText = `${unchangedCount} ${unchangedCount === 1 ? 'term was' : 'terms were'} unchanged as ${unchangedCount === 1 ? 'it' : 'they'} matched existing values in the target ontology.`;
    container.appendChild(_createInfoIcon(tooltipText));
  }

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

  const uniqueTermsDisplay = _createUniqueTermsDisplay(column);
  card.appendChild(uniqueTermsDisplay);

  const termsDisplay = _createTermsDisplay(column.changedRows, column.totalRows);
  card.appendChild(termsDisplay);

  const confidenceDisplay = _createConfidenceDisplay(column.confidenceBucketsChanged, column.uniqueTermsChanged);
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
