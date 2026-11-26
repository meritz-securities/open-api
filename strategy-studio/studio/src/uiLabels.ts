export const runStatus=(status:string)=>({succeeded:'완료',failed:'실패',running:'실행 중',queued:'대기',cancelled:'취소',interrupted:'중단'} as Record<string,string>)[status]||'상태 미확인';
export function localTime(value?:string){
 if(!value)return '시각 미확인';
 const date=new Date(value);if(!Number.isFinite(date.getTime()))return '시각 미확인';
 return new Intl.DateTimeFormat('ko-KR',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date)+' (내 PC 시간)';
}
