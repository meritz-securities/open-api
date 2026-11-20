export interface Candle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface Settings {
  fast: number;
  slow: number;
  initialCash: number;
  feeBps: number;
  slippageBps: number;
}

export interface Trade {
  id: string;
  date: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  fee: number;
  reason: string;
  signalDate: string;
}

export type DataSource = { kind: 'synthetic'; label: string } | {
  kind: 'meritz'; adjustment?: 'requested-adjusted'; label: string; symbol: string; market: 'J' | 'NJ' | 'TJ';
  startDate: string; endDate: string; fetchedAt: string;
};

export interface Report {
  schemaVersion: 1;
  runId: string;
  source: DataSource;
  strategy: { fast: number; slow: number } | { name: string; codeHash: string; interface: 'on_bar_v1' };
  assumptions: {
    initialCash: number;
    feeBps: number;
    slippageBps: number;
    fillRule: string;
  };
  metrics: {
    totalReturnPct: number;
    maxDrawdownPct: number;
    benchmarkReturnPct: number;
    tradeCount: number;
    finalEquity: number;
    totalFees: number;
  };
  equity: { date: string; equity: number; benchmark: number; drawdownPct: number }[];
  trades: Trade[];
  warnings: string[];
}
