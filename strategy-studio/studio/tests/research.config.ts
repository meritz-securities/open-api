import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'research.spec.ts',use:{baseURL:'http://127.0.0.1:4271',channel:'chrome'},webServer:{command:'npm run dev -- --port 4271 --strictPort',url:'http://127.0.0.1:4271',reuseExistingServer:false}});
