import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:['strategy-graph-unit.spec.ts','strategy-graph.spec.ts'],workers:1,timeout:60000,use:{channel:'chrome'}});
