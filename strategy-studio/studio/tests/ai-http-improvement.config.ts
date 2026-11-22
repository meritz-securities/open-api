import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'ai-http-improvement.spec.ts',use:{baseURL:'http://127.0.0.1:4201',channel:'chrome'},webServer:{command:'npm run dev -- --port 4201 --strictPort',url:'http://127.0.0.1:4201',reuseExistingServer:false}});
