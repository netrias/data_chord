/**
 * The uploaded-file record that Stage 1 keeps in session storage.
 * Every stage reads it to name the file; only Stage 1 writes it.
 */
import { CURRENT_FILE_SESSION_KEY, readFromSession, removeFromSession, writeToSession } from './storage-keys.js';

export const readFileSession = () => readFromSession(CURRENT_FILE_SESSION_KEY);

/** Start a new record from the upload response; the browser clock dates it only when the server did not. */
export const saveFileSession = (uploadResponse, fileName) => {
  writeToSession(CURRENT_FILE_SESSION_KEY, {
    file_id: uploadResponse.file_id,
    original_name: fileName,
    uploaded_at: uploadResponse.uploaded_at ?? new Date().toISOString(),
    human_size: uploadResponse.human_size,
    tabular_format: uploadResponse.tabular_format ?? null,
    sheet_names: uploadResponse.sheet_names ?? [],
    sheet_previews: uploadResponse.sheet_previews ?? {},
    selected_sheet: uploadResponse.selected_sheet ?? null,
  });
};

/** Merge fields into the stored record. Does nothing when no file is stored. */
export const updateFileSession = (fields) => {
  const session = readFileSession();
  if (!session) return;
  writeToSession(CURRENT_FILE_SESSION_KEY, { ...session, ...fields });
};

export const clearFileSession = () => removeFromSession(CURRENT_FILE_SESSION_KEY);

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

/** One digit-grouping rule for every place that shows row or column counts. */
export const formatCount = (count) => count.toLocaleString('en-US');
