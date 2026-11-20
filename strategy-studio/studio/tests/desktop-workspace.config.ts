import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'desktop-workspace.spec.ts',use:{baseURL:'http://127.0.0.1:4205',channel:'chrome'},webServer:{command:'npm run dev -- --port 4205 --strictPort',url:'http://127.0.0.1:4205',reuseExistingServer:false}});
