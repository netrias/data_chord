/** Load the current conformance result before Stage 5 navigation. */

export const fetchConformanceResult = async (fileId, fetchRequest = fetch) => {
  const response = await fetchRequest(`/stage-4/non-conformant/${encodeURIComponent(fileId)}`);
  if (!response.ok) {
    throw new Error(`Conformance check failed: ${response.status}`);
  }

  const result = await response.json();
  if (
    !result
    || !Number.isInteger(result.count)
    || result.count < 0
    || !Array.isArray(result.items)
    || result.count !== result.items.length
    || result.items.some((item) => (
      typeof item?.column !== 'string'
      || typeof item?.value !== 'string'
      || typeof item?.original !== 'string'
    ))
  ) {
    throw new Error('Conformance check returned an invalid response.');
  }
  return result;
};
