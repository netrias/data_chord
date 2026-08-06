const _safeCount = (value) => {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.trunc(count) : 0;
};

const _formatCount = (changedValue, totalValue) => {
  const changed = _safeCount(changedValue);
  const total = _safeCount(totalValue);
  const percent = total > 0 ? (changed / total) * 100 : 0;
  const percentLabel = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 1,
  }).format(percent);
  return `${changed.toLocaleString()} / ${total.toLocaleString()} · ${percentLabel}%`;
};

const _statusContent = (column) => {
  const nonConformantValues = _safeCount(column.nonConformantValues);
  switch (column.reviewStatus) {
    case 'clear':
      return { className: 'column-outcome-status--approved', text: 'Clear' };
    case 'needs_attention':
      return {
        className: 'column-outcome-status--attention',
        text: `${nonConformantValues.toLocaleString()} ${nonConformantValues === 1 ? 'value needs' : 'values need'} review`,
      };
    case 'not_checked':
      return { className: 'column-outcome-status--unchecked', text: 'Not checked' };
    default:
      return nonConformantValues > 0
        ? {
            className: 'column-outcome-status--attention',
            text: `${nonConformantValues.toLocaleString()} ${nonConformantValues === 1 ? 'value needs' : 'values need'} review`,
          }
        : { className: 'column-outcome-status--unchecked', text: 'No issues found' };
  }
};

const _appendCell = (row, text, className = '') => {
  const cell = document.createElement('td');
  cell.textContent = text;
  if (className) cell.className = className;
  row.appendChild(cell);
  return cell;
};

const _createHeader = () => {
  const head = document.createElement('thead');
  const row = document.createElement('tr');
  const labels = [
    'Source column',
    'Distinct values changed',
    'Rows affected',
    'Final-value status',
  ];

  for (const label of labels) {
    const header = document.createElement('th');
    header.scope = 'col';
    header.textContent = label;
    row.appendChild(header);
  }
  head.appendChild(row);
  return head;
};

const _createBody = (columns) => {
  const body = document.createElement('tbody');
  for (const column of columns) {
    const row = document.createElement('tr');
    if (column.columnKey) row.dataset.columnKey = column.columnKey;

    _appendCell(row, column.label || 'Unnamed column', 'column-outcome-name');
    _appendCell(
      row,
      _formatCount(column.changedDistinctValues, column.totalDistinctValues),
      'column-outcome-metric',
    );
    _appendCell(
      row,
      _formatCount(column.changedRows, column.totalRows),
      'column-outcome-metric',
    );
    const status = _statusContent(column);
    const statusCell = document.createElement('td');
    const statusLabel = document.createElement('span');
    statusLabel.className = `column-outcome-status ${status.className}`;
    statusLabel.textContent = status.text;
    statusCell.appendChild(statusLabel);
    row.appendChild(statusCell);
    body.appendChild(row);
  }
  return body;
};

/**
 * Render source-ordered column outcomes without re-sorting the supplied data.
 */
export const renderColumnOutcomeTable = ({
  container,
  columns,
}) => {
  if (!container) return null;
  container.replaceChildren();

  if (!Array.isArray(columns) || columns.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'column-outcome-empty';
    empty.textContent = 'No reviewed columns are available for this dataset.';
    container.appendChild(empty);
    return empty;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'column-outcome-table-container';
  wrapper.setAttribute('tabindex', '0');
  wrapper.setAttribute('role', 'region');
  wrapper.setAttribute('aria-label', 'Changes by source column');

  const table = document.createElement('table');
  table.className = 'column-outcome-table';

  const caption = document.createElement('caption');
  caption.className = 'sr-only';
  caption.textContent = 'Exact changes and final review status for each source column';
  table.appendChild(caption);
  table.appendChild(_createHeader());
  table.appendChild(_createBody(columns));
  wrapper.appendChild(table);
  container.appendChild(wrapper);
  return wrapper;
};
