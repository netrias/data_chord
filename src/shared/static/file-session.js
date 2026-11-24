/**
 * The uploaded-file record that Stage 1 keeps in session storage.
 * Every stage reads it to name the file; only Stage 1 writes it.
 */
import { CURRENT_FILE_SESSION_KEY, readFromSession, writeToSession } from './storage-keys.js';

export const readFileSession = () => readFromSession(CURRENT_FILE_SESSION_KEY);

/** Merge fields into the stored record. Does nothing when no file is stored. */
export const updateFileSession = (fields) => {
  const session = readFileSession();
  if (!session) return;
  writeToSession(CURRENT_FILE_SESSION_KEY, { ...session, ...fields });
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

export const formatCount = (count, noun) => `${count.toLocaleString('en-US')} ${noun}${count === 1 ? '' : 's'}`;
