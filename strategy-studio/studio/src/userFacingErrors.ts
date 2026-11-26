/** Translate persisted and transport errors that older/local Runtime versions emit. */
const knownRuntimeErrors:Record<string,string>={
 'Previous execution stopped before finalization':'앱이 종료되기 전에 실행이 완료되지 않았습니다.',
 'JSON required':'요청 형식을 확인하지 못했습니다. 다시 시도해 주세요.',
 'Invalid JSON':'요청 내용을 확인하지 못했습니다. 입력값을 확인하고 다시 시도해 주세요.',
 'Request too large':'요청 크기가 허용 범위를 넘었습니다. 입력 내용을 줄이고 다시 시도해 주세요.'
};
export function userFacingRuntimeError(value:string):string{
 const message=value.trim();
 if(knownRuntimeErrors[message])return knownRuntimeErrors[message];
 if(/failed to fetch|load failed|networkerror|fetch failed/i.test(message))return '내 PC 실행기와 연결이 끊겼습니다. 실행기를 다시 열고 시도해 주세요.';
 if(/time.?out|timed out|signal timed out|aborterror/i.test(message))return '요청 시간이 초과되었습니다. 연결 상태를 확인하고 다시 시도해 주세요.';
 // 영문 런타임 예외가 고객 화면에 그대로 노출되지 않도록 알기 쉬운 안내로 바꾼다.
 if(/[A-Za-z]/.test(message)&&!/[가-힣]/.test(message))return '요청 처리 중 문제가 발생했습니다. 입력값과 연결 상태를 확인하고 다시 시도해 주세요.';
 return message||'요청 처리 중 문제가 발생했습니다. 다시 시도해 주세요.';
}
