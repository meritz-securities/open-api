export type Diagnostic = {line?:number|null; column?:number|null; message:string};

// Runtime columns are 1-based Unicode code points. Monaco uses 1-based UTF-16.
export function diagnosticColumns(text:string,column?:number|null) {
 const pointColumn=Number.isInteger(column)&&column!>0?column!:1;
 const startColumn=Array.from(text).slice(0,pointColumn-1).join('').length+1;
 const next=Array.from(text.slice(startColumn-1))[0];
 return {startColumn,endColumn:Math.min(startColumn+(next?.length||1),text.length+1)};
}
