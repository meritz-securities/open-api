import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'parameter-grid.spec.ts',use:{baseURL:'http://127.0.0.1:4261',channel:'chrome'},webServer:{command:'npm run dev -- --port 4261 --strictPort',url:'http://127.0.0.1:4261',reuseExistingServer:false}});
