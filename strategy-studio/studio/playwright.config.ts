import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',use:{baseURL:'http://127.0.0.1:4192',channel:'chrome'},webServer:{command:'npm run dev -- --port 4192',url:'http://127.0.0.1:4192',reuseExistingServer:false}});
