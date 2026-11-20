import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'home-console.spec.ts',use:{baseURL:'http://127.0.0.1:4281',channel:'chrome'},webServer:{command:'npm run dev -- --port 4281 --strictPort',url:'http://127.0.0.1:4281',reuseExistingServer:false}});
