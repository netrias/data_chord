/**
 * Row context popup module.
 * Shows original spreadsheet context when users click row indicators in Column Mode.
 * Uses Clusterize.js for virtualized rendering of large datasets.
 */

import { escapeHtml, toExcelRowNumber } from './shared_review_utils.js';

/** Max rows per API request (backend limit). */
const MAX_ROWS_PER_REQUEST = 10000;

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
 * Fetch full row indices for a term from manifest (when initial response was truncated).
 * @param {string} fileId
 * @param {string} columnKey
 * @param {string} originalValue
 * @returns {Promise<number[]>} 0-based row indices
 */
async function _fetchTermRowIndices(fileId, columnKey, originalValue) {
  const response = await fetch('/stage-4/term-row-indices', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_id: fileId, column_key: columnKey, original_value: originalValue }),
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch term row indices: ${response.status}`);
  }

  const data = await response.json();
  return data.row_indices;
}

/**
 * Fetch all rows in chunks to handle large datasets.
 * @param {string} fileId
 * @param {number[]} rowIndices
 * @param {Function} onProgress - Called with (loadedCount, totalCount) during loading
 * @returns {Promise<{headers: string[], rows: string[][]}>}
 */
async function _fetchAllRowsChunked(fileId, rowIndices, onProgress) {
  const totalRows = rowIndices.length;

  if (totalRows <= MAX_ROWS_PER_REQUEST) {
    return _fetchRowContext(fileId, rowIndices);
  }

  // Fetch in chunks
  let allRows = [];
  let headers = [];

  for (let i = 0; i < totalRows; i += MAX_ROWS_PER_REQUEST) {
    const chunkIndices = rowIndices.slice(i, i + MAX_ROWS_PER_REQUEST);
    const data = await _fetchRowContext(fileId, chunkIndices);

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

/**
 * Build the table header row HTML.
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
 * Build table body rows as an array of HTML strings for Clusterize.
 * @returns {string[]} Array of <tr>...</tr> strings
 */
function _buildTableRowsArray(rows, rowIndices, headers, columnKey) {
  const normalizedColumnKey = _normalizeForComparison(columnKey);
  const highlightColIdx = headers.findIndex((h) => _normalizeForComparison(h) === normalizedColumnKey);

  return rows.map((row, i) => {
    const excelRowNum = toExcelRowNumber(rowIndices[i] + 1);
    const cells = row.map((value, colIdx) => {
      const highlightClass = colIdx === highlightColIdx ? ' class="row-context-highlight"' : '';
      return `<td${highlightClass}>${escapeHtml(value)}</td>`;
    });
    return `<tr><td>${excelRowNum}</td>${cells.join('')}</tr>`;
  });
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
 * Build the dialog HTML with Clusterize-compatible structure.
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

  // Single scrollable table with sticky header
  // Clusterize manages tbody content for virtualization
  return `
    <div class="row-context-dialog-content">
      <div class="row-context-dialog-header">
        <h2 class="row-context-dialog-title">${titleHTML}</h2>
        ${toggleHTML}
        <button class="row-context-close-btn" type="button" aria-label="Close">×</button>
      </div>
      <div id="rowContextScrollArea" class="row-context-table-wrapper clusterize-scroll">
        <table class="row-context-table">
          <thead class="row-context-thead">${_buildTableHeader(headers, columnKey)}</thead>
          <tbody id="rowContextContentArea" class="clusterize-content">
            <tr class="clusterize-no-data">
              <td>Loading...</td>
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
   * Clusterize.destroy() can be slow so deferring keeps the UI responsive.
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
        tbody.innerHTML = '<tr class="clusterize-no-data"><td>Loading...</td></tr>';
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
 * @param {number[]} params.rowIndices - 0-based row indices where term appears (may be truncated)
 * @param {number} [params.rowCount] - True count of rows (indices may be truncated for large arrays)
 * @param {string} params.fileId - File ID for fetching context
 * @param {number} [params.totalOriginalRows] - Total rows in original spreadsheet
 */
export async function showRowContextPopup({ term, columnKey, rowIndices, rowCount, fileId, totalOriginalRows = 0 }) {
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

  // Track Clusterize instance for cleanup
  let clusterizeInstance = null;

  const cleanup = () => {
    if (clusterizeInstance) {
      clusterizeInstance.destroy();
      clusterizeInstance = null;
    }
  };

  _attachCloseHandlers(dialog, cleanup);

  // Fetch full indices if truncated (rowCount > indices provided)
  const actualRowCount = rowCount ?? rowIndices.length;
  let fullRowIndices = rowIndices;
  if (actualRowCount > rowIndices.length) {
    try {
      fullRowIndices = await _fetchTermRowIndices(fileId, columnKey, term);
    } catch (err) {
      console.error('Failed to fetch full row indices, using truncated list:', err);
    }
  }

  const showToggle = totalOriginalRows > 0 && totalOriginalRows !== fullRowIndices.length;

  /**
   * Render content for the given mode.
   */
  async function renderContent(mode) {
    const currentIndices = mode === 'all'
      ? Array.from({ length: totalOriginalRows }, (_, i) => i)
      : fullRowIndices;
    const displayedRowCount = currentIndices.length;

    // Show loading in table area if dialog already has content
    const existingWrapper = dialog.querySelector('.row-context-table-wrapper');
    if (existingWrapper) {
      existingWrapper.innerHTML = '<div class="row-context-loading">Loading row data...</div>';
    }

    // Destroy previous Clusterize instance
    if (clusterizeInstance) {
      clusterizeInstance.destroy();
      clusterizeInstance = null;
    }

    try {
      // Update loading message for large datasets
      const updateLoadingMessage = (loaded, total) => {
        const loadingEl = dialog.querySelector('.row-context-loading');
        if (loadingEl) {
          loadingEl.textContent = `Loading rows ${loaded} of ${total}...`;
        }
      };

      const data = await _fetchAllRowsChunked(fileId, currentIndices, updateLoadingMessage);

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

      // Build row data for Clusterize
      const rowsHTML = _buildTableRowsArray(data.rows, currentIndices, data.headers, columnKey);

      // Initialize Clusterize for virtualized rendering
      // Clusterize is loaded globally via CDN
      if (typeof Clusterize !== 'undefined') {
        // rows_in_block × blocks_in_cluster = 200 rows rendered in DOM at once.
        // Tuned for smooth scrolling on 10k+ row datasets without excessive DOM nodes.
        clusterizeInstance = new Clusterize({
          rows: rowsHTML,
          scrollId: 'rowContextScrollArea',
          contentId: 'rowContextContentArea',
          rows_in_block: 50,
          blocks_in_cluster: 4,
          tag: 'tr',
        });
      } else {
        // Fallback: render all rows directly (no virtualization)
        const tbody = dialog.querySelector('#rowContextContentArea');
        if (tbody) {
          tbody.innerHTML = rowsHTML.join('');
        }
      }

      // Auto-scroll to target column
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
      _attachCloseHandlers(dialog, cleanup);
    }
  }

  // Initial render in filtered mode
  await renderContent('filtered');
}
