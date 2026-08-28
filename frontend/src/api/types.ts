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
