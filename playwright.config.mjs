import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:8001',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8001',
    url: 'http://127.0.0.1:8001',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
