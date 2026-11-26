import type {
  AdminOverviewData,
  CouponData,
  DashboardSummary,
  MatchDetail,
  NotificationItem,
  PerformanceBreakdown,
  PerformanceData,
  PredictionItem,
  PredictionStatusItem,
  PulseData,
  SiteAnnouncement,
  SportotoCurrentResponse,
  SportotoHistoryItem,
  SportotoPlan,
  SportotoWeekData,
} from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getSummary: () => requestJson<DashboardSummary>("/api/dashboard/summary"),

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

  getCouponHistory: () => requestJson<{ coupons: CouponData[] }>("/api/coupons/history"),

  getPerformance: () => requestJson<PerformanceData>("/api/performance"),

  getPerformanceBreakdown: () =>
    requestJson<PerformanceBreakdown>("/api/performance/breakdown"),

  getPredictionStatuses: (ids: number[]) => {
    const clean = Array.from(new Set(ids.filter((x) => Number.isInteger(x) && x > 0))).slice(0, 30);
    if (clean.length === 0) return Promise.resolve({ items: [] as PredictionStatusItem[] });
    return requestJson<{ items: PredictionStatusItem[] }>(
      `/api/predictions/status?ids=${clean.join(",")}`
    );
  },

  getActiveAnnouncements: () =>
    requestJson<SiteAnnouncement[]>("/api/announcements/active"),

  getCommunityFavorites: () =>
    requestJson<{ prediction_ids: number[] }>("/api/predictions/community-favorites"),

  getPulse: (predictionId: number) =>
    requestJson<PulseData>(`/api/predictions/${predictionId}/pulse`),

  votePulse: (predictionId: number, choice: "agree" | "risky") =>
    requestJson<PulseData>(`/api/predictions/${predictionId}/pulse/vote`, {
      method: "POST",
      body: JSON.stringify({ choice }),
    }),

  recordSlipAdd: (predictionId: number) =>
    requestJson<{ ok: boolean }>(`/api/predictions/${predictionId}/pulse/slip-add`, {
      method: "POST",
    }),

  adminExportCalibrationCsv: async (secret: string): Promise<Blob> => {
    const res = await fetch(`${API_BASE}/api/admin/export/calibration.csv`, {
      headers: { "X-Cron-Secret": secret },
    });
    if (!res.ok) {
      throw new Error(`API ${res.status}: ${res.statusText}`);
    }
    return res.blob();
  },

  adminOverview: (secret: string) =>
    requestJson<AdminOverviewData>("/api/admin/overview", {
      headers: { "X-Cron-Secret": secret },
    }),

  adminCreateAnnouncement: (
    secret: string,
    payload: { text: string; link_url?: string | null; expires_hours?: number | null }
  ) =>
    requestJson<SiteAnnouncement>("/api/admin/announcements", {
      method: "POST",
      headers: { "X-Cron-Secret": secret },
      body: JSON.stringify(payload),
    }),

  adminPatchAnnouncement: (secret: string, id: number, is_active: boolean) =>
    requestJson<SiteAnnouncement>(`/api/admin/announcements/${id}`, {
      method: "PATCH",
      headers: { "X-Cron-Secret": secret },
      body: JSON.stringify({ is_active }),
    }),

  adminDeleteAnnouncement: (secret: string, id: number) =>
    requestJson<{ ok: boolean; deleted_id: number }>(`/api/admin/announcements/${id}`, {
      method: "DELETE",
      headers: { "X-Cron-Secret": secret },
    }),

  adminTelegramTest: (secret: string) =>
    requestJson<{ ok: boolean; chat_id?: string; error?: string; topics?: Record<string, unknown> }>(
      "/api/admin/telegram/test",
      {
        method: "POST",
        headers: { "X-Cron-Secret": secret },
      }
    ),

  getVapidKey: () => requestJson<{ public_key: string }>("/api/push/vapid-public-key"),

  subscribePush: (sub: { endpoint: string; p256dh: string; auth: string }) =>
    requestJson<{ status: string }>("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(sub),
    }),

  getSportotoCurrent: () =>
    requestJson<SportotoCurrentResponse>("/api/sportoto/current"),

  getSportotoHistory: () =>
    requestJson<{ items: SportotoHistoryItem[] }>("/api/sportoto/history"),

  optimizeSportoto: (payload: {
    week_id: number;
    budget: number;
    mode: "hit15" | "shield13" | "contrarian";
    locks: Record<number, string>;
  }) =>
    requestJson<SportotoPlan>("/api/sportoto/optimize", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getRecentNotifications: () =>
    requestJson<{ items: NotificationItem[] }>("/api/notifications/recent"),

  adminGetSportotoWeeks: (secret: string) =>
    requestJson<{ items: SportotoWeekData[] }>("/api/admin/sportoto/weeks", {
      headers: { "X-Cron-Secret": secret },
    }),

  adminParseSportoto: (
    secret: string,
    payload: { title: string; column_price?: number; raw_text: string }
  ) =>
    requestJson<SportotoWeekData>("/api/admin/sportoto/parse", {
      method: "POST",
      headers: { "X-Cron-Secret": secret },
      body: JSON.stringify(payload),
    }),

  adminPatchSportotoMatches: (
    secret: string,
    weekId: number,
    payload: {
      matches: {
        slot_no: number;
        odd_1?: number | null;
        odd_x?: number | null;
        odd_2?: number | null;
        public_pct_1?: number | null;
        public_pct_x?: number | null;
        public_pct_2?: number | null;
        actual_result?: string | null;
      }[];
      auto_settle_from_db?: boolean;
    }
  ) =>
    requestJson<SportotoWeekData>(`/api/admin/sportoto/weeks/${weekId}/matches`, {
      method: "PATCH",
      headers: { "X-Cron-Secret": secret },
      body: JSON.stringify(payload),
    }),

  adminPublishSportoto: (secret: string, weekId: number) =>
    requestJson<SportotoWeekData>(`/api/admin/sportoto/weeks/${weekId}/publish`, {
      method: "POST",
      headers: { "X-Cron-Secret": secret },
    }),

  adminTelegramSportoto: (secret: string, weekId: number) =>
    requestJson<{ ok: boolean; sent: boolean; reason?: string; error?: string }>(
      `/api/admin/sportoto/weeks/${weekId}/telegram`,
      {
        method: "POST",
        headers: { "X-Cron-Secret": secret },
      }
    ),

  adminDeleteSportoto: (secret: string, weekId: number) =>
    requestJson<{ ok: boolean; deleted_id: number }>(`/api/admin/sportoto/weeks/${weekId}`, {
      method: "DELETE",
      headers: { "X-Cron-Secret": secret },
    }),
};
