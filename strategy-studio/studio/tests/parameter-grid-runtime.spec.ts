import {test,expect} from '@playwright/test';
import {spawn,type ChildProcessWithoutNullStreams} from 'node:child_process';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';

// No route interception: actual local HTTP, SQLite and accounting engine.
const runtimeRoot=process.env.MERITZ_RUNTIME_ROOT||resolve('..');
const python=process.env.MERITZ_TEST_PYTHON;
let child:ChildProcessWithoutNullStreams;
let nextLine:()=>Promise<any>;
let errors='';
test.beforeAll(async()=>{
 if(!python)throw Error('Set MERITZ_TEST_PYTHON to the pinned test interpreter.');
 child=spawn(python,[resolve('tests/parameter_grid_runtime_server.py'),resolve('dist/index.html')],{
  env:{PATH:process.env.PATH,PYTHONPATH:runtimeRoot,PYTHONUNBUFFERED:'1',MERITZ_NO_ENV_FILE:'1',MERITZ_READ_ONLY:'1'},
 });
 const queue:string[]=[];const waiters:((line:string)=>void)[]=[];
 createInterface({input:child.stdout}).on('line',line=>{const waiter=waiters.shift();if(waiter)waiter(line);else queue.push(line);});
 child.stderr.on('data',chunk=>{errors+=chunk.toString();});
 nextLine=async()=>JSON.parse(await(queue.length?Promise.resolve(queue.shift()!):new Promise<string>((accept,reject)=>{
  const timer=setTimeout(()=>reject(Error('Fixture response timeout: '+errors)),20000);
  waiters.push(line=>{clearTimeout(timer);accept(line);});
 })));
});
test.afterAll(async()=>{
 if(child){child.stdin.end('stop\n');await new Promise<void>(done=>{
  const timer=setTimeout(()=>{child.kill();done();},5000);
  child.once('exit',()=>{clearTimeout(timer);done();});
 });}
});
test('actual HTTP grid, stored reopen and MCP use the same numerical results',async({page},testInfo)=>{
 const pageErrors:string[]=[];const external:string[]=[];
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('request',request=>{if(!['127.0.0.1','localhost'].includes(new URL(request.url()).hostname))external.push(request.url());});
 const first=await nextLine();await page.goto(first.url);
 await expect(page.getByText('내 PC 저장소 준비 완료',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'실행 열기: '+first.baselineId,exact:true}).click();
 await expect(page.getByRole('heading',{name:'실행 리포트',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();
 await page.getByLabel('검증 방법').selectOption('parameter_grid');
 await page.getByRole('combobox',{name:'비교 항목 1',exact:true}).selectOption('quantity');
 await page.getByRole('textbox',{name:'비교할 값 1',exact:true}).fill('2, 3');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='validate_robustness');
 await page.getByRole('button',{name:'조건 값 비교 실행',exact:true}).click();
 const envelope=await(await response).json();expect(envelope.ok).toBe(true);
 const record=envelope.result;
 expect(record.method).toBe('parameter_grid');
 expect(record.result.baselineReturnPct).toBeCloseTo(1,12);
 expect(record.result.baselineComparable).toBe(true);
 expect(record.result.results.map((item:any)=>item.result.metrics.finalEquity)).toEqual([1020,1030]);
 expect(record.result.results[0].result.metrics.totalReturnPct).toBeCloseTo(2,12);
 expect(record.result.results[1].result.metrics.totalReturnPct).toBeCloseTo(3,12);
 const result=page.getByRole('region',{name:'조건 값 비교 결과',exact:true});
 await expect(result).toBeVisible();await expect(result.locator('tbody tr')).toHaveCount(3);
 await expect(result.locator('tbody tr').nth(0).locator('th')).toHaveText('원본 (기준)');
 await expect(result.locator('tbody tr').nth(0).locator('td').first()).toHaveText('+1.00%');
 await expect(result.locator('tbody tr').nth(1).getByRole('cell').first()).toHaveText('+2.00%');
 await expect(result.locator('tbody tr').nth(2).getByRole('cell').first()).toHaveText('+3.00%');
 // A fresh Runtime and HTTP server reopen the persisted store, not component memory.
 child.stdin.write('reopen\n');const reopened=await nextLine();expect(reopened.originalUnchanged).toBe(true);
 await page.goto('about:blank');await page.goto(reopened.url);
 await page.getByRole('button',{name:'실행 열기: '+first.baselineId,exact:true}).click();
 await page.getByRole('button',{name:'검증 결과',exact:true}).click();
 await page.getByRole('button',{name:'검증 이력 새로고침',exact:true}).click();
 const restoredResponse=page.waitForResponse(r=>r.url().endsWith('/api/platform')&&r.request().postDataJSON()?.action==='get_validation');
 await page.getByRole('button',{name:'저장된 결과 열기',exact:true}).click();
 const restored=await(await restoredResponse).json();expect(restored.result).toEqual(record);
 await expect(page.getByLabel('검증 방법')).toHaveValue('parameter_grid');
 await expect(result).toBeVisible();await expect(result.locator('tbody tr')).toHaveCount(3);
 child.stdin.write('mcp\n');const mcp=await nextLine();expect(mcp.originalUnchanged).toBe(true);
 expect(mcp.record.result.results.map((item:any)=>item.result.metrics)).toEqual(record.result.results.map((item:any)=>item.result.metrics));
 expect(mcp.hashes).toEqual(mcp.originalHashes);
 expect(mcp.hashes).toHaveLength(2);
 await page.screenshot({path:testInfo.outputPath('grid-restored-desktop.png'),fullPage:true});
 await page.setViewportSize({width:320,height:780});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
 await page.screenshot({path:testInfo.outputPath('grid-restored-mobile.png'),fullPage:true});
 expect(pageErrors).toEqual([]);expect(external).toEqual([]);
});
