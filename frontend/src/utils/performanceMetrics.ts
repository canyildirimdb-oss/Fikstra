import type { BreakdownRow, PerformanceData } from "../types";

const BAND_BADGE_CLS = {
  DENGELI: "bg-amber-500/15 border-amber-400/35 text-amber-300",
  GUCLU: "bg-teal-500/15 border-teal-400/40 text-teal-300",
  ELIT:
    "bg-gradient-to-r from-[#00FF85]/25 via-emerald-400/20 to-amber-400/25 border-[#00FF85]/55 text-[#00FF85] shadow-[0_0_10px_-3px_rgba(0,255,133,0.55)]",
} as const;

export type ArchiveItem = PerformanceData["archive"][number];
export type ArchiveStatusFilter = "ALL" | "WON" | "LOST";

export interface StrongestMarketInfo {
  label: string;
  winRate: number;
  won: number;
  total: number;
}

export interface ConfidenceBandSummary {
  key: string;
  label: string;
  rangeLabel: string;
  total: number;
  won: number;
  winRate: number | null;
  badgeCls: string;
}

export function computeAverageWinningOdd(archive: ArchiveItem[]): number | null {
  const won = archive.filter(
    (item) => item.status === "WON" && Number.isFinite(item.iddaa_odd) && item.iddaa_odd > 0
  );
  if (won.length === 0) return null;
  const sum = won.reduce((acc, item) => acc + item.iddaa_odd, 0);
  return Number((sum / won.length).toFixed(2));
}

export function getArchiveMarketGroup(marketKey: string): string {
  if (marketKey === "AU_15" || marketKey === "AU_25" || marketKey === "AU_35") {
    return "Gol Alt/Üst";
  }
  if (marketKey === "MS") return "MS";
  if (marketKey === "CS") return "ÇŞ";
  if (marketKey === "KG") return "KG";
  return marketKey || "Diğer";
}

export function computeStrongestMarket(
  breakdownMarkets: BreakdownRow[] | undefined,
  archive: ArchiveItem[]
): StrongestMarketInfo | null {
  if (breakdownMarkets && breakdownMarkets.length > 0) {
    const valid = breakdownMarkets.filter((r) => r.decided > 0);
    if (valid.length > 0) {
      const sorted = [...valid].sort((a, b) => {
        const ra = a.win_rate ?? Math.round((a.won / a.decided) * 100);
        const rb = b.win_rate ?? Math.round((b.won / b.decided) * 100);
        if (rb !== ra) return rb - ra;
        return b.decided - a.decided;
      });
      const best = sorted[0];
      const rate = best.win_rate ?? Math.round((best.won / best.decided) * 100);
      return {
        label: best.label,
        winRate: Math.round(rate),
        won: best.won,
        total: best.decided,
      };
    }
  }

  const groups = new Map<string, { won: number; total: number }>();
  for (const item of archive) {
    if (item.status !== "WON" && item.status !== "LOST") continue;
    const grp = getArchiveMarketGroup(item.market_key);
    const cur = groups.get(grp) ?? { won: 0, total: 0 };
    cur.total += 1;
    if (item.status === "WON") cur.won += 1;
    groups.set(grp, cur);
  }
  if (groups.size === 0) return null;

  const list: StrongestMarketInfo[] = Array.from(groups.entries()).map(([label, st]) => ({
    label,
    winRate: Math.round((st.won / st.total) * 100),
    won: st.won,
    total: st.total,
  }));
  list.sort((a, b) => b.winRate - a.winRate || b.total - a.total);
  return list[0];
}

function getChronologicalKey(item: ArchiveItem): number {
  if (item.settled_at) {
    const ms = Date.parse(item.settled_at);
    if (!Number.isNaN(ms)) return ms;
  }
  return item.prediction_id;
}

export function computeWinStreaks(archive: ArchiveItem[]): {
  longestStreak: number;
  currentStreak: number;
} {
  const decided = archive.filter((it) => it.status === "WON" || it.status === "LOST");
  if (decided.length === 0) return { longestStreak: 0, currentStreak: 0 };

  const ordered = [...decided].sort((a, b) => getChronologicalKey(a) - getChronologicalKey(b));
  let longest = 0;
  let running = 0;
  for (const item of ordered) {
    if (item.status === "WON") {
      running += 1;
      if (running > longest) longest = running;
    } else {
      running = 0;
    }
  }
  return { longestStreak: longest, currentStreak: running };
}

export function computeConfidenceTierBands(
  archive: ArchiveItem[],
  fallbackBands?: PerformanceData["calibration_bands"]
): ConfidenceBandSummary[] {
  const defs = [
    {
      key: "DENGELI",
      label: "DENGELİ",
      rangeLabel: "60–67",
      min: 60,
      max: 67.9999,
      badgeCls: BAND_BADGE_CLS.DENGELI,
    },
    {
      key: "GUCLU",
      label: "GÜÇLÜ",
      rangeLabel: "68–74",
      min: 68,
      max: 74.9999,
      badgeCls: BAND_BADGE_CLS.GUCLU,
    },
    {
      key: "ELIT",
      label: "ELİT",
      rangeLabel: "75+",
      min: 75,
      max: 1000,
      badgeCls: BAND_BADGE_CLS.ELIT,
    },
  ];

  const decided = archive.filter((it) => it.status === "WON" || it.status === "LOST");
  if (decided.length > 0) {
    return defs.map((d) => {
      const rows = decided.filter(
        (it) => it.confidence_score >= d.min && it.confidence_score <= d.max
      );
      const total = rows.length;
      const won = rows.filter((it) => it.status === "WON").length;
      return {
        key: d.key,
        label: d.label,
        rangeLabel: d.rangeLabel,
        total,
        won,
        winRate: total > 0 ? Math.round((won / total) * 100) : null,
        badgeCls: d.badgeCls,
      };
    });
  }

  if (fallbackBands && fallbackBands.length > 0) {
    return fallbackBands.map((b, idx) => ({
      key: b.band,
      label: idx === 0 ? "DENGELİ" : idx === 1 ? "GÜÇLÜ" : "ELİT",
      rangeLabel: b.band,
      total: b.total,
      won: b.won,
      winRate: b.win_rate,
      badgeCls: defs[Math.min(idx, 2)].badgeCls,
    }));
  }

  return defs.map((d) => ({
    key: d.key,
    label: d.label,
    rangeLabel: d.rangeLabel,
    total: 0,
    won: 0,
    winRate: null,
    badgeCls: d.badgeCls,
  }));
}

export function getArchiveStatusCounts(archive: ArchiveItem[]): {
  all: number;
  won: number;
  lost: number;
} {
  let won = 0;
  let lost = 0;
  for (const item of archive) {
    if (item.status === "WON") won += 1;
    else if (item.status === "LOST") lost += 1;
  }
  return { all: archive.length, won, lost };
}

export function getDynamicArchiveMarkets(
  archive: ArchiveItem[]
): { key: string; label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const item of archive) {
    const grp = getArchiveMarketGroup(item.market_key);
    counts.set(grp, (counts.get(grp) ?? 0) + 1);
  }
  const markets = Array.from(counts.entries())
    .map(([key, count]) => ({ key, label: key, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "tr-TR"));
  return [{ key: "ALL", label: "Tümü", count: archive.length }, ...markets];
}

export function filterAndPaginateArchive(
  archive: ArchiveItem[],
  statusFilter: ArchiveStatusFilter,
  marketFilter: string,
  visibleLimit: number
): {
  filtered: ArchiveItem[];
  visible: ArchiveItem[];
  hasMore: boolean;
  remainingCount: number;
} {
  const filtered = archive.filter((item) => {
    if (statusFilter !== "ALL" && item.status !== statusFilter) return false;
    if (marketFilter !== "ALL" && getArchiveMarketGroup(item.market_key) !== marketFilter) {
      return false;
    }
    return true;
  });
  const limit = Math.max(1, visibleLimit);
  const visible = filtered.slice(0, limit);
  const remainingCount = Math.max(0, filtered.length - visible.length);
  return {
    filtered,
    visible,
    hasMore: remainingCount > 0,
    remainingCount,
  };
}
