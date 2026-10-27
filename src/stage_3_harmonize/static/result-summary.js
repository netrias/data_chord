/**
 * Pure display rules for the Stage 3 result summary.
 * No DOM access, so the rules can be tested without a browser.
 */

/**
 * @typedef {Object} ResultCounts
 * @property {number} checked - Unique values checked against an approved list (> 0)
 * @property {number} harmonized - Unique values rewritten to an approved value
 * @property {number} matched - Unique values that already matched the approved list
 * @property {number} unresolved - Unique values that still do not match
 */

/**
 * @typedef {'normal' | 'zero' | 'attention'} ResultGroupState
 */

/**
 * @typedef {Object} ResultGroup
 * @property {'harmonized' | 'matched' | 'unresolved'} key
 * @property {number} count
 * @property {string} label - Text after the count, singular when count is 1
 * @property {ResultGroupState} state - Only a non-zero unresolved group needs attention
 */

/**
 * @typedef {Object} ResultSummary
 * @property {string} percentLabel - Matching share, rounded down; "<1%" for a tiny non-zero share
 * @property {string} caption - Text under the percent
 * @property {string} ariaLabel - Dial description with all three counts
 * @property {ResultGroup[]} groups - Always three groups, in dial order
 */

const _uniqueValues = (count) => `unique ${count === 1 ? 'value' : 'values'}`;

export const formatUniqueValueCount = (count) => `${count.toLocaleString()} ${_uniqueValues(count)}`;

/** Rounds down so that 100% appears only when no value is left unresolved. */
const _percentLabel = (matching, checked) => {
  const exact = (matching / checked) * 100;
  if (exact > 0 && exact < 1) return '<1%';
  return `${Math.floor(exact)}%`;
};

/**
 * @param {ResultGroup['key']} key
 * @param {number} count
 * @param {string} labelText
 * @param {boolean} needsAttentionWhenPresent
 * @returns {ResultGroup}
 */
const _group = (key, count, labelText, needsAttentionWhenPresent) => ({
  key,
  count,
  label: `${_uniqueValues(count)} ${labelText}`,
  state: count === 0 ? 'zero' : needsAttentionWhenPresent ? 'attention' : 'normal',
});

/**
 * @param {ResultCounts} counts
 * @returns {ResultSummary}
 */
export const summarizeResult = ({ checked, harmonized, matched, unresolved }) => {
  const groups = [
    _group('harmonized', harmonized, 'harmonized to an approved value', false),
    _group('matched', matched, 'already matched the approved list', false),
    _group('unresolved', unresolved, 'not harmonized', true),
  ];
  return {
    percentLabel: _percentLabel(harmonized + matched, checked),
    caption: `of ${formatUniqueValueCount(checked)} now match`,
    ariaLabel: `Of ${formatUniqueValueCount(checked)} checked: `
      + groups.map((group) => `${group.count.toLocaleString()} ${group.label}`).join('; ')
      + '.',
    groups,
  };
};
