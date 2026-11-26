import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir:'.',testMatch:'templates-all-runtime.spec.ts',workers:1,retries:0,
 timeout:600000,use:{viewport:{width:1440,height:1000},screenshot:'only-on-failure'},
});
