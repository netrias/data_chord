/**
 * The uploaded-file record that Stage 1 keeps in session storage.
 * Every stage reads it to name the file; only Stage 1 writes it.
 */
import { CURRENT_FILE_SESSION_KEY, readFromSession, removeFromSession, writeToSession } from './storage-keys.js';

// Once Upload changes the record, the current page stays usable even if storage refuses the write.
let currentFileSession;
export const readCurrentFileSession = () => currentFileSession === undefined
  ? readFromSession(CURRENT_FILE_SESSION_KEY)
  : currentFileSession;

export const readFileSession = (fileId) => {
  const session = readCurrentFileSession();
  return fileId && session?.file_id === fileId ? session : null;
};

export const saveFileSession = (uploadResponse, fileName) => {
  currentFileSession = {
    file_id: uploadResponse.file_id,
    original_name: fileName,
    uploaded_at: uploadResponse.uploaded_at ?? new Date().toISOString(),
    human_size: uploadResponse.human_size,
    tabular_format: uploadResponse.tabular_format ?? null,
    sheet_names: uploadResponse.sheet_names ?? [],
    sheet_previews: uploadResponse.sheet_previews ?? {},
    selected_sheet: uploadResponse.selected_sheet ?? null,
  };
  writeToSession(CURRENT_FILE_SESSION_KEY, currentFileSession);
  return currentFileSession;
};

/** Keep the page current without writing changes into another upload's stored record. */
export const updateFileSession = (fileId, fields) => {
  const session = readFileSession(fileId);
  if (!session) return;
  currentFileSession = { ...session, ...fields };
  if (readFromSession(CURRENT_FILE_SESSION_KEY)?.file_id === fileId) {
    writeToSession(CURRENT_FILE_SESSION_KEY, currentFileSession);
  }
};

export const clearFileSession = () => {
  currentFileSession = null;
  removeFromSession(CURRENT_FILE_SESSION_KEY);
};

/**
 * Rows and columns come from Analyze, which reads one sheet.
 * Returns null when the counts are unknown for the selected sheet.
 */
export const selectedSheetShape = (session, sheetName = session?.selected_sheet) => {
  const shape = session?.dataset_shape;
  if (!shape || !Number.isInteger(shape.rows) || !Number.isInteger(shape.columns)) return null;
  if ((shape.sheet_name ?? null) !== (sheetName ?? null)) return null;
  return { rows: shape.rows, columns: shape.columns };
};

export const formatCount = (count) => count.toLocaleString('en-US');
