import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'python-platform-gate.spec.ts',use:{baseURL:'http://127.0.0.1:4216',channel:'chrome'},webServer:{command:'npm run dev -- --port 4216 --strictPort',url:'http://127.0.0.1:4216',reuseExistingServer:false}});
