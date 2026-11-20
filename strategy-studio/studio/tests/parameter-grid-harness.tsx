// Test-only parent replaces the run while promises remain pending. Production
// disables that navigation during calls; this isolates component teardown guards.
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ParameterGrid} from '../src/ParameterGrid';
import type {Run} from '../src/api';

const definition={schemaVersion:1,kind:'dsl',name:'Callback test',quantity:1,allowPyramiding:false,risk:{stopLossPct:null,takeProfitPct:null},entry:{op:'gt',left:{type:'field',field:'close'},right:{type:'constant',value:0}},exit:{op:'lt',left:{type:'field',field:'close'},right:{type:'constant',value:0}}};
function Harness(){
 const [id,setId]=useState('runA'),[pending,setPending]=useState<{id:string;resolve:(value:unknown)=>void;options:unknown}[]>([]);
 const run={id,revisionId:'revision-'+id,report:{metrics:{maxDrawdownPct:1}}} as unknown as Run;
 const call=async(action:string,args:Record<string,unknown>)=>{
  if(action==='get_strategy')return {id:args.revisionId,definition};
  if(action==='validate_robustness')return new Promise(resolve=>setPending(old=>[...old,{id:args.runId as string,resolve,options:args.options}]));
  throw Error('Unexpected harness action '+action);
 };
 return <><button onClick={()=>setId('runB')}>다른 실행으로 전환</button><p role="status" aria-label="대기 요청">{pending.map(item=>item.id).join(',')}</p>{pending.map(item=><button key={item.id} onClick={()=>{item.resolve({id:'result-'+item.id,runId:item.id,method:'parameter_grid',options:item.options,result:{status:'completed',candidateCount:1,bestCandidateIndex:1,bestCandidateImproved:true,baselineComparable:true,baselineReturnPct:0,deadlineSeconds:120,assumptions:[],results:[{index:1,parameterValues:{quantity:1},status:'succeeded',result:{runId:'child-'+item.id,metrics:{totalReturnPct:1,maxDrawdownPct:1}}}]}});setPending(old=>old.filter(other=>other!==item));}}>{item.id} 응답 완료</button>)}<ParameterGrid key={run.id} run={run} call={call} busy={false} onExecutionStart={()=>{}}/></>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
