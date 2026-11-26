import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'ai-http-journey.spec.ts',workers:1,timeout:90000,use:{channel:'chrome'}});
