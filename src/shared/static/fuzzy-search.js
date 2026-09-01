/**
 * Small, dependency-free fuzzy matching for browser search controls.
 * Screens keep ownership of fields, sections, ordering, and selection.
 */

const TYPO_MIN_LENGTH = 4;

/** @typedef {{literalFields: string[], wordFields: string[], words: Array<{text: string, fieldIndex: number}>}} PreparedSearchCandidate */

const _foldText = (text) => String(text ?? '')
  .normalize('NFKD')
  .replace(/\p{M}+/gu, '')
  .toLowerCase()
  .replace(/\s+/g, ' ')
  .trim();

const _wordForm = (literalText) => literalText
  .replace(/[^\p{L}\p{N}]+/gu, ' ')
  .replace(/\s+/g, ' ')
  .trim();

const _words = (wordForm) => wordForm ? wordForm.split(' ') : [];

/**
 * Normalize one candidate once before repeated searches.
 * Field order is priority order for otherwise equal matches.
 * @param {Array<string|null|undefined>} fields
 * @returns {PreparedSearchCandidate}
 */
export const prepareSearchCandidate = (fields) => {
  const literalFields = fields.map(_foldText);
  const wordFields = literalFields.map(_wordForm);
  const words = wordFields.flatMap((field, fieldIndex) => (
    _words(field).map((text) => ({ text, fieldIndex }))
  ));
  return { literalFields, wordFields, words };
};

const _oneEditAway = (query, target) => {
  const queryChars = Array.from(query);
  const targetChars = Array.from(target);
  const lengthDifference = queryChars.length - targetChars.length;
  if (Math.abs(lengthDifference) > 1) return false;

  if (lengthDifference === 0) {
    const differences = [];
    for (let index = 0; index < queryChars.length; index++) {
      if (queryChars[index] !== targetChars[index]) differences.push(index);
      if (differences.length > 2) return false;
    }
    if (differences.length === 1) return true;
    if (differences.length !== 2) return false;
    const [first, second] = differences;
    return second === first + 1
      && queryChars[first] === targetChars[second]
      && queryChars[second] === targetChars[first];
  }

  const shorter = lengthDifference < 0 ? queryChars : targetChars;
  const longer = lengthDifference < 0 ? targetChars : queryChars;
  let shortIndex = 0;
  let longIndex = 0;
  let skipped = false;
  while (shortIndex < shorter.length && longIndex < longer.length) {
    if (shorter[shortIndex] === longer[longIndex]) {
      shortIndex++;
      longIndex++;
      continue;
    }
    if (skipped) return false;
    skipped = true;
    longIndex++;
  }
  return true;
};

const _phraseScore = (queryText, fields, baseScore) => {
  let best = null;
  fields.forEach((field, fieldIndex) => {
    if (!field) return;
    let score = null;
    if (field === queryText) score = baseScore;
    else if (field.startsWith(queryText)) score = baseScore + 100;
    else {
      const position = field.indexOf(queryText);
      if (position >= 0) score = baseScore + 200 + position;
    }
    if (score !== null) {
      const prioritizedScore = score + fieldIndex;
      best = best === null ? prioritizedScore : Math.min(best, prioritizedScore);
    }
  });
  return best;
};

const _tokenScore = (queryToken, candidate) => {
  let best = null;
  for (const word of candidate.words) {
    let score = null;
    if (word.text === queryToken) score = 0;
    else if (word.text.startsWith(queryToken)) score = 100 + word.text.length - queryToken.length;
    else {
      const position = word.text.indexOf(queryToken);
      if (position >= 0) score = 200 + position;
      else if (queryToken.length >= TYPO_MIN_LENGTH && _oneEditAway(queryToken, word.text)) score = 300;
    }
    if (score !== null) {
      const prioritizedScore = score + word.fieldIndex;
      best = best === null ? prioritizedScore : Math.min(best, prioritizedScore);
    }
  }
  return best;
};

/**
 * Score one prepared candidate. Lower scores are better; null means no match.
 * @param {string} query
 * @param {PreparedSearchCandidate} candidate
 * @returns {number|null}
 */
export const scoreSearch = (query, candidate) => {
  const queryLiteral = _foldText(query);
  if (!queryLiteral) return 0;
  const queryWordForm = _wordForm(queryLiteral);

  const literalPhraseScore = _phraseScore(
    queryLiteral,
    candidate.literalFields,
    0,
  );
  if (literalPhraseScore !== null) return literalPhraseScore;
  if (!queryWordForm) return null;

  const wordPhraseScore = _phraseScore(
    queryWordForm,
    candidate.wordFields,
    400,
  );
  if (wordPhraseScore !== null) return wordPhraseScore;

  const queryTokens = [...new Set(_words(queryWordForm))];
  let score = 1000;
  for (const token of queryTokens) {
    const tokenScore = _tokenScore(token, candidate);
    if (tokenScore === null) return null;
    score += tokenScore;
  }
  return score;
};
