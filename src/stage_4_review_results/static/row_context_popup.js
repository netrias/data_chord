/**
 * Row context popup module.
 * Shows original spreadsheet context when users click row indicators in Column Mode.
 * Uses a small local fixed-row virtual table for large datasets.
 */

import { escapeHtml } from '/assets/shared/html.js';
import { mountFixedRowVirtualTable } from './fixed_row_virtual_table.js';
import { toExcelRowNumber } from './shared_review_utils.js';

/** Max rows per API request (backend limit). */
const MAX_ROWS_PER_REQUEST = 10000;

/**
 * Fetch row context from the backend.
 * @param {string} fileId
 * @param {number[]} rowIndices - 0-based row indices
 * @param {AbortSignal} signal
 * @returns {Promise<{headers: string[], rows: string[][]}>}
 */
async function _fetchRowContext(fileId, rowIndices, signal) {
  const response = await fetch('/stage-4/row-context', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, row_indices: rowIndices }),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch row context: ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch all rows in chunks to handle large datasets.
 * @param {string} fileId
 * @param {number[]} rowIndices
 * @param {Function} onProgress - Called with (loadedCount, totalCount) during loading
 * @param {AbortSignal} signal
 * @returns {Promise<{headers: string[], rows: string[][]}>}
 */
async function _fetchAllRowsChunked(fileId, rowIndices, onProgress, signal) {
  const totalRows = rowIndices.length;

  if (totalRows <= MAX_ROWS_PER_REQUEST) {
    return _fetchRowContext(fileId, rowIndices, signal);
  }

  // Fetch in chunks
  let allRows = [];
  let headers = [];

  for (let i = 0; i < totalRows; i += MAX_ROWS_PER_REQUEST) {
    const chunkIndices = rowIndices.slice(i, i + MAX_ROWS_PER_REQUEST);
    const data = await _fetchRowContext(fileId, chunkIndices, signal);

    if (i === 0) {
      headers = data.headers;
    }
    allRows.push(...data.rows);

    if (onProgress) {
      onProgress(Math.min(i + MAX_ROWS_PER_REQUEST, totalRows), totalRows);
    }
  }

  return { headers, rows: allRows };
}

/**
 * Strip BOM and whitespace for header matching.
 * CSV headers often have invisible BOM characters from Excel exports that would
 * otherwise cause column highlighting to fail silently.
 */
function _normalizeForComparison(str) {
  return str.replace(/^\uFEFF/, '').trim();
}

function _columnIndexFromKey(columnKey) {
  const match = /^col_(\d+)$/.exec(columnKey);
  if (!match) return null;
  return Number.parseInt(match[1], 10);
}

/**
 * Build the table header row HTML.
 */
function _buildTableHeader(headers, columnKey) {
  const normalizedColumnKey = _normalizeForComparison(columnKey);
  const targetIndex = _columnIndexFromKey(columnKey);
  const headerCells = headers.map((h, idx) => {
    const isHighlight = targetIndex === null
      ? _normalizeForComparison(h) === normalizedColumnKey
      : idx === targetIndex;
    const classes = isHighlight ? 'row-context-highlight' : '';
    const dataAttr = isHighlight ? ' data-target-column="true"' : '';
    return `<th class="${classes}"${dataAttr}>${escapeHtml(h)}</th>`;
  });

  return `<tr><th>Row</th>${headerCells.join('')}</tr>`;
}

/**
 * Build one table row on demand for the virtual table.
 * @returns {(index: number) => string}
 */
function _createTableRowRenderer(rows, rowIndices, headers, columnKey) {
  const normalizedColumnKey = _normalizeForComparison(columnKey);
  const targetIndex = _columnIndexFromKey(columnKey);
  const highlightColIdx = targetIndex === null
    ? headers.findIndex((h) => _normalizeForComparison(h) === normalizedColumnKey)
    : targetIndex;

  return (index) => {
    const row = rows[index];
    const excelRowNum = toExcelRowNumber(rowIndices[index] + 1);
    const cells = row.map((value, colIdx) => {
      const highlightClass = colIdx === highlightColIdx ? ' class="row-context-highlight"' : '';
      return `<td${highlightClass}>${escapeHtml(value)}</td>`;
    });
    return `<tr><td>${excelRowNum}</td>${cells.join('')}</tr>`;
  };
}

/**
 * Build toggle HTML for filtered/all rows.
 */
function _buildToggleHTML(filteredCount, totalCount, currentMode) {
  const filteredActive = currentMode === 'filtered' ? ' data-active="true"' : '';
  const allActive = currentMode === 'all' ? ' data-active="true"' : '';

  return `
    <div class="row-context-toggle">
      <button class="row-context-toggle-btn" data-mode="filtered"${filteredActive}>
        Filtered (${filteredCount})
      </button>
      <button class="row-context-toggle-btn" data-mode="all"${allActive}>
        All rows (${totalCount})
      </button>
    </div>
  `;
}

/**
 * Build title HTML.
 */
function _buildTitleHTML(params) {
  const { term, columnKey, displayedRowCount, mode } = params;
  const safeTerm = escapeHtml(term);
  const safeColumnKey = escapeHtml(columnKey);
  const rowText = displayedRowCount === 1 ? 'row' : 'rows';

  const mainTitle = mode === 'all' ? 'All Rows' : `"${safeTerm}"`;
  const subtitle = `<span class="row-context-column-link" data-action="scroll-to-column">${safeColumnKey}</span> · ${displayedRowCount} ${rowText}`;

  return `
    <span class="row-context-title-main">${mainTitle}</span>
    <span class="row-context-title-meta">${subtitle}</span>
  `;
}

/**
 * Build the dialog HTML.
 */
function _buildDialogHTML(params) {
  const {
    term,
    columnKey,
    headers,
    displayedRowCount,
    mode,
    filteredCount,
    totalOriginalRows,
    showToggle,
  } = params;

  const toggleHTML = showToggle ? _buildToggleHTML(filteredCount, totalOriginalRows, mode) : '';
  const titleHTML = _buildTitleHTML({ term, columnKey, displayedRowCount, mode });

  // Single scrollable table with a sticky header.
  return `
    <div class="row-context-dialog-content">
      <div class="row-context-dialog-header">
        <h2 class="row-context-dialog-title">${titleHTML}</h2>
        ${toggleHTML}
        <button class="row-context-close-btn" type="button" aria-label="Close">×</button>
      </div>
      <div id="rowContextScrollArea" class="row-context-table-wrapper" tabindex="0" aria-label="Scrollable row context">
        <table class="row-context-table" aria-rowcount="${displayedRowCount + 1}">
          <thead class="row-context-thead">${_buildTableHeader(headers, columnKey)}</thead>
          <tbody id="rowContextContentArea" class="virtual-table-content">
            <tr class="virtual-table-no-data">
              <td colspan="${headers.length + 1}">Loading...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
}

/**
 * Center the target column and flash it to help users find it quickly.
 * Wide spreadsheets may have the target column off-screen; auto-scrolling
 * prevents users from manually searching dozens of columns.
 */
function _scrollToTargetColumn(dialog) {
  const wrapper = dialog.querySelector('.row-context-table-wrapper');
  const targetHeader = dialog.querySelector('th[data-target-column="true"]');
  if (!wrapper || !targetHeader) {
    return;
  }

  const targetLeft = targetHeader.offsetLeft;
  const targetWidth = targetHeader.offsetWidth;
  const wrapperWidth = wrapper.clientWidth;

  const scrollTarget = targetLeft - (wrapperWidth / 2) + (targetWidth / 2);
  wrapper.scrollTo({ left: Math.max(0, scrollTarget), behavior: 'smooth' });

  const highlightedCells = dialog.querySelectorAll('.row-context-highlight');
  highlightedCells.forEach((cell) => {
    cell.classList.remove('flash');
    void cell.offsetWidth;
    cell.classList.add('flash');
  });

  setTimeout(() => {
    highlightedCells.forEach((cell) => cell.classList.remove('flash'));
  }, 800);
}

/**
 * Attach handler for clicking the column name to scroll to it.
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
 * Attach close handlers to the dialog.
 * Uses a flag to prevent double cleanup when both closeDialog() and 'close' event fire.
 * @param {HTMLDialogElement} dialog
 * @param {Function} [onClose] - Cleanup callback
 */
function _attachCloseHandlers(dialog, onClose) {
  let cleanupCalled = false;

  /**
   * Close dialog first for instant visual feedback, then defer cleanup.
   * Deferring cleanup keeps the close interaction responsive.
   */
  const runCleanup = () => {
    if (cleanupCalled) return;
    cleanupCalled = true;
    dialog.remove();
    if (onClose) {
      setTimeout(onClose, 0);
    }
  };

  const closeDialog = () => {
    dialog.close();
    runCleanup();
  };

  const closeBtn = dialog.querySelector('.row-context-close-btn');
  if (closeBtn) {
    closeBtn.addEventListener('click', closeDialog);
  }

  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) {
      closeDialog();
    }
  });

  // Handle native dialog close (e.g., Escape key)
  dialog.addEventListener('close', runCleanup);
}

/**
 * Attach toggle handler for filtered/all rows.
 * Provides immediate visual feedback before async data loading.
 * Uses loading flag to prevent rapid clicks from queueing multiple fetches.
 */
function _attachToggleHandler(dialog, onModeChange) {
  let isLoading = false;

  const toggleBtns = dialog.querySelectorAll('.row-context-toggle-btn');
  toggleBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const newMode = btn.dataset.mode;
      const currentActive = dialog.querySelector('.row-context-toggle-btn[data-active="true"]');

      // Ignore clicks on already-active button or during loading
      if (currentActive === btn || isLoading) return;

      isLoading = true;

      // Immediate visual feedback: switch active button state
      if (currentActive) {
        currentActive.removeAttribute('data-active');
      }
      btn.setAttribute('data-active', 'true');

      // Show loading state in table area
      const tbody = dialog.querySelector('#rowContextContentArea');
      if (tbody) {
        tbody.innerHTML = '<tr class="virtual-table-no-data"><td>Loading...</td></tr>';
      }

      // Reset loading flag after mode change completes (re-renders the toggle handler)
      onModeChange(newMode);
    });
  });
}

/**
 * Show the row context popup with virtualized rendering.
 * @param {Object} params
 * @param {string} params.term - The original value being reviewed
 * @param {string} params.columnKey - Raw column name from spreadsheet
 * @param {number[]} params.rowIndices - Complete 0-based row indices where term appears
 * @param {string} params.fileId - File ID for fetching context
 * @param {number} [params.totalOriginalRows] - Total rows in original spreadsheet
 */
export async function showRowContextPopup({ term, columnKey, rowIndices, fileId, totalOriginalRows = 0 }) {
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

  let virtualTable = null;
  let activeFetch = null;
  let closed = false;

  const cleanup = () => {
    closed = true;
    activeFetch?.abort();
    activeFetch = null;
    if (virtualTable) {
      virtualTable.destroy();
      virtualTable = null;
    }
  };

  _attachCloseHandlers(dialog, cleanup);

  const showToggle = totalOriginalRows > 0 && totalOriginalRows !== rowIndices.length;

  /**
   * Render content for the given mode.
   */
  async function renderContent(mode) {
    activeFetch?.abort();
    const fetchController = new AbortController();
    activeFetch = fetchController;
    const currentIndices = mode === 'all'
      ? Array.from({ length: totalOriginalRows }, (_, i) => i)
      : rowIndices;
    const displayedRowCount = currentIndices.length;

    // Show loading in table area if dialog already has content
    const existingWrapper = dialog.querySelector('.row-context-table-wrapper');
    if (existingWrapper) {
      existingWrapper.innerHTML = '<div class="row-context-loading">Loading row data...</div>';
    }

    if (virtualTable) {
      virtualTable.destroy();
      virtualTable = null;
    }

    try {
      // Update loading message for large datasets
      const updateLoadingMessage = (loaded, total) => {
        const loadingEl = dialog.querySelector('.row-context-loading');
        if (loadingEl) {
          loadingEl.textContent = `Loading rows ${loaded} of ${total}...`;
        }
      };

      const data = await _fetchAllRowsChunked(
        fileId,
        currentIndices,
        updateLoadingMessage,
        fetchController.signal,
      );
      if (closed || activeFetch !== fetchController) return;

      // Build dialog structure
      dialog.innerHTML = _buildDialogHTML({
        term,
        columnKey,
        headers: data.headers,
        displayedRowCount,
        mode,
        filteredCount: rowIndices.length,
        totalOriginalRows,
        showToggle,
      });

      // Re-attach handlers
      _attachCloseHandlers(dialog, cleanup);
      _attachColumnLinkHandler(dialog);
      _attachToggleHandler(dialog, renderContent);

      const scrollElement = dialog.querySelector('#rowContextScrollArea');
      const contentElement = dialog.querySelector('#rowContextContentArea');
      if (
        !(scrollElement instanceof HTMLElement)
        || !(contentElement instanceof HTMLTableSectionElement)
      ) {
        throw new Error('Row context table did not render.');
      }
      virtualTable = mountFixedRowVirtualTable({
        scrollElement,
        contentElement,
        rowCount: data.rows.length,
        renderRow: _createTableRowRenderer(data.rows, currentIndices, data.headers, columnKey),
        columnCount: data.headers.length + 1,
      });
      activeFetch = null;

      // Auto-scroll to target column
      requestAnimationFrame(() => {
        _scrollToTargetColumn(dialog);
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (closed || activeFetch !== fetchController) return;
      activeFetch = null;
      dialog.innerHTML = `
        <div class="row-context-dialog-content">
          <div class="row-context-dialog-header">
            <h2 class="row-context-dialog-title">Error</h2>
            <button class="row-context-close-btn" type="button" aria-label="Close">×</button>
          </div>
          <div class="row-context-error">Failed to load row context. Please try again.</div>
        </div>
      `;
      _attachCloseHandlers(dialog, cleanup);
    }
  }

  // Initial render in filtered mode
  await renderContent('filtered');
}
