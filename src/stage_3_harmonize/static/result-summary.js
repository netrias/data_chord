/**
 * Pure display rules for the Stage 3 result summary.
 * No DOM access, so the rules can be tested without a browser.
 */

/**
 * @typedef {Object} ResultCounts
 * @property {number} checked - Unique values checked against an approved list (>= 0)
 * @property {number} harmonized - Unique values rewritten to an approved value
 * @property {number} matched - Unique values that already matched the approved list
 * @property {number} unresolved - Unique values that still do not match
 */

/**
 * @typedef {Object} ReferenceStandard
 * @property {string | null | undefined} label - Standard name the job recorded, if any
 * @property {string | null | undefined} version - Standard version the job recorded, if any
 */

/**
 * @typedef {'normal' | 'zero' | 'attention'} ResultGroupState
 */

/**
 * @typedef {Object} ResultGroup
 * @property {'harmonized' | 'matched' | 'unresolved'} key
 * @property {number} count
 * @property {string} label - Text after the count; the list name says the counts are unique values
 * @property {ResultGroupState} state - Only a non-zero unresolved group needs attention
 */

/**
 * @typedef {Object} ResultHeadline
 * @property {string} lead - Text that continues the sentence after the dial value
 * @property {string | null} standardName - Emphasized name that ends the sentence; null when the lead is complete
 */

/**
 * @typedef {Object} ResultSummary
 * @property {string} dialValue - The only text in the dial; a fixed-size dial cannot fit a name of any length
 * @property {ResultHeadline} headline - Read after dialValue as one sentence
 * @property {ResultGroup[]} groups - Three groups in dial order; none when nothing was checked
 */

const _formatUniqueValueCount = (count) => `${count.toLocaleString()} unique ${count === 1 ? 'value' : 'values'}`;

/* why: round down so 100% appears only when nothing is unresolved; multiply first so 29/100 is exactly 29. */
const _percentLabel = (matching, checked) => {
  const exact = (matching * 100) / checked;
  if (exact > 0 && exact < 1) return '<1%';
  return `${Math.floor(exact)}%`;
};

/**
 * One naming rule for every place the page names the standard.
 * @param {ReferenceStandard} standard
 * @returns {string | null} Null when no label was recorded; a version alone does not name a standard
 */
export const formatStandardName = ({ label, version }) => {
  const name = label?.trim();
  if (!name) return null;
  const versionText = version?.trim();
  return versionText ? `${name} ${versionText}` : name;
};

/**
 * @param {ResultGroup['key']} key
 * @param {number} count
 * @param {string} label
 * @param {boolean} needsAttentionWhenPresent
 * @returns {ResultGroup}
 */
const _group = (key, count, label, needsAttentionWhenPresent) => ({
  key,
  count,
  label,
  state: count === 0 ? 'zero' : needsAttentionWhenPresent ? 'attention' : 'normal',
});

/**
 * @param {ResultCounts} counts
 * @param {ReferenceStandard} standard
 * @returns {ResultSummary}
 */
export const summarizeResult = ({ checked, harmonized, matched, unresolved }, standard) => {
  if (checked === 0) {
    /* why: no list name here; with nothing checked, no standard was applied to any value. */
    return {
      dialValue: '0',
      headline: { lead: 'unique values were checked against an approved list', standardName: null },
      groups: [],
    };
  }
  const standardName = formatStandardName(standard);
  const lead = `of ${_formatUniqueValueCount(checked)} now ${checked === 1 ? 'matches' : 'match'}`;
  return {
    dialValue: _percentLabel(harmonized + matched, checked),
    headline: standardName ? { lead, standardName } : { lead: `${lead} the approved list`, standardName: null },
    groups: [
      _group('harmonized', harmonized, 'harmonized', false),
      _group('matched', matched, 'already matched', false),
      _group('unresolved', unresolved, 'not harmonized', true),
    ],
  };
};
