import { defineConfig } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';
export default defineConfig({
  plugins: [viteSingleFile()],
  server: { host: '127.0.0.1' },
  build: { rollupOptions: { input: 'mcp-app.html' }, sourcemap: false },
});
