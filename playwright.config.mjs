import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: true,
  workers: process.env.CI ? 4 : undefined,
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
    env: {
      // E2E tests mock all external calls; dummy key satisfies startup validation
      NETRIAS_API_KEY: process.env.NETRIAS_API_KEY || 'e2e-test-key',
    },
  },
});
