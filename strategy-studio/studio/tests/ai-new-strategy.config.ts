import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'ai-new-strategy.spec.ts',use:{baseURL:'http://127.0.0.1:4198',channel:'chrome'},webServer:{command:'npm run dev -- --port 4198 --strictPort',url:'http://127.0.0.1:4198',reuseExistingServer:false}});
