import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'ai-studio-hidden.spec.ts',use:{baseURL:'http://127.0.0.1:4217',channel:'chrome'},webServer:{command:'npm run dev -- --port 4217 --strictPort',url:'http://127.0.0.1:4217',reuseExistingServer:false}});
