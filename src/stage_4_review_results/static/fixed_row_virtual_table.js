/**
 * Render a large, fixed-height table through a small moving DOM window.
 *
 * Callers own the row data and markup. This module owns only the visible range,
 * spacer rows, scroll scheduling, and cleanup. The table CSS must prevent body
 * rows from wrapping so one measured row height applies to every row.
 */

const OVERSCAN_ROWS = 10;
const FALLBACK_ROW_HEIGHT = 45;

function _spacerRow(height, columnCount) {
  if (height <= 0) return '';
  return `
    <tr class="virtual-table-spacer" aria-hidden="true">
      <td colspan="${columnCount}" style="height: ${height}px"></td>
    </tr>
  `;
}

function _prepareDataRows(contentElement, startIndex) {
  const dataRows = contentElement.querySelectorAll('tr:not(.virtual-table-spacer)');
  dataRows.forEach((row, visibleIndex) => {
    const logicalIndex = startIndex + visibleIndex;
    row.dataset.virtualRow = String(logicalIndex);
    row.setAttribute('aria-rowindex', String(logicalIndex + 2));
  });
}

/**
 * @param {Object} params
 * @param {HTMLElement} params.scrollElement
 * @param {HTMLTableSectionElement} params.contentElement
 * @param {number} params.rowCount
 * @param {(index: number) => string} params.renderRow
 * @param {number} params.columnCount
 * @returns {{destroy: () => void}}
 */
export function mountFixedRowVirtualTable({
  scrollElement,
  contentElement,
  rowCount,
  renderRow,
  columnCount,
}) {
  const table = contentElement.closest('table');
  const view = contentElement.ownerDocument.defaultView;
  if (!view) {
    throw new Error('Virtual table requires a browser window.');
  }
  table?.setAttribute('aria-rowcount', String(rowCount + 1));

  if (rowCount === 0) {
    contentElement.innerHTML = `
      <tr class="virtual-table-no-data">
        <td colspan="${columnCount}">No rows to display.</td>
      </tr>
    `;
    return { destroy() {} };
  }

  contentElement.innerHTML = renderRow(0);
  _prepareDataRows(contentElement, 0);
  const measuredHeight = contentElement.firstElementChild?.getBoundingClientRect().height ?? 0;
  const rowHeight = measuredHeight > 0 ? measuredHeight : FALLBACK_ROW_HEIGHT;
  let renderedStart = -1;
  let renderedEnd = -1;
  let animationFrame = null;
  let destroyed = false;

  const renderVisibleRows = () => {
    animationFrame = null;
    if (destroyed) return;

    const bodyOffset = contentElement.offsetTop;
    const viewportOffset = Math.max(0, scrollElement.scrollTop - bodyOffset);
    const visibleCount = Math.max(1, Math.ceil(scrollElement.clientHeight / rowHeight));
    const firstVisible = Math.min(rowCount - 1, Math.floor(viewportOffset / rowHeight));
    const start = Math.max(0, firstVisible - OVERSCAN_ROWS);
    const end = Math.min(rowCount, firstVisible + visibleCount + OVERSCAN_ROWS);

    if (start === renderedStart && end === renderedEnd) return;
    renderedStart = start;
    renderedEnd = end;

    const visibleRows = Array.from(
      { length: end - start },
      (_, offset) => renderRow(start + offset),
    );
    contentElement.innerHTML = [
      _spacerRow(start * rowHeight, columnCount),
      ...visibleRows,
      _spacerRow((rowCount - end) * rowHeight, columnCount),
    ].join('');
    _prepareDataRows(contentElement, start);
  };

  const scheduleRender = () => {
    if (destroyed || animationFrame !== null) return;
    animationFrame = view.requestAnimationFrame(renderVisibleRows);
  };

  scrollElement.addEventListener('scroll', scheduleRender, { passive: true });
  view.addEventListener('resize', scheduleRender);
  renderVisibleRows();

  return {
    destroy() {
      if (destroyed) return;
      destroyed = true;
      scrollElement.removeEventListener('scroll', scheduleRender);
      view.removeEventListener('resize', scheduleRender);
      if (animationFrame !== null) {
        view.cancelAnimationFrame(animationFrame);
        animationFrame = null;
      }
    },
  };
}
