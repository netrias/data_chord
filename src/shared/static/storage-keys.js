/**
 * Centralized session storage key constants and utilities for cross-stage state handoff.
 * Single source of truth prevents typos causing silent state breaks.
 */

export const STAGE_2_PAYLOAD_KEY = 'stage2Payload';
export const STAGE_3_PAYLOAD_KEY = 'stage3HarmonizePayload';
export const STAGE_3_JOB_KEY = 'stage3HarmonizeJob';
export const CURRENT_FILE_SESSION_KEY = 'currentFileSession';
export const MAX_REACHED_STAGE_KEY = 'maxReachedStage';

/** Regex pattern for valid file IDs - alphanumeric with underscores and hyphens only. */
const FILE_ID_PATTERN = /^[a-zA-Z0-9_-]+$/;

/** Regex pattern for safe filenames - alphanumeric with underscore, hyphen, and dot. */
const SAFE_FILENAME_PATTERN = /^[a-zA-Z0-9_.-]+$/;

/**
 * Validate file_id to prevent path traversal attacks.
 * Rejects IDs containing path separators, dots, or other unsafe characters.
 * @param {string|null|undefined} id - The file ID to validate
 * @returns {boolean} True if ID is safe to use in URL paths
 */
export const isValidFileId = (id) => {
  if (!id || typeof id !== 'string') return false;
  return FILE_ID_PATTERN.test(id);
};

/**
 * Validate filename is safe - no path traversal or unsafe characters.
 * @param {string|null|undefined} filename - The filename to validate
 * @returns {boolean} True if filename is safe
 */
export const isSafeFilename = (filename) => {
  if (!filename || typeof filename !== 'string') return false;
  if (filename.includes('/') || filename.includes('\\')) return false;
  if (filename.includes('..')) return false;
  return SAFE_FILENAME_PATTERN.test(filename);
};

/**
 * Safely parse JSON from session storage.
 * @param {string} raw - Raw JSON string
 * @returns {any|null} Parsed value or null on error
 */
const _safeJsonParse = (raw) => {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

/**
 * Read value from session storage with error handling.
 * @param {string} key - Storage key
 * @returns {any|null} Parsed value or null on error
 */
export const readFromSession = (key) => {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? _safeJsonParse(raw) : null;
  } catch (error) {
    console.warn(`Unable to read sessionStorage key "${key}"`, error);
    return null;
  }
};

/**
 * Write value to session storage with error handling.
 * @param {string} key - Storage key
 * @param {any} value - Value to store (will be JSON stringified)
 * @returns {boolean} True if write succeeded
 */
export const writeToSession = (key, value) => {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (error) {
    console.warn(`Unable to write sessionStorage key "${key}"`, error);
    return false;
  }
};

/**
 * Remove value from session storage with error handling.
 * @param {string} key - Storage key
 */
export const removeFromSession = (key) => {
  try {
    sessionStorage.removeItem(key);
  } catch (error) {
    console.warn(`Unable to remove sessionStorage key "${key}"`, error);
  }
};
