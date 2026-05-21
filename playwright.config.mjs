import { defineConfig } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8001';
const useWebServer = process.env.PLAYWRIGHT_NO_WEBSERVER !== 'true';

export default defineConfig({
  testDir: './tests/e2e',
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
    url: 'http://127.0.0.1:8001',
    reuseExistingServer: true,
    timeout: 120_000,
  } : undefined,
});
