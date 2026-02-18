/**
 * Determine the visual state of a mapping row based on AI recommendation and user selection.
 *
 * States:
 * - 'recommended' (✓): Using AI recommendation (no override, or override matches AI)
 * - 'override' (✎): Manual override differs from AI recommendation
 * - 'no-mapping' (○): No mapping will be applied to this column
 */

/**
 * @param {Object} params
 * @param {string|null} params.aiRecommendation - The AI's suggested CDE field, or null/empty if none
 * @param {string|null} params.userSelection - The user's override selection, or null/empty if none
 * @param {string} params.noMappingValue - The "No Mapping" sentinel value
 * @returns {{state: 'recommended'|'override'|'no-mapping', icon: string}}
 */
export const determineRowState = ({ aiRecommendation, userSelection, noMappingValue }) => {
  const normalizedAiRec = (aiRecommendation ?? '').trim().toLowerCase();
  const normalizedUserSel = (userSelection ?? '').trim().toLowerCase();
  const normalizedNoMapping = noMappingValue.toLowerCase();

  const hasAiRecommendation = normalizedAiRec !== '';
  const hasUserSelection = normalizedUserSel !== '';
  const isNoMappingSelection = normalizedUserSel === normalizedNoMapping;

  /* Rule: Explicit "No Mapping" selection always shows as no-mapping, even with AI rec. */
  if (isNoMappingSelection || (!hasAiRecommendation && !hasUserSelection)) {
    return { state: 'no-mapping', icon: '○' };
  }

  /* Rule: Has AI recommendation and no user selection → recommended (using AI) */
  if (hasAiRecommendation && !hasUserSelection) {
    return { state: 'recommended', icon: '✓' };
  }

  /* Rule: User selection matches AI recommendation → recommended */
  if (hasAiRecommendation && normalizedUserSel === normalizedAiRec) {
    return { state: 'recommended', icon: '✓' };
  }

  /* Rule: Any other case with a user selection → override */
  return { state: 'override', icon: '✎' };
};
