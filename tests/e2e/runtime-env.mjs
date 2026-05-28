import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const e2eRuntimeDir = process.env.DATA_CHORD_E2E_RUNTIME_DIR
  ?? path.join(os.tmpdir(), 'data-chord-e2e-runtime');

export const e2eEnv = {
  ...process.env,
  // Keep browser-driven tests out of the repo workspace so failed runs do not
  // leave uploads that affect later tests or local development.
  DATA_CHORD_UPLOAD_DIR: path.join(e2eRuntimeDir, 'uploads'),
  DATA_CHORD_WORKFLOW_STORAGE_DIR: path.join(e2eRuntimeDir, 'workflow_storage'),
};

fs.mkdirSync(e2eRuntimeDir, { recursive: true });
process.env.DATA_CHORD_UPLOAD_DIR = e2eEnv.DATA_CHORD_UPLOAD_DIR;
process.env.DATA_CHORD_WORKFLOW_STORAGE_DIR = e2eEnv.DATA_CHORD_WORKFLOW_STORAGE_DIR;
