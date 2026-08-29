export interface ApiErrorBody {
  code: string;
  message: string;
  fields: Record<string, string[]> | null;
}

/** The §13.1 error envelope, surfaced as a throwable. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: Record<string, string[]> | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.fields = body.fields;
  }

  /** First message for a field, for binding inline errors to inputs. */
  fieldError(name: string): string | undefined {
    return this.fields?.[name]?.[0];
  }
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_at: string;
}

export interface User {
  id: string;
  email: string;
  created_at: string;
}

export type RiskAppetite = "CONSERVATIVE" | "MODERATE" | "AGGRESSIVE";
export type InvestmentGoal = "RETIREMENT" | "GROWTH" | "WEALTH_CREATION";
export type InvestmentExperience = "NONE" | "BEGINNER" | "INTERMEDIATE" | "ADVANCED";
export type FinancialLiteracy = "LOW" | "MEDIUM" | "HIGH";

export interface FinancialProfile {
  id: string;
  age: number;
  income: string;
  savings: string;
  risk_appetite: RiskAppetite;
  investment_goal: InvestmentGoal;
  investment_horizon: number;
  experience: InvestmentExperience;
  financial_literacy: FinancialLiteracy;
  created_at: string;
  updated_at: string;
}

export interface ProfileCompleteness {
  complete: boolean;
  missing_fields: string[];
}

// ---------------------------------------------------------------------------
// Market data (M2) — mirrors app/schemas/market.py
// ---------------------------------------------------------------------------

export type AssetType = "EQUITY" | "ETF" | "BOND" | "COMMODITY" | "INDEX";
export type AssetClass = "EQUITY" | "BOND" | "COMMODITY" | "REAL_ESTATE" | "CASH";

export interface Asset {
  id: string;
  symbol: string;
  name: string | null;
  asset_type: AssetType;
  asset_class: AssetClass;
  currency: string;
  exchange: string | null;
  is_active: boolean;
}

/** Numeric fields arrive as strings — see the note in lib/format.ts. */
export interface PriceBar {
  date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  adj_close: string;
  volume: number;
}

/** The §13.1 list envelope. */
export interface Paginated<T> {
  data: T[];
  next_cursor: string | null;
}

export interface MarketHistory extends Paginated<PriceBar> {
  asset: Asset;
}

// ---------------------------------------------------------------------------
// Risk and predictions (M3) — mirrors app/schemas/risk.py
// ---------------------------------------------------------------------------

export type RiskCategory = "LOW" | "MEDIUM" | "HIGH";
export type TrendDirection = "UP" | "DOWN" | "FLAT";

export interface RiskFactor {
  factor: string;
  contribution: number;
  detail: string;
}

export interface RiskAssessment {
  id: string;
  risk_category: RiskCategory;
  risk_score: string;
  top_factors: RiskFactor[];
  model_version: string;
  created_at: string;
  disclaimer: string;
}

export interface Prediction {
  symbol: string;
  prediction_date: string;
  horizon_days: number;
  predicted_return: string;
  trend: TrendDirection;
  confidence: string;
  model_version: string;
  expected_return: string | null;
  volatility: number | null;
  momentum: number | null;
  risk_score: number | null;
  /** FR-09 — metrics that could not be computed, named rather than silently absent. */
  unavailable: string[];
  disclaimer: string;
}

// ---------------------------------------------------------------------------
// Portfolio and recommendations (M4) — mirrors app/schemas/portfolio.py
// ---------------------------------------------------------------------------

export interface Holding {
  symbol: string;
  name: string | null;
  asset_class: AssetClass;
  weight: string;
  reason: string | null;
  recommendation_id: string | null;
}

export interface ClassBand {
  floor: number;
  cap: number;
}

/** The constraint set in force when the portfolio was generated. */
export interface PortfolioObjective {
  risk_aversion?: number;
  max_weight_per_asset?: number;
  class_bands?: Record<string, ClassBand>;
  notes?: string[];
  summary?: string;
  mu_source?: string;
}

export interface Portfolio {
  id: string;
  risk_category: RiskCategory;
  expected_return: string;
  expected_risk: string;
  model_version: string;
  created_at: string;
  holdings: Holding[];
  objective: PortfolioObjective | null;
  explanation: string | null;
  disclaimer: string;
}

export interface Explanation {
  recommendation_id: string;
  symbol: string;
  score: string;
  reason: string;
  model_version: string;
  portfolio_id: string | null;
  weight: string | null;
  portfolio_explanation: string | null;
  created_at: string;
  disclaimer: string;
}

/** FR-13 (advanced) — one feature's Shapley contribution to a prediction. */
export interface FeatureContribution {
  feature: string;
  label: string;
  /** Null when the feature itself had no value that day; the attribution still holds. */
  value: number | null;
  contribution: number;
  direction: "increases" | "decreases";
}

export interface PredictionExplanation {
  symbol: string;
  prediction_date: string;
  horizon_days: number;
  model_version: string;
  predicted_return: number;
  base_value: number;
  contributions: FeatureContribution[];
  /** The payload is truncated, so `base_value + shown` does not close. These say so. */
  contributions_shown: number;
  contributions_total: number;
  reproduced: boolean;
  disclaimer: string;
}

/** FR-14 — every metric is nullable, and null means suppressed, never zero. */
export interface FairnessGroup {
  group: string;
  size: number;
  suppressed: boolean;
  risk_distribution: Record<string, number> | null;
  mean_risk_score: number | null;
  mean_equity_weight: number | null;
  portfolio_rate: number | null;
}

export interface FairnessDisparity {
  metric: string;
  ratio: number;
  lowest_group: string;
  highest_group: string;
  lowest_rate: number;
  highest_rate: number;
  flagged: boolean;
}

export interface FairnessDimension {
  dimension: string;
  label: string;
  groups: FairnessGroup[];
  disparity: FairnessDisparity | null;
  note: string | null;
}

export interface FairnessReport {
  population: number;
  reportable_population: number;
  min_group_size: number;
  dimensions: FairnessDimension[];
  disclaimer: string;
}

/** §19 — one point on a growth-of-1 curve. */
export interface EquityPoint {
  date: string;
  value: number;
}

export interface BacktestMetrics {
  total_return: number;
  annualised_return: number;
  volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
}

export interface Backtest {
  portfolio_id: string;
  /** The window actually used, which is not always the one requested — see below. */
  start: string;
  end: string;
  months_requested: number;
  /**
   * The last date the production predictor saw. §19 requires the backtest period to
   * be separate from the training period, so the start is pushed past this when the
   * two would overlap. Null when no model is promoted.
   */
  training_end: string | null;
  rebalances: number;
  portfolio: BacktestMetrics;
  benchmark: BacktestMetrics;
  benchmark_symbol: string;
  transaction_cost_bps: number;
  total_costs: number;
  equity_curve: EquityPoint[];
  benchmark_curve: EquityPoint[];
  disclaimer: string;
}
