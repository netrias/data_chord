/** Escape a value before interpolating it into an HTML string. */
export const escapeHtml = (value) => {
  if (typeof value !== 'string') return String(value);

  const entities = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  };
  return value.replace(/[&<>"']/g, (character) => entities[character]);
};
