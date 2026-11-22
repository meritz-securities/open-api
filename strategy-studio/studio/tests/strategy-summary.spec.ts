import {test,expect} from '@playwright/test';
import {explainCondition} from '../src/StrategySummary';

test('a false branch does not describe an entire OR strategy as unable to trade',()=>{
 const text=explainCondition({op:'any',conditions:[
  {op:'gt',left:{type:'constant',value:0},right:{type:'constant',value:1}},
  {op:'gt',left:{type:'field',field:'close'},right:{type:'constant',value:100}},
 ]});
 expect(text).toContain('하나 이상 만족');
 expect(text).toContain('성립하지 않는 조건');
 expect(text).toContain('종가 값이 100보다 높을 때');
 expect(text).not.toContain('거래하지 않습니다');
});
