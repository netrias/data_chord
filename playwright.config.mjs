import { defineConfig } from '@playwright/test';
import path from 'node:path';

import { e2eEnv, e2eRuntimeDir } from './tests/e2e/runtime-env.mjs';

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8001';
const useWebServer = process.env.PLAYWRIGHT_NO_WEBSERVER !== 'true';
const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_SERVER === 'true';

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: path.join(e2eRuntimeDir, 'test-results'),
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: true,
  workers: process.env.CI ? 4 : undefined,
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: useWebServer ? {
    command: 'uv run python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001',
    env: e2eEnv,
    url: 'http://127.0.0.1:8001',
    reuseExistingServer,
    timeout: 120_000,
  } : undefined,
});
