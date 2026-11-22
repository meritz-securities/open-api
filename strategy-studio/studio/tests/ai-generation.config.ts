import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'ai-generation.spec.ts',workers:1,timeout:60000,use:{channel:'chrome'}});
