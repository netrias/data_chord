import fs from 'node:fs';
import path from 'node:path';

const _readFile = (filePath) => fs.readFileSync(filePath, 'utf8');

export const resolvePrivateStorageState = (
  environment,
  {
    fileExists = fs.existsSync,
    readFile = _readFile,
  } = {},
) => {
  const configuredPath = environment.PERF_STORAGE_STATE_PATH?.trim();
  if (!configuredPath) {
    throw new Error(
      'PERF_STORAGE_STATE_PATH is required. Run just perf-staging-login first.',
    );
  }

  const storageStatePath = path.resolve(configuredPath);
  if (!fileExists(storageStatePath)) {
    throw new Error(`Authentication state file does not exist: ${storageStatePath}`);
  }

  let storageState;
  try {
    storageState = JSON.parse(readFile(storageStatePath));
  } catch {
    throw new Error('Authentication state is not valid JSON. Create it again.');
  }
  if (!Array.isArray(storageState.cookies) || !Array.isArray(storageState.origins)) {
    throw new Error('Authentication state must contain cookies and origins arrays.');
  }
  return storageStatePath;
};
