// 서버 DSL 지표 라이브러리(runtime/dsl.py INDICATORS, 54종)의 한국어 표시 이름.
// 비주얼 빌더는 이 중 sma/ema/rsi(+가격·숫자)만 직접 편집한다. 나머지는 요약·조건 값 비교·읽기 전용 표시에만 쓴다.
// 이름이 없는 지표는 식별자를 그대로 보여 준다(값을 꾸며 내지 않는다).
export const indicatorNames:Record<string,string>={
 sma:'단순 이동평균',ema:'지수 이동평균',rsi:'RSI',wma:'가중 이동평균',dema:'이중 지수 이동평균 DEMA',tema:'삼중 지수 이동평균 TEMA',
 hma:'헐 이동평균 HMA',kama:'적응 이동평균 KAMA',zlema:'지연 제거 이동평균 ZLEMA',trima:'삼각 이동평균',t3:'T3 이동평균',vwma:'거래량 가중 이동평균',vwap:'VWAP',
 bollinger:'볼린저 밴드',keltner:'켈트너 채널',donchian:'돈치안 채널',macd:'MACD',ppo:'PPO',roc:'변화율 ROC',momentum:'모멘텀',cmo:'CMO',cci:'CCI',
 stochastic:'스토캐스틱',stochrsi:'스토캐스틱 RSI',williams_r:'윌리엄스 %R',ultimate_oscillator:'얼티밋 오실레이터',awesome_oscillator:'어썸 오실레이터',
 trix:'TRIX',fisher_transform:'피셔 변환',adx:'추세 강도 ADX',aroon:'아룬',aroon_oscillator:'아룬 오실레이터',vortex:'보텍스',dpo:'DPO',coppock:'코폭 곡선',
 atr:'ATR 변동폭',stdev:'이동 표준편차',historical_volatility:'역사적 변동성',chaikin_volatility:'차이킨 변동성',mass_index:'매스 인덱스',ulcer_index:'얼서 인덱스',
 obv:'OBV',ad_line:'A/D 라인',cmf:'차이킨 자금 흐름 CMF',chaikin_oscillator:'차이킨 오실레이터',mfi:'MFI',force_index:'포스 인덱스',ease_of_movement:'EMV',pvt:'PVT',
 elder_ray:'엘더 레이',ichimoku:'일목균형표',parabolic_sar:'파라볼릭 SAR',pivot_points:'피벗 포인트',rvi:'RVI',
};
export const componentNames:Record<string,string>={
 mid:'중심선',upper:'상단',lower:'하단',macd:'MACD선',signal:'시그널선',hist:'히스토그램',ppo:'PPO선',k:'%K',d:'%D',fast_k:'빠른 %K',stochrsi:'스토캐스틱 RSI',
 adx:'ADX',plus_di:'+DI',minus_di:'-DI',up:'상승선',down:'하락선',vi_plus:'VI+',vi_minus:'VI-',bull:'불 파워',bear:'베어 파워',rvi:'RVI',
 tenkan:'전환선',kijun:'기준선',senkou_a:'선행스팬 A',senkou_b:'선행스팬 B',chikou:'후행스팬',p:'피벗',r1:'저항 1',r2:'저항 2',r3:'저항 3',s1:'지지 1',s2:'지지 2',s3:'지지 3',
};
export const parameterNames:Record<string,string>={period:'기간',wma_period:'가중 평균 기간',fast:'빠른 기간',slow:'느린 기간',signal:'시그널 기간',num_std:'표준편차 배수',mult:'ATR 배수',atr_period:'ATR 기간',annualize:'연간 거래일 수',scale:'거래량 조정값',step:'초기 가속 비율',max_step:'최대 가속 비율',vfactor:'T3 보정값'};
export type ExtendedOperand={type:string;component?:string;[key:string]:unknown};
export const builderOperandTypes=['field','constant','sma','ema','rsi'] as const;
export function indicatorLabel(type:string):string{return indicatorNames[type]||type;}
/** 빌더 외 지표를 사람이 읽는 문장으로. 예: "볼린저 밴드 상단 (기간 20 · 표준편차 배수 2)" */
export function describeExtendedOperand(operand:ExtendedOperand):string{
 const params=Object.entries(operand).filter(([key,value])=>key!=='type'&&key!=='component'&&typeof value==='number').map(([key,value])=>`${parameterNames[key]||key} ${value}`);
 const component=operand.component?` ${componentNames[operand.component]||operand.component}`:'';
 return `${indicatorLabel(operand.type)}${component}${params.length?` (${params.join(' · ')})`:''}`;
}
