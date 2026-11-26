import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', workers: 1,
  use: { channel: process.platform === 'darwin' ? 'chrome' : undefined, headless: true, baseURL: 'http://127.0.0.1:4175' },
  webServer: { command: 'vite preview --host 127.0.0.1 --port 4175 --strictPort', url: 'http://127.0.0.1:4175/mcp-app.html', reuseExistingServer: false },
});
