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
