// Match Python str.strip() whitespace, including NEXT LINE and separators.
// JavaScript trim() differs: it removes BOM but does not remove these controls.
const MISSING_TEXT = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u;

/** @param {string|null|undefined} value */
export const isMissingValue = (value) => value == null || MISSING_TEXT.test(value);
