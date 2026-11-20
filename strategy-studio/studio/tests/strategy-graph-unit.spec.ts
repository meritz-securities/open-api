import {explainCondition} from '../src/StrategySummary';
import {test,expect} from '@playwright/test';
import {baseCondition,defaultStrategy,type Condition} from '../src/api';
import {compileGraph,connect,fromDefinition,newNode,removeNode,storedLayoutWarning,type DSL} from '../src/strategyGraph';
const base=()=>defaultStrategy() as DSL;
test('nested DSL roundtrip and layout preserve strategy semantics',()=>{
 const definition=base();definition.entry={op:'all',conditions:[baseCondition(),{op:'any',conditions:[{op:'gt',left:{type:'rsi',period:14},right:{type:'constant',value:40}},baseCondition()]}]};definition.quantity=7;definition.risk={stopLossPct:2,takeProfitPct:9};
 const graph=fromDefinition(definition);const first=compileGraph(graph,definition);expect(first.valid).toBe(true);if(!first.valid)return;expect(first.definition).toEqual(definition);
 graph.nodes.find(n=>n.id==='entry.conditions.0.left')!.x=1123;const second=compileGraph(graph,definition);if(!second.valid)throw Error(second.errors.join());expect(second.layout['entry.conditions.0.left'].x).toBe(1123);expect(compileGraph(fromDefinition(definition,second.layout),definition)).toEqual(second);
});
test('port types, duplicate inputs, shared outputs and cycles are refused',()=>{
 const graph=fromDefinition(base());expect(connect(graph,{from:'entry.left',to:'sell',port:'condition'}).error).toContain('지표');
 expect(connect(graph,{from:'entry.right',to:'entry',port:'left'}).error).toBeTruthy();expect(connect(graph,{from:'entry',to:'sell',port:'condition'}).error).toBeTruthy();
 const a={...newNode({nodes:[],edges:[]},'all'),id:'a',inputs:1},b={...a,id:'b'};const cyclic={nodes:[a,b],edges:[{from:'a',to:'b',port:'condition.0'}]};expect(connect(cyclic,{from:'b',to:'a',port:'condition.0'}).error).toContain('순환');
});
test('missing roots, disconnected ports and orphan nodes cannot compile last valid DSL',()=>{
 const graph=fromDefinition(base());expect(compileGraph(removeNode(graph,'buy'),base()).valid).toBe(false);
 expect(compileGraph({...graph,edges:graph.edges.slice(1)},base()).valid).toBe(false);
 expect(compileGraph({...graph,nodes:[...graph.nodes,newNode(graph,'constant')]},base()).valid).toBe(false);
 const root=newNode(graph,'buy');expect(compileGraph({...graph,nodes:[...graph.nodes,root]},base()).valid).toBe(false);
});
test('exact shared Runtime depth and total-node boundaries include operands and both roots',()=>{
 const definition=base();const nested=(groups:number):Condition=>groups?{op:'all',conditions:[nested(groups-1)]}:baseCondition();
 definition.entry=nested(6);expect(compileGraph(fromDefinition(definition),definition).valid).toBe(true);definition.entry=nested(7);expect(compileGraph(fromDefinition(definition),definition).valid).toBe(false);
 definition.entry={op:'all',conditions:Array.from({length:20},baseCondition)};expect(compileGraph(fromDefinition(definition),definition).valid).toBe(true); // 61 + 3 = 64
 definition.entry.conditions.push(baseCondition());expect(compileGraph(fromDefinition(definition),definition).valid).toBe(false);
});
test('invalid indicator periods and nonfinite constants remain incomplete',()=>{
 for(const period of [1,251,2.5,NaN]){const definition=base();definition.entry={op:'gt',left:{type:'sma',period},right:{type:'constant',value:0}};expect(compileGraph(fromDefinition(definition),definition).valid).toBe(false);}
 const definition=base();definition.entry={op:'gt',left:{type:'constant',value:Infinity},right:{type:'field',field:'close'}};expect(compileGraph(fromDefinition(definition),definition).valid).toBe(false);
});
test('untrusted stored coordinates fall back with an explicit warning',()=>{
 const definition=base(),layout={entry:{x:Infinity,y:0},buy:{x:-1,y:0},bad:{x:10,y:20}};expect(storedLayoutWarning(definition,layout)).not.toBe('');expect(compileGraph(fromDefinition(definition,layout),definition).valid).toBe(true);
 expect(storedLayoutWarning(definition,null)).not.toBe('');expect(compileGraph(fromDefinition(definition,null as any),definition).valid).toBe(true);expect(storedLayoutWarning(definition,{})).toBe('');
});

test('fixed numeric comparisons explain always and never including impossible crossings',()=>{
 for(const [op,left,right,always] of [['gt',1,0,true],['gte',1,1,true],['lt',0,1,true],['lte',1,1,true],['gt',0,1,false],['cross_above',1,0,false],['cross_below',0,1,false]] as const){
  const text=explainCondition({op,left:{type:'constant',value:left},right:{type:'constant',value:right}});expect(text).toContain(always?'항상 만족':'성립하지 않는 조건');
 }
});
