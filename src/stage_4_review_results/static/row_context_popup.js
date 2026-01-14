/**
 * Row context popup module.
 * Shows original spreadsheet context when users click row indicators in Column Mode.
 */

import { escapeHtml, toExcelRowNumber } from './shared_review_utils.js';

const INITIAL_ROW_LIMIT = 20;

/**
 * Fetch row context from the backend.
 * @param {string} fileId
 * @param {number[]} rowIndices - 0-based row indices
 * @returns {Promise<{headers: string[], rows: string[][]}>}
 */
async function _fetchRowContext(fileId, rowIndices) {
  const response = await fetch('/stage-4/row-context', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, row_indices: rowIndices }),
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch row context: ${response.status}`);
  }

  return response.json();
}

/**
 * Strip BOM (Byte Order Mark) and whitespace from string for comparison.
 * CSV files often have BOM on the first header which breaks exact string matching.
 */
function _normalizeForComparison(str) {
  return str.replace(/^\uFEFF/, '').trim();
}

/**
 * Build the table header row HTML.
 * @param {string[]} headers
 * @param {string} columnKey - Column to highlight
 * @returns {string}
 */
function _buildTableHeader(headers, columnKey) {
  const normalizedColumnKey = _normalizeForComparison(columnKey);
  const headerCells = headers.map((h) => {
    const isHighlight = _normalizeForComparison(h) === normalizedColumnKey;
    const classes = isHighlight ? 'row-context-highlight' : '';
    const dataAttr = isHighlight ? ' data-target-column="true"' : '';
    return `<th class="${classes}"${dataAttr}>${escapeHtml(h)}</th>`;
  });

  return `<tr><th>Row</th>${headerCells.join('')}</tr>`;
}

/**
 * Build table body rows HTML.
 * @param {string[][]} rows - Row data
 * @param {number[]} rowIndices - Original row indices (0-based)
 * @param {string[]} headers
 * @param {string} columnKey - Column to highlight
 * @returns {string}
 */
function _buildTableRows(rows, rowIndices, headers, columnKey) {
  const normalizedColumnKey = _normalizeForComparison(columnKey);
  return rows.map((row, i) => {
    // rowIndices are 0-based (array index), convert to 1-based then to Excel row number
    const excelRowNum = toExcelRowNumber(rowIndices[i] + 1);
    const cells = row.map((value, colIdx) => {
      const isHighlight = _normalizeForComparison(headers[colIdx]) === normalizedColumnKey;
      const highlightClass = isHighlight ? ' class="row-context-highlight"' : '';
      return `<td${highlightClass}>${escapeHtml(value)}</td>`;
    });
    return `<tr><td>${excelRowNum}</td>${cells.join('')}</tr>`;
  }).join('');
}

/**
 * Build the dialog HTML content.
 * Uses raw column name (columnKey) to match spreadsheet headers.
 * @param {Object} params
 * @returns {string}
 */
function _buildDialogHTML(params) {
  const { term, totalRows, headers, rows, rowIndices, columnKey, hasMore } = params;
  const safeTerm = escapeHtml(term);
  const safeColumnKey = escapeHtml(columnKey);

  const loadAllButton = hasMore
    ? `<button class="row-context-load-all" type="button">Load all ${totalRows} rows</button>`
    : '';

  return `
    <div class="row-context-dialog-content">
      <div class="row-context-dialog-header">
        <h2 class="row-context-dialog-title">
          Context for "${safeTerm}" in <span class="row-context-column-link" data-action="scroll-to-column">${safeColumnKey}</span> (${totalRows} row${totalRows === 1 ? '' : 's'})
        </h2>
        <button class="row-context-close-btn" type="button" aria-label="Close">×</button>
      </div>
      <div class="row-context-table-wrapper">
        <table class="row-context-table">
          <thead>${_buildTableHeader(headers, columnKey)}</thead>
          <tbody>${_buildTableRows(rows, rowIndices, headers, columnKey)}</tbody>
        </table>
      </div>
      ${loadAllButton ? `<div class="row-context-dialog-footer">${loadAllButton}</div>` : ''}
    </div>
  `;
}

/**
 * Scroll the table wrapper to bring the target column into view and flash it.
 * Uses manual scroll calculation for reliable behavior in nested dialog containers.
 * @param {HTMLDialogElement} dialog
 */
function _scrollToTargetColumn(dialog) {
  const wrapper = dialog.querySelector('.row-context-table-wrapper');
  const targetHeader = dialog.querySelector('th[data-target-column="true"]');
  if (!wrapper || !targetHeader) {
    return;
  }

  // Calculate scroll position to center the target column in the visible area
  // targetHeader.offsetLeft is relative to the table (its offsetParent)
  const targetLeft = targetHeader.offsetLeft;
  const targetWidth = targetHeader.offsetWidth;
  const wrapperWidth = wrapper.clientWidth;

  // Center the target column in the wrapper
  const scrollTarget = targetLeft - (wrapperWidth / 2) + (targetWidth / 2);
  wrapper.scrollTo({ left: Math.max(0, scrollTarget), behavior: 'smooth' });

  // Add visual flash feedback to all highlighted cells (header and body cells)
  const highlightedCells = dialog.querySelectorAll('.row-context-highlight');
  highlightedCells.forEach((cell) => {
    cell.classList.remove('flash');
    // Force reflow to restart animation
    void cell.offsetWidth;
    cell.classList.add('flash');
  });

  // Remove flash class after animation completes
  setTimeout(() => {
    highlightedCells.forEach((cell) => cell.classList.remove('flash'));
  }, 800);
}

/**
 * Attach handler for clicking the column name to scroll to it.
 * Uses event delegation on dialog for robustness.
 * @param {HTMLDialogElement} dialog
 */
function _attachColumnLinkHandler(dialog) {
  dialog.addEventListener('click', (event) => {
    const link = event.target.closest('[data-action="scroll-to-column"]');
    if (link) {
      event.preventDefault();
      _scrollToTargetColumn(dialog);
    }
  });
}

/**
 * Enable horizontal scrolling with mouse wheel when no vertical scroll is needed.
 * @param {HTMLDialogElement} dialog
 */
function _attachHorizontalWheelHandler(dialog) {
  const wrapper = dialog.querySelector('.row-context-table-wrapper');
  if (!wrapper) return;

  wrapper.addEventListener('wheel', (event) => {
    // Only convert vertical wheel to horizontal if there's no vertical overflow
    const hasVerticalScroll = wrapper.scrollHeight > wrapper.clientHeight;
    if (!hasVerticalScroll && event.deltaY !== 0) {
      event.preventDefault();
      wrapper.scrollLeft += event.deltaY;
    }
  }, { passive: false });
}

/**
 * Attach close handlers to the dialog.
 * @param {HTMLDialogElement} dialog
 */
function _attachCloseHandlers(dialog) {
  // Close button
  const closeBtn = dialog.querySelector('.row-context-close-btn');
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      dialog.close();
      dialog.remove();
    });
  }

  // Click outside (on backdrop)
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      dialog.close();
      dialog.remove();
    }
  });

  // ESC key (native dialog behavior, but ensure cleanup)
  dialog.addEventListener('close', () => {
    dialog.remove();
  });
}

/**
 * Attach handler for "Load all" button.
 * @param {HTMLDialogElement} dialog
 * @param {string} fileId
 * @param {number[]} allRowIndices
 * @param {string[]} headers
 * @param {string} columnKey
 */
function _attachLoadAllHandler(dialog, fileId, allRowIndices, headers, columnKey) {
  const loadAllBtn = dialog.querySelector('.row-context-load-all');
  if (!loadAllBtn) return;

  loadAllBtn.addEventListener('click', async () => {
    loadAllBtn.disabled = true;
    loadAllBtn.textContent = 'Loading...';

    try {
      const data = await _fetchRowContext(fileId, allRowIndices);
      const tbody = dialog.querySelector('.row-context-table tbody');
      if (tbody) {
        tbody.innerHTML = _buildTableRows(data.rows, allRowIndices, headers, columnKey);
      }
      // Remove the footer with load all button
      const footer = dialog.querySelector('.row-context-dialog-footer');
      if (footer) {
        footer.remove();
      }
    } catch (error) {
      loadAllBtn.disabled = false;
      loadAllBtn.textContent = 'Load failed - retry';
    }
  });
}

/**
 * Show the row context popup.
 * @param {Object} params
 * @param {string} params.term - The original value being reviewed
 * @param {string} params.columnKey - Raw column name from spreadsheet (for highlighting and display)
 * @param {number[]} params.rowIndices - 0-based row indices where term appears
 * @param {string} params.fileId - File ID for fetching context
 */
export async function showRowContextPopup({ term, columnKey, rowIndices, fileId }) {
  const dialog = document.createElement('dialog');
  dialog.className = 'row-context-dialog';

  // Show loading state
  dialog.innerHTML = `
    <div class="row-context-dialog-content">
      <div class="row-context-dialog-header">
        <h2 class="row-context-dialog-title">Loading context...</h2>
        <button class="row-context-close-btn" type="button" aria-label="Close">×</button>
      </div>
      <div class="row-context-loading">Loading row data...</div>
    </div>
  `;

  document.body.appendChild(dialog);
  dialog.showModal();
  _attachCloseHandlers(dialog);

  try {
    // Fetch initial rows (first 20)
    const initialIndices = rowIndices.slice(0, INITIAL_ROW_LIMIT);
    const data = await _fetchRowContext(fileId, initialIndices);

    const hasMore = rowIndices.length > INITIAL_ROW_LIMIT;

    dialog.innerHTML = _buildDialogHTML({
      term,
      totalRows: rowIndices.length,
      headers: data.headers,
      rows: data.rows,
      rowIndices: initialIndices,
      columnKey,
      hasMore,
    });

    // Re-attach handlers after innerHTML replacement
    _attachCloseHandlers(dialog);
    _attachColumnLinkHandler(dialog);

    if (hasMore) {
      _attachLoadAllHandler(dialog, fileId, rowIndices, data.headers, columnKey);
    }

    // Enable horizontal wheel scrolling when no vertical scroll is present
    _attachHorizontalWheelHandler(dialog);

    // Auto-scroll to target column after a brief delay (let DOM settle)
    requestAnimationFrame(() => {
      _scrollToTargetColumn(dialog);
    });
  } catch (error) {
    dialog.innerHTML = `
      <div class="row-context-dialog-content">
        <div class="row-context-dialog-header">
          <h2 class="row-context-dialog-title">Error</h2>
          <button class="row-context-close-btn" type="button" aria-label="Close">×</button>
        </div>
        <div class="row-context-error">Failed to load row context. Please try again.</div>
      </div>
    `;
    _attachCloseHandlers(dialog);
  }
}
