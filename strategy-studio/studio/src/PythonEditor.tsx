import {useEffect, useLayoutEffect, useRef} from 'react';
import * as monaco from 'monaco-editor/editor/editor.api';
import 'monaco-editor/editor/contrib/suggest/browser/suggestController';
import 'monaco-editor/editor/contrib/hover/browser/hoverContribution';
import 'monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching';
import 'monaco-editor/languages/definitions/python/register';

import {diagnosticColumns,type Diagnostic} from './diagnostics';
export type {Diagnostic} from './diagnostics';
// SDK signatures and behavior are sourced from sdk/meritz_strategy/__init__.py.
const methods = [
 ['get_cash', 'get_cash()', '현재 가상 포트폴리오의 cash를 반환합니다.'],
 ['get_position', 'get_position()', 'quantity, averageCost, unrealizedPnl을 반환합니다.'],
 ['get_portfolio', 'get_portfolio()', '가상 포트폴리오의 깊은 복사본을 반환합니다.'],
 ['buy', 'buy(quantity=None, **options)', '매수 의도를 반환합니다. reason, order_type, limit_price, evidence를 지정할 수 있습니다.'],
 ['sell', 'sell(quantity=None, **options)', '매도 의도를 반환합니다. 수량 생략 시 보유 수량을 대상으로 합니다.'],
 ['close_position', 'close_position(**options)', '보유 시 전량 매도 의도, 미보유 시 hold를 반환합니다.'],
 ['get_indicator', "get_indicator(name, period=20, field='close', component=None, **params)", 'sma/ema/rsi 외에 bollinger·macd·stochastic·adx·atr 등 54종 지표를 이름으로 호출합니다(잘못된 이름은 오류 메시지에 전체 목록 안내). period 2..250 · open/high/low/close. 여러 선을 가진 지표는 component 로 선택합니다(예: bollinger, component="upper"). 준비 기간에는 None. 현재까지 완료된 봉만 사용합니다.'],
] as const;
const executionHelp = '의도는 현재 봉 평가 후 다음 봉부터 가상 체결 대상입니다. 마지막 봉의 의도는 체결되지 않습니다.';
export const pythonExample = `from meritz_strategy import Context

def on_bar(ctx):
    context = Context(ctx)
    average = context.get_indicator("sma", period=20)
    if average is not None and context.bar["close"] > average:
        if context.get_position()["quantity"] == 0:
            return context.buy(1, reason="종가가 20봉 평균 위")
    return {"action": "hold"}
`;

// Deliberately conservative lexical SDK discovery, not Python type inference.
// Monaco's Python tokenizer masks comments and all string forms (including triple quotes).
function sdkReceiver(model:monaco.editor.ITextModel,line:number,column:number) {
 const source=model.getValueInRange(new monaco.Range(1,1,line,column));
 const lines=source.split('\n');
 const tokens=monaco.editor.tokenize(source,'python');
 const code=lines.map((text,index)=>{
  const parts=tokens[index]||[];
  return parts.map((token,i)=>{const part=text.slice(token.offset,parts[i+1]?.offset??text.length);return /string|comment/.test(token.type)?' '.repeat(part.length):part;}).join('');
 });
 const receiver=code.at(-1)?.match(/(?:^|[^\w.])([A-Za-z_]\w*)\.$/)?.[1];
 if(!receiver)return false;
 const aliases=new Set<string>();const wrappers=new Set<string>();
 const invalidateTargets=(target:string)=>{
  const bare=target.trim().replace(/^[([]|[)\]]$/g,'');
  if(!/^[A-Za-z_\w\s,*()[\]]+$/.test(bare)){wrappers.clear();return;}
  for(const name of bare.match(/[A-Za-z_]\w*/g)||[]){wrappers.delete(name);aliases.delete(name);}
 };
 let functionIndent:number|undefined;let delimiterDepth=0;
 for(const text of code.slice(0,-1)) {
  const equals:number[]=[];const startedAtTop=delimiterDepth===0;
  for(let i=0;i<text.length;i++){
   if('([{'.includes(text[i]))delimiterDepth++;
   else if(')]}'.includes(text[i]))delimiterDepth=Math.max(0,delimiterDepth-1);
   else if(text[i]==='='&&delimiterDepth===0&&text[i+1]!=='='&&!/[=!<>]/.test(text[i-1]||''))equals.push(i);
  }
  const indent=text.match(/^\s*/)?.[0].length||0;
  if(text.trim()&&functionIndent!==undefined&&indent<=functionIndent){wrappers.clear();functionIndent=undefined;}
  const imported=text.match(/^from\s+meritz_strategy\s+import\s+Context(?:\s+as\s+([A-Za-z_]\w*))?\s*$/);
  if(imported){aliases.add(imported[1]||'Context');continue;}
  if(/^\s*(?:async\s+)?def\s/.test(text)){wrappers.clear();functionIndent=indent;for(const name of text.match(/[A-Za-z_]\w*/g)||[])aliases.delete(name);continue;}
  if(startedAtTop){
   const loop=text.match(/^\s*(?:async\s+)?for\s+(.+?)\s+in\b/);
   const deletion=text.match(/^\s*del\s+(.+)$/);
   if(loop)invalidateTargets(loop[1]);
   if(deletion)invalidateTargets(deletion[1]);
   if(/^\s*(?:async\s+)?with\b/.test(text)){
    for(const binding of text.matchAll(/\bas\s+([A-Za-z_]\w*|\([^)]*\))/g))invalidateTargets(binding[1]);
   }
  }
  if(equals.length){
   // Only top-level '=' is a binding. Keyword arguments/comparisons are not.
   let previous=0;
   for(const index of equals){
    const target=text.slice(previous,index).trim().replace(/[+*/%&|^:-]+$/,'').replace(/:[^:]*$/,'');
    invalidateTargets(target);previous=index+1;
   }
   const assignment=equals.length===1?text.slice(0,equals[0]).trim().match(/^([A-Za-z_]\w*)(?:\s*:[^=]+)?$/):null;
   const value=text.slice(equals[0]+1).trim();
   const constructor=value.match(/^([A-Za-z_]\w*)\s*\(\s*[A-Za-z_]\w*\s*\)\s*$/)?.[1];
   if(assignment&&constructor&&aliases.has(constructor)&&functionIndent!==undefined)wrappers.add(assignment[1]);
  }
 }
 return functionIndent!==undefined&&(code.at(-1)?.match(/^\s*/)?.[0].length||0)>functionIndent&&wrappers.has(receiver);
}

// Python tokenization and our providers run on the main thread. No worker URL,
// language server, CDN loader or eval is configured. Worker-dependent editor
// features (notably word-based suggestions) remain disabled.
export function PythonEditor({value,onChange,diagnostics,readOnly,theme}:{value:string;onChange:(value:string)=>void;diagnostics:Diagnostic[];readOnly:boolean;theme:string}) {
 const host = useRef<HTMLDivElement>(null);
 const editor = useRef<monaco.editor.IStandaloneCodeEditor|undefined>(undefined);
 const change = useRef(onChange);change.current=onChange;
 const initial = useRef(value);
 const applying = useRef(false);
 useEffect(()=>{
  const model = monaco.editor.createModel(initial.current,'python');
  const instance = monaco.editor.create(host.current!,{
   model,ariaLabel:'Python 전략 코드',automaticLayout:true,minimap:{enabled:false},
   fontSize:13,lineNumbers:'on',scrollBeyondLastLine:false,wordWrap:'on',
   tabSize:4,insertSpaces:true,autoIndent:'none',renderValidationDecorations:'on',
   wordBasedSuggestions:'off',links:false,colorDecorators:false,
   unicodeHighlight:{ambiguousCharacters:false,invisibleCharacters:false,nonBasicASCII:false},
   quickSuggestions:{other:true,comments:false,strings:false},
   padding:{top:12,bottom:12},accessibilitySupport:'on',
  });
  editor.current=instance;
  const subscription=model.onDidChangeContent(()=>{
   monaco.editor.setModelMarkers(model,'runtime',[]);
   if(!applying.current)change.current(model.getValue());
  });
  const completion=monaco.languages.registerCompletionItemProvider('python',{
   triggerCharacters:['.'],
   provideCompletionItems(current,position){
    if(current!==model)return {suggestions:[]};
    const word=current.getWordUntilPosition(position);
    if(!sdkReceiver(current,position.lineNumber,word.startColumn))return {suggestions:[]};
    const range=new monaco.Range(position.lineNumber,word.startColumn,position.lineNumber,word.endColumn);
    return {suggestions:methods.map(([name,signature,help])=>({label:name,kind:monaco.languages.CompletionItemKind.Method,detail:`Context.${signature}`,documentation:`${help}\n\n${executionHelp}`,insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,insertText:name==='buy'||name==='sell'?`${name}(\${1:quantity})`:name==='get_indicator'?`${name}("sma", period=20)`:`${name}()`,range}))};
   },
  });
  const hover=monaco.languages.registerHoverProvider('python',{
   provideHover(current,position){
    if(current!==model)return null;
    const word=current.getWordAtPosition(position);const method=methods.find(([name])=>name===word?.word);
    if(!word||!method||!sdkReceiver(current,position.lineNumber,word.startColumn))return null;
    return {range:new monaco.Range(position.lineNumber,word.startColumn,position.lineNumber,word.endColumn),contents:[{value:`Context.${method[1]}`},{value:method[2]},{value:executionHelp}]};
   },
  });
  // Escape followed by Tab exits even when the completion/hover UI is open.
  let escape=false;
  const keyboard=(event:KeyboardEvent)=>{
   if(event.key==='Escape'){escape=true;return;}
   if(event.key==='Tab'&&escape){event.preventDefault();event.stopPropagation();host.current?.parentElement?.querySelector<HTMLButtonElement>('.editor-exit')?.focus();}
   escape=false;
  };
  const element=host.current!;element.addEventListener('keydown',keyboard,true);
  return ()=>{element.removeEventListener('keydown',keyboard,true);subscription.dispose();completion.dispose();hover.dispose();instance.dispose();model.dispose();editor.current=undefined;};
 },[]);
 // Synchronize external definitions before the browser can accept another edit.
 // A passive effect can replay older props over newer Monaco input.
 useLayoutEffect(()=>{const model=editor.current?.getModel();if(model&&model.getValue()!==value){applying.current=true;model.setValue(value);applying.current=false;}},[value]);
 useEffect(()=>{editor.current?.updateOptions({readOnly});},[readOnly]);
 useEffect(()=>{monaco.editor.setTheme(theme==='dark'?'vs-dark':'vs');},[theme]);
 useEffect(()=>{
  const model=editor.current?.getModel();if(!model)return;
  monaco.editor.setModelMarkers(model,'runtime',diagnostics.flatMap(item=>{
   if(!Number.isInteger(item.line)||item.line!<1||item.line!>model.getLineCount())return [];
   const line=item.line!;const text=model.getLineContent(line);
   return [{severity:monaco.MarkerSeverity.Error,message:item.message,startLineNumber:line,endLineNumber:line,...diagnosticColumns(text,item.column)}];
  }));
 },[diagnostics,value]);
 return <div className="python-editor"><p className="micro" id="python-editor-help">Python 문법 강조 · Context SDK 자동완성 (Ctrl+Space) · 오류는 전략 검증 후 표시됩니다. 편집기에서 나오려면 Esc 다음 Tab을 누르세요.</p><div ref={host} className="monaco-host" aria-describedby="python-editor-help"/><button type="button" className="editor-exit">편집기 밖으로 이동</button></div>;
}
