/**
 * Best-effort browser event reporting for operator-visible failures.
 *
 * The backend validates the payload, so this helper keeps the client shape small
 * and avoids sending arbitrary console details.
 */

export const CLIENT_EVENT_ENDPOINT = '/client-events';
export const CLIENT_FETCH_FAILED = 'client.fetch.failed';
export const CLIENT_API_ERROR = 'client.api.error';

const MAX_ERROR_MESSAGE_LENGTH = 512;
const FILE_ID_PATTERN = /^[a-f0-9]{8,64}$/;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

const _truncate = (value, maxLength) => {
  if (typeof value !== 'string') return null;
  return value.length > maxLength ? value.slice(0, maxLength) : value;
};

const _pathForEndpoint = (endpoint) => {
  if (!endpoint) return null;
  try {
    return new URL(endpoint, window.location.origin).pathname;
  } catch {
    return null;
  }
};

const _safeFileId = (fileId) => (FILE_ID_PATTERN.test(fileId ?? '') ? fileId : null);

const _safeRequestId = (requestId) => (REQUEST_ID_PATTERN.test(requestId ?? '') ? requestId : null);

const _sendEvent = (payload) => {
  const body = JSON.stringify({
    ...payload,
    online: typeof navigator.onLine === 'boolean' ? navigator.onLine : null,
    timestamp_ms: Date.now(),
  });
  const blob = new Blob([body], { type: 'application/json' });
  try {
    if (navigator.sendBeacon?.(CLIENT_EVENT_ENDPOINT, blob)) {
      return;
    }
  } catch {
    /* fall back below */
  }
  fetch(CLIENT_EVENT_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
    keepalive: true,
  }).catch(() => {});
};

export const reportClientEvent = ({
  eventName,
  stage,
  operation,
  endpoint = null,
  fileId = null,
  error = null,
  statusCode = null,
  serverRequestId = null,
}) => {
  _sendEvent({
    event_name: eventName,
    stage,
    operation,
    path: _pathForEndpoint(endpoint),
    file_id: _safeFileId(fileId),
    error_name: _truncate(error?.name ?? null, 80),
    error_message: _truncate(error?.message ?? null, MAX_ERROR_MESSAGE_LENGTH),
    status_code: statusCode,
    server_request_id: _safeRequestId(serverRequestId),
  });
};

export const reportFetchFailure = ({ stage, operation, endpoint, fileId = null, error }) => {
  reportClientEvent({
    eventName: CLIENT_FETCH_FAILED,
    stage,
    operation,
    endpoint,
    fileId,
    error,
  });
};

export const reportApiError = ({
  stage,
  operation,
  endpoint,
  fileId = null,
  statusCode,
  serverRequestId = null,
}) => {
  reportClientEvent({
    eventName: CLIENT_API_ERROR,
    stage,
    operation,
    endpoint,
    fileId,
    statusCode,
    serverRequestId,
  });
};
