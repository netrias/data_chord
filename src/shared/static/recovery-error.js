export const REVIEW_STATE_RECOVERY_DETAIL = 'The saved review state cannot be read. Return to Stage 3 and run harmonization again.';

/** Read a server detail message without trusting the response shape. */
export const readResponseDetail = async (response, fallback) => {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === 'string' && body.detail.trim()
    ? body.detail
    : fallback;
};

export const isReviewStateRecovery = (message) => message === REVIEW_STATE_RECOVERY_DETAIL;

/** Render a safe error message with one same-origin recovery link. */
export const renderErrorLink = (container, message, href, label) => {
  if (!container) return;
  container.replaceChildren();
  const text = document.createElement('span');
  text.textContent = message;
  container.append(text, document.createTextNode(' '));
  const link = document.createElement('a');
  link.href = href;
  link.textContent = label;
  container.append(link);
};

/** Render a safe recovery message with a same-origin Stage 3 link. */
export const renderRecoveryError = (container, message, fileId) => {
  if (!fileId) {
    container.textContent = message;
    return;
  }
  renderErrorLink(
    container,
    message,
    `/stage-3?file_id=${encodeURIComponent(fileId)}`,
    'Return to Stage 3',
  );
};
