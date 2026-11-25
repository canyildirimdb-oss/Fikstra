import type {
  BulletinLeague,
  CouponData,
  DashboardSummary,
  MatchDetail,
  PerformanceData,
  PredictionItem,
} from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getSummary: () => requestJson<DashboardSummary>("/api/dashboard/summary"),

  triggerRefresh: () =>
    requestJson<{ status: "started" | "cooldown" | "already_running"; retry_after_sec?: number }>(
      "/api/refresh",
      { method: "POST" }
    ),

  getPredictions: (params: {
    todayOnly: boolean;
    majorOnly: boolean;
    minOdd: number;
    minConfidence: number;
    market?: string;
    search?: string;
  }) => {
    const qs = new URLSearchParams({
      today_only: String(params.todayOnly),
      major_only: String(params.majorOnly),
      min_odd: String(Math.max(1.3, params.minOdd)),
      min_confidence: String(params.minConfidence),
    });
    if (params.market && params.market !== "ALL") qs.set("market", params.market);
    if (params.search?.trim()) qs.set("search", params.search.trim());
    return requestJson<{ count: number; items: PredictionItem[] }>(`/api/predictions?${qs}`);
  },

  getBulletin: (params: {
    todayOnly: boolean;
    majorOnly: boolean;
    minOdd: number;
    market: string;
    search?: string;
  }) => {
    const qs = new URLSearchParams({
      today_only: String(params.todayOnly),
      major_only: String(params.majorOnly),
      min_odd: String(params.minOdd),
      market: params.market,
    });
    if (params.search?.trim()) qs.set("search", params.search.trim());
    return requestJson<{ leagues_count: number; leagues: BulletinLeague[] }>(`/api/matches?${qs}`);
  },

  getMatchDetail: (matchId: number, marketKey?: string, selectionKey?: string) => {
    const qs = new URLSearchParams();
    if (marketKey) qs.set("market_key", marketKey);
    if (selectionKey) qs.set("selection_key", selectionKey);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return requestJson<MatchDetail>(`/api/matches/${matchId}${suffix}`);
  },

  generateAiSummary: (matchId: number, force = false) =>
    requestJson<{ match_id: number; ai_summary: string; ai_source: string }>(
      `/api/matches/${matchId}/ai-summary?force=${force}`,
      { method: "POST" }
    ),

  getCoupons: () => requestJson<{ coupons: CouponData[] }>("/api/coupons"),

  getPerformance: () => requestJson<PerformanceData>("/api/performance"),

  getVapidKey: () => requestJson<{ public_key: string }>("/api/push/vapid-public-key"),

  subscribePush: (sub: { endpoint: string; p256dh: string; auth: string }) =>
    requestJson<{ status: string }>("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(sub),
    }),
};
