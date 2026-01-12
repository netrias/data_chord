/**
 * Column-based review mode module.
 * Shows one column at a time with all unique entries displayed in a grid.
 * Batches entries within each column based on configurable grid size.
 */

import {
  rowHasChanges,
  cellNeedsReview,
  createEmptyState,
  createValueCard,
  renderProgressPills,
  toExcelRowNumber,
} from './shared_review_utils.js';

/** Default number of entries per batch when not specified. */
const DEFAULT_ENTRIES_PER_BATCH = 25;

/**
 * Determine which column indices have at least one non-empty value across all rows.
 * @param {Array} rows - Array of row objects
 * @returns {Set<number>} Set of populated column indices
 */
const _getPopulatedColumnIndices = (rows) => {
  if (!rows.length) return new Set();

  const firstRow = rows[0];
  if (!firstRow?.cells?.length) return new Set();

  const columnCount = firstRow.cells.length;
  const populated = new Set();

  for (let colIdx = 0; colIdx < columnCount; colIdx++) {
    for (const row of rows) {
      const cell = row.cells[colIdx];
      const hasOriginal = cell?.originalValue && cell.originalValue.trim() !== '';
      const hasHarmonized = cell?.harmonizedValue && cell.harmonizedValue.trim() !== '';
      if (hasOriginal || hasHarmonized) {
        populated.add(colIdx);
        break;
      }
    }
  }

  return populated;
};

/**
 * Process a single row's cell for a given column, updating the entries map.
 * @param {Object} row - Row object
 * @param {number} colIdx - Column index
 * @param {string} columnKey - Column key identifier
 * @param {Map} entriesByOriginal - Map to accumulate entries by original value
 */
const _processRowCellForColumn = (row, colIdx, columnKey, entriesByOriginal) => {
  const cell = row.cells[colIdx];
  const originalValue = (cell?.originalValue ?? '').trim();

  // Skip cells without original value (nothing to review)
  if (!originalValue) return;
  // Skip cells that don't need review
  if (!cellNeedsReview(cell)) return;

  const rowIndex = row.sourceRowNumber ?? row.rowNumber;

  if (entriesByOriginal.has(originalValue)) {
    entriesByOriginal.get(originalValue).rowIndices.push(rowIndex);
    return;
  }

  entriesByOriginal.set(originalValue, {
    originalValue: cell.originalValue,
    harmonizedValue: cell.harmonizedValue,
    confidence: cell.confidence,
    bucket: cell.bucket,
    isChanged: cell.isChanged,
    recommendationType: cell.recommendationType,
    manualOverride: cell.manualOverride,
    isPVConformant: cell.isPVConformant,
    pvSetAvailable: cell.pvSetAvailable,
    topSuggestions: cell.topSuggestions ?? [],
    rowIndices: [rowIndex],
    columnKey,
  });
};

/**
 * Build entries for a single column from all changed rows.
 * @param {Array} changedRows - Rows that have changes
 * @param {number} colIdx - Column index
 * @param {Object} columnCell - Cell object containing column metadata
 * @returns {Object|null} Column object with entries, or null if no entries
 */
const _buildColumnEntries = (changedRows, colIdx, columnCell) => {
  if (!columnCell) return null;

  const columnLabel = columnCell.columnLabel;
  const columnKey = columnCell.columnKey;
  const entriesByOriginal = new Map();

  for (const row of changedRows) {
    _processRowCellForColumn(row, colIdx, columnKey, entriesByOriginal);
  }

  const entries = Array.from(entriesByOriginal.values());
  if (entries.length === 0) return null;

  return {
    columnLabel,
    columnKey,
    columnIndex: colIdx,
    entries,
  };
};

/**
 * Build column-centric data structure that groups cells by unique original value.
 * Returns columns with only entries that have changes (original !== harmonized).
 * @param {Array} rows - Array of row objects
 * @returns {Array} Array of column objects with entries
 */
const _buildCompactedColumns = (rows) => {
  const changedRows = rows.filter(rowHasChanges);
  if (!changedRows.length) return [];

  const firstRow = changedRows[0];
  if (!firstRow?.cells) return [];

  const populatedIndices = _getPopulatedColumnIndices(changedRows);
  const allColumns = firstRow.cells;
  const columns = [];

  for (let colIdx = 0; colIdx < allColumns.length; colIdx++) {
    if (!populatedIndices.has(colIdx)) continue;

    const column = _buildColumnEntries(changedRows, colIdx, allColumns[colIdx]);
    if (column) {
      columns.push(column);
    }
  }

  return columns;
};

/**
 * Get total number of navigable units (columns with batches).
 * Returns 0 when no data exists to signal empty state to UI.
 * @param {Array} rows - Array of row objects
 * @param {number} entriesPerBatch - Number of entries per batch
 * @returns {number}
 */
export const getTotalUnits = (rows, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH) => {
  const safeBatchSize = Math.max(1, entriesPerBatch);
  const columns = _buildCompactedColumns(rows);
  let total = 0;
  for (const col of columns) {
    total += Math.ceil(col.entries.length / safeBatchSize);
  }
  return total;
};

/**
 * Get all columns with their batch counts for progress display.
 * @param {Array} rows - Array of row objects
 * @param {number} entriesPerBatch - Number of entries per batch
 * @returns {Array} Array of summary objects for each unit (empty array if no data)
 */
const getColumnSummaries = (rows, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH) => {
  const safeBatchSize = Math.max(1, entriesPerBatch);
  const columns = _buildCompactedColumns(rows);
  const summaries = [];
  let unitIndex = 0;

  for (const col of columns) {
    const batchCount = Math.ceil(col.entries.length / safeBatchSize);
    for (let batch = 0; batch < batchCount; batch++) {
      summaries.push({
        unitIndex: unitIndex + 1,
        columnLabel: col.columnLabel,
        columnKey: col.columnKey,
        columnIndex: col.columnIndex,
        batchWithinColumn: batch + 1,
        totalBatchesInColumn: batchCount,
        entryCount: col.entries.length,
        startEntry: batch * safeBatchSize,
        endEntry: Math.min((batch + 1) * safeBatchSize, col.entries.length),
      });
      unitIndex++;
    }
  }

  return summaries;
};

/**
 * Get entries for the current unit (column + batch within column).
 * @param {Array} rows - Array of row objects
 * @param {number} currentUnit - Current unit index (1-based)
 * @param {number} entriesPerBatch - Number of entries per batch
 * @returns {Object} Batch metadata with entries array
 */
export const getCurrentEntries = (rows, currentUnit, entriesPerBatch = DEFAULT_ENTRIES_PER_BATCH) => {
  const summaries = getColumnSummaries(rows, entriesPerBatch);
  const totalUnits = Math.max(1, summaries.length);
  const safeUnit = summaries.length > 0
    ? Math.min(Math.max(currentUnit, 1), summaries.length)
    : 1;
  const summary = summaries[safeUnit - 1];

  const emptyResult = {
    entries: [],
    unitIndex: safeUnit,
    totalUnits,
    summaries,
    columnLabel: summary?.columnLabel ?? '',
    batchWithinColumn: summary?.batchWithinColumn ?? 1,
    totalBatchesInColumn: summary?.totalBatchesInColumn ?? 1,
  };

  if (!summary || summary.entryCount === 0) {
    return emptyResult;
  }

  const columns = _buildCompactedColumns(rows);
  const column = columns.find((c) => c.columnKey === summary.columnKey);
  if (!column) {
    return emptyResult;
  }

  const entries = column.entries.slice(summary.startEntry, summary.endEntry).map((entry) => ({
    ...entry,
    columnLabel: column.columnLabel,
  }));

  return {
    entries,
    unitIndex: safeUnit,
    totalUnits,
    summaries,
    columnLabel: summary.columnLabel,
    batchWithinColumn: summary.batchWithinColumn,
    totalBatchesInColumn: summary.totalBatchesInColumn,
  };
};

/**
 * Render entries for the current unit into the container.
 * Uses a grid layout for column mode.
 * @param {HTMLElement} container - Container element
 * @param {Object} batchMeta - Batch metadata with entries
 * @param {Object} pendingOverrides - Map of pending overrides
 * @param {Function} onOverrideChange - Callback for override changes
 * @param {Function} onSave - Callback to save changes (called on blur)
 * @param {number} gridSize - Grid dimension (3, 4, or 5 for 3x3, 4x4, 5x5)
 * @param {Object} [columnPVs] - Map of column_key -> PV list
 */
export const renderEntries = (container, batchMeta, pendingOverrides, onOverrideChange, onSave, gridSize = 5, columnPVs = {}) => {
  container.innerHTML = '';

  if (!batchMeta.entries.length) {
    container.append(createEmptyState());
    return;
  }

  const wrapper = document.createElement('div');
  wrapper.className = 'column-mode-grid';
  wrapper.style.setProperty('--grid-columns', gridSize);

  for (const entry of batchMeta.entries) {
    const rowCount = entry.rowIndices.length;
    const excelRows = entry.rowIndices.map(toExcelRowNumber);
    const rowLabel = rowCount === 1 ? `Row ${excelRows[0]}` : `${rowCount} rows`;
    const tooltipText = rowCount > 1 ? `Rows: ${excelRows.join(', ')}` : null;

    const card = createValueCard({
      entry,
      labelText: rowLabel,
      tooltipText,
      pendingOverrides,
      onOverrideChange,
      onSave,
      columnPVs,
    });
    wrapper.append(card);
  }

  container.append(wrapper);
};

/**
 * Render progress pills for column mode.
 * @param {HTMLElement} container - Container element
 * @param {Object} batchMeta - Batch metadata with summaries
 * @param {number} currentUnit - Current unit index
 * @param {Function} onUnitClick - Callback when unit is clicked
 */
export const renderBatchProgress = (container, batchMeta, currentUnit, onUnitClick) => {
  renderProgressPills({
    container,
    summaries: batchMeta.summaries,
    currentUnit,
    onUnitClick,
    isColumnMode: true,
    getLabelForSummary: (summary) => {
      const batchSuffix = summary.totalBatchesInColumn > 1
        ? ` (${summary.batchWithinColumn}/${summary.totalBatchesInColumn})`
        : '';
      return summary.columnLabel + batchSuffix;
    },
    getAriaLabelForSummary: (summary, status) => {
      const batchSuffix = summary.totalBatchesInColumn > 1
        ? ` (${summary.batchWithinColumn}/${summary.totalBatchesInColumn})`
        : '';
      return `${summary.columnLabel}${batchSuffix}: ${status}`;
    },
  });
};
