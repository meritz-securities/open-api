import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'ai-new-runtime.spec.ts',timeout:60000,use:{channel:'chrome'}});
