import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'research-cancel-http.spec.ts',workers:1,retries:0,
 timeout:60000,use:{viewport:{width:1280,height:1000},screenshot:'only-on-failure'}});
