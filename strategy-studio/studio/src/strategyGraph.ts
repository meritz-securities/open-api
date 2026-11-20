import type {Condition,Definition,EditorLayout,Operand} from './api';
export type DSL=Extract<Definition,{kind:'dsl'}>;
export type NodeKind='field'|'constant'|'sma'|'ema'|'rsi'|'compare'|'all'|'any'|'buy'|'sell';
export type GraphNode={id:string;kind:NodeKind;x:number;y:number;operand?:Operand;op?:string;inputs?:number};
export type Edge={from:string;to:string;port:string};
export type StrategyGraph={nodes:GraphNode[];edges:Edge[]};
export type Compilation={valid:true;definition:DSL;layout:EditorLayout}|{valid:false;errors:string[]};
export const nodeNames:Record<NodeKind,string>={field:'가격',constant:'숫자',sma:'이동평균',ema:'지수 이동평균',rsi:'상대강도 RSI',compare:'비교',all:'AND',any:'OR',buy:'매수',sell:'매도'};
export const comparisonNames:Record<string,string>={gt:'초과',gte:'이상',lt:'미만',lte:'이하',cross_above:'상향 돌파',cross_below:'하향 돌파'};
export const isOperand=(node:GraphNode)=>['field','constant','sma','ema','rsi'].includes(node.kind);
/** 빌더가 직접 편집할 수 있는 정의인지. 피연산자가 모두 가격·숫자·SMA·EMA·RSI 여야 한다.
 *  그 외 지표(볼린저·MACD 등 서버 DSL 54종)를 쓰는 유효한 정의는 읽기 전용으로 저장·검증·실행만 한다. */
export function builderSupports(definition:DSL):boolean{
 const supported=new Set<string>(['field','constant','sma','ema','rsi']);let count=0;
 const visit=(value:Condition|Operand,depth:number):boolean=>{
  if(++count>256||depth>16)return false;
  if('type'in value)return supported.has((value as {type:string}).type);
  return 'conditions'in value?value.conditions.every(child=>visit(child,depth+1)):visit(value.left,depth+1)&&visit(value.right,depth+1);
 };
 return visit(definition.entry,1)&&visit(definition.exit,1);
}
export function ports(node:GraphNode):string[]{return node.kind==='compare'?['left','right']:node.kind==='buy'||node.kind==='sell'?['condition']:node.kind==='all'||node.kind==='any'?Array.from({length:Math.min(32,Math.max(0,node.inputs??2))},(_,i)=>`condition.${i}`):[];}
export function newNode(graph:StrategyGraph,kind:NodeKind,x=30,y=30):GraphNode{
 let i=1;while(graph.nodes.some(node=>node.id===`node-${i}`))i++;
 return {id:`node-${i}`,kind,x,y,...(kind==='compare'?{op:'gt'}:kind==='all'||kind==='any'?{inputs:2}:kind==='field'?{operand:{type:'field',field:'close'} as Operand}:kind==='constant'?{operand:{type:'constant',value:0} as Operand}:['sma','ema','rsi'].includes(kind)?{operand:{type:kind,period:20} as Operand}:{})};
}
function validPosition(value:unknown):value is {x:number;y:number}{return typeof value==='object'&&value!==null&&Object.keys(value).length===2&&'x'in value&&'y'in value&&[value.x,value.y].every(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=10000);}
function positionAt(layout:unknown,path:string){if(typeof layout!=='object'||layout===null||Array.isArray(layout)||!Object.hasOwn(layout,path))return undefined;const value=(layout as Record<string,unknown>)[path];return validPosition(value)?value:undefined;}
export function storedLayoutWarning(definition:DSL,layout:unknown):string{
 if(layout===undefined)return '';
 const paths=new Set(fromDefinition(definition).nodes.map(node=>node.id));
 return typeof layout!=='object'||layout===null||Array.isArray(layout)||Object.keys(layout).length>128||Object.entries(layout).some(([path,value])=>!paths.has(path)||!validPosition(value))?'저장된 배치 일부가 유효하지 않아 기본 위치로 표시합니다.':'';
}
export function fromDefinition(definition:DSL,layout:EditorLayout={}):StrategyGraph{
 const graph:StrategyGraph={nodes:[],edges:[]};let row=0;
 const depthOf=(value:Condition|Operand):number=>'type'in value?1:1+Math.max(...('conditions'in value?value.conditions:[value.left,value.right]).map(depthOf));
 const maxDepth=Math.max(depthOf(definition.entry),depthOf(definition.exit));
 const place=(node:GraphNode,path:string,depth:number)=>{const fallback={x:30+(maxDepth-depth)*220,y:30+row*120};const position=positionAt(layout,path)||fallback;graph.nodes.push({...node,...position,id:path});return path;};
 function visit(value:Condition|Operand,path:string,depth:number):string{
  if('type'in value){const id=place({id:path,kind:value.type,x:0,y:0,operand:structuredClone(value)},path,depth);row++;return id;}
  const id=place({id:path,kind:'conditions'in value?value.op:'compare',op:value.op,inputs:'conditions'in value?value.conditions.length:undefined,x:0,y:0},path,depth);
  if('conditions'in value)value.conditions.forEach((child,i)=>graph.edges.push({from:visit(child,`${path}.conditions.${i}`,depth+1),to:id,port:`condition.${i}`}));
  else for(const side of ['left','right'] as const)graph.edges.push({from:visit(value[side],`${path}.${side}`,depth+1),to:id,port:side});
  if(!positionAt(layout,path)){const children=graph.edges.filter(edge=>edge.to===id).map(edge=>graph.nodes.find(node=>node.id===edge.from)!.y);graph.nodes.find(node=>node.id===id)!.y=(Math.min(...children)+Math.max(...children))/2;}
  return id;
 }
 for(const [path,kind] of [['entry','buy'],['exit','sell']] as const){const id=visit(definition[path],path,1);const position=positionAt(layout,kind)||{x:30+maxDepth*220,y:graph.nodes.find(n=>n.id===id)!.y};graph.nodes.push({id:kind,kind,...position});graph.edges.push({from:id,to:kind,port:'condition'});}
 return graph;
}
function structuralErrors(graph:StrategyGraph,complete:boolean):string[]{
 const errors:string[]=[];const nodes=new Map(graph.nodes.map(n=>[n.id,n]));
 if(graph.nodes.length>128||graph.edges.length>128)return ['그래프는 최대 128개 노드·연결입니다.'];
 if(nodes.size!==graph.nodes.length)errors.push('노드 ID가 중복됩니다.');
 const targets=new Set<string>(),parents=new Set<string>();
 const label=(node:GraphNode)=>`${nodeNames[node.kind]} #${graph.nodes.indexOf(node)+1}`;
 const portName=(port:string)=>port==='left'?'왼쪽':port==='right'?'오른쪽':port==='condition'?'조건':`조건 ${Number(port.split('.')[1])+1}`;
 for(const node of graph.nodes){
  if(!Object.hasOwn(nodeNames,node.kind))errors.push('지원하지 않는 노드입니다.');
  if(![node.x,node.y].every(v=>Number.isFinite(v)&&v>=0&&v<=10000))errors.push('노드 위치는 0~10000 범위입니다.');
  if((node.kind==='all'||node.kind==='any')&&(!Number.isInteger(node.inputs)||node.inputs!<1||node.inputs!>32))errors.push('AND/OR 입력은 1~32개입니다.');
 }
 for(const edge of graph.edges){const from=nodes.get(edge.from),to=nodes.get(edge.to),target=`${edge.to}:${edge.port}`;
  if(!from||!to){errors.push('연결 대상 노드가 없습니다.');continue;}
  if(!ports(to).includes(edge.port))errors.push('존재하지 않는 입력 포트입니다.');
  if(from.kind==='buy'||from.kind==='sell'||isOperand(from)!==(to.kind==='compare'))errors.push('지표는 비교 입력에, 조건은 AND/OR·매수·매도에 연결하세요.');
  if(targets.has(target))errors.push('한 입력에는 하나만 연결할 수 있습니다.');targets.add(target);
  if(parents.has(from.id))errors.push('출력은 한 곳에만 연결하세요. 재사용하려면 노드를 복제하세요.');parents.add(from.id);
 }
 const visiting=new Set<string>(),visited=new Set<string>();
 function cycle(id:string):boolean{if(visiting.has(id))return true;if(visited.has(id))return false;visiting.add(id);for(const edge of graph.edges.filter(e=>e.from===id))if(cycle(edge.to))return true;visiting.delete(id);visited.add(id);return false;}
 if(graph.nodes.some(node=>cycle(node.id)))errors.push('순환 연결은 허용하지 않습니다.');
 if(complete){
  for(const kind of ['buy','sell'])if(graph.nodes.filter(n=>n.kind===kind).length!==1)errors.push(`${kind==='buy'?'매수':'매도'} 노드가 정확히 하나 필요합니다.`);
  for(const node of graph.nodes){for(const port of ports(node))if(!targets.has(`${node.id}:${port}`))errors.push(`${label(node)}: ${portName(port)} 입력을 연결하세요.`);if(node.kind!=='buy'&&node.kind!=='sell'&&!parents.has(node.id))errors.push(`${label(node)}: 출력이 연결되지 않았습니다.`);}
 }
 return [...new Set(errors)];
}
export function connect(graph:StrategyGraph,edge:Edge):{graph?:StrategyGraph;error?:string}{
 const candidate={...graph,edges:[...graph.edges,edge]};const errors=structuralErrors(candidate,false);return errors.length?{error:errors[0]}:{graph:candidate};
}
export function removeNode(graph:StrategyGraph,id:string):StrategyGraph{return {nodes:graph.nodes.filter(n=>n.id!==id),edges:graph.edges.filter(e=>e.from!==id&&e.to!==id)};}
export function compileGraph(graph:StrategyGraph,base:DSL):Compilation{
 const errors=structuralErrors(graph,true);if(errors.length)return {valid:false,errors};
 const nodes=new Map(graph.nodes.map(node=>[node.id,node])),layout:EditorLayout={},seen=new Set<string>();let count=0;
 function walk(id:string,path:string,depth:number):Condition|Operand{
  const node=nodes.get(id)!;seen.add(id);layout[path]={x:node.x,y:node.y};count++;
  if(count>64||depth>8)throw Error('전략 전체는 64개 노드·깊이 8까지입니다.');
  if(isOperand(node)){
   const operand=node.operand;
   if(!operand||operand.type!==node.kind)throw Error(`${nodeNames[node.kind]} #${graph.nodes.indexOf(node)+1}: 지표 종류가 일치하지 않습니다.`);
   if(operand.type==='field'&&!['open','high','low','close'].includes(operand.field)||operand.type==='constant'&&(!Number.isFinite(operand.value)||Math.abs(operand.value)>1e12)||'period'in operand&&(!Number.isInteger(operand.period)||operand.period<2||operand.period>250))throw Error(`${nodeNames[node.kind]} #${graph.nodes.indexOf(node)+1}: 가격·숫자·기간을 확인하세요 (기간은 2~250의 정수).`);
   return structuredClone(operand);
  }
  const input=(port:string,nextPath:string)=>walk(graph.edges.find(e=>e.to===id&&e.port===port)!.from,nextPath,depth+1);
  if(node.kind==='all'||node.kind==='any')return {op:node.kind,conditions:ports(node).map((port,i)=>input(port,`${path}.conditions.${i}`) as Condition)};
  if(node.kind==='compare'&&Object.hasOwn(comparisonNames,node.op||''))return {op:node.op as 'gt',left:input('left',`${path}.left`) as Operand,right:input('right',`${path}.right`) as Operand};
  throw Error(`비교 #${graph.nodes.indexOf(node)+1}: 비교 방식을 확인하세요.`);
 }
 try{
  const result=structuredClone(base);
  for(const [kind,path] of [['buy','entry'],['sell','exit']] as const){const root=graph.nodes.find(n=>n.kind===kind)!;seen.add(root.id);layout[kind]={x:root.x,y:root.y};result[path]=walk(graph.edges.find(e=>e.to===root.id)!.from,path,1) as Condition;}
  if(seen.size!==graph.nodes.length)throw Error('매수·매도에 도달하지 않는 노드가 있습니다.');
  return {valid:true,definition:result,layout};
 }catch(error){return {valid:false,errors:[error instanceof Error?error.message:'그래프를 확인하세요.']};}
}
