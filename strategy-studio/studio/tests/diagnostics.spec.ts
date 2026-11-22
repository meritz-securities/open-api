import {test,expect} from '@playwright/test';
import {diagnosticColumns} from '../src/diagnostics';

test('Runtime code-point columns map to Monaco UTF-16 without splitting emoji',()=>{
 expect(diagnosticColumns('a😀한x',3)).toEqual({startColumn:4,endColumn:5});
 expect(diagnosticColumns('a😀한x',2)).toEqual({startColumn:2,endColumn:4});
 expect(diagnosticColumns('a😀한x',5)).toEqual({startColumn:6,endColumn:6});
 expect(diagnosticColumns('abc',null)).toEqual({startColumn:1,endColumn:2});
 expect(diagnosticColumns('',1)).toEqual({startColumn:1,endColumn:1});
});
