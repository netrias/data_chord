/** Compare original file bytes across the browser/server upload boundary. */
export const UPLOAD_INTEGRITY_MESSAGE = 'The uploaded file did not match the selected file. Please try again.';

export class UploadVerificationError extends Error {}

/** @typedef {{sha256: string, size_bytes: number}} UploadFingerprint */

/** @param {File} file @returns {Promise<UploadFingerprint>} */
export const fingerprintFile = async (file) => {
  if (!globalThis.crypto?.subtle) {
    throw new UploadVerificationError('File verification requires a supported browser using HTTPS or localhost.');
  }
  try {
    const bytes = await file.arrayBuffer();
    if (bytes.byteLength !== file.size) throw new Error('File read was incomplete');
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
    return {
      sha256: Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join(''),
      size_bytes: file.size,
    };
  } catch {
    throw new UploadVerificationError('We could not prepare this file for upload. Please select it again.');
  }
};

/** @param {unknown} receipt @param {UploadFingerprint} expected */
export const verificationMatches = (receipt, expected) => (
  receipt !== null && typeof receipt === 'object'
  && 'sha256' in receipt && receipt.sha256 === expected.sha256
  && 'size_bytes' in receipt && receipt.size_bytes === expected.size_bytes
);
