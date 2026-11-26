import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir:'.',testMatch:'parameter-grid-runtime.spec.ts',workers:1,retries:0,
 timeout:60000,use:{viewport:{width:1440,height:1000},screenshot:'only-on-failure'},
});
