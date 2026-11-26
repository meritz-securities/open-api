import { copyFile, mkdir } from 'node:fs/promises';
await mkdir(new URL('../../meritz/ui/', import.meta.url), {recursive:true});
await copyFile(new URL('./dist/mcp-app.html', import.meta.url), new URL('../../meritz/ui/backtest.html', import.meta.url));
