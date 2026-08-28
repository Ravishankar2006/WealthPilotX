/**
 * Typed wrappers for the M2–M4 endpoints.
 *
 * Thin on purpose: they exist so the field names live in one place rather than in
 * a dozen `api.get<...>("/some/path")` calls, and so a backend rename surfaces as a
 * type error here instead of an undefined at runtime in a component.
 *
 * `notFoundAsNull` is the important helper. A 404 from `/risk/latest` or
 * `/portfolio/current` is not an error — it is the ordinary condition for a user
 * who has not run those yet, and the dashboard treats it as an empty state.
 * Without this, every new user's first dashboard load would look like a failure.
 */

import { api } from "./client";
import { ApiError } from "./types";
import type {
  Asset,
  Explanation,
  MarketHistory,
  Paginated,
  Portfolio,
  Prediction,
  RiskAssessment,
} from "./types";

async function notFoundAsNull<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export const market = {
  assets: (params: { limit?: number; cursor?: string; asset_class?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.cursor) query.set("cursor", params.cursor);
    if (params.asset_class) query.set("asset_class", params.asset_class);
    const suffix = query.toString();
    return api.get<Paginated<Asset>>(`/market/assets${suffix ? `?${suffix}` : ""}`);
  },

  history: (symbol: string, limit = 180) =>
    api.get<MarketHistory>(`/market/${encodeURIComponent(symbol)}?limit=${limit}`),

  /** Null when the asset has no stored prediction — an ordinary state, not a failure. */
  prediction: (symbol: string) =>
    notFoundAsNull(api.get<Prediction>(`/market/${encodeURIComponent(symbol)}/prediction`)),
};

export const risk = {
  latest: () => notFoundAsNull(api.get<RiskAssessment>("/risk/latest")),
  analyze: () => api.post<RiskAssessment>("/risk/analyze"),
};

export const portfolio = {
  current: () => notFoundAsNull(api.get<Portfolio>("/portfolio/current")),
  generate: () => api.post<Portfolio>("/portfolio/generate"),
  history: (params: { limit?: number; cursor?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set("limit", String(params.limit));
    if (params.cursor) query.set("cursor", params.cursor);
    const suffix = query.toString();
    return api.get<Paginated<Portfolio>>(`/portfolio/history${suffix ? `?${suffix}` : ""}`);
  },
  explanation: (recommendationId: string) =>
    api.get<Explanation>(`/recommendation/${encodeURIComponent(recommendationId)}/explanation`),
};
