import {defineConfig} from '@playwright/test';
// Production HTML served by an ephemeral authenticated Runtime; no shared Vite port.
export default defineConfig({testDir:'.',testMatch:'ai-requests.spec.ts',workers:1,use:{channel:'chrome'}});
