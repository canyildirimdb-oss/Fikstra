import type { PredictionItem } from "../types";

export type MarketFilterKey = "ALL" | "MS" | "AU_25_UST" | "AU_35_ALT" | "KG_VAR" | "CS";
export type SortKey = "confidence" | "time" | "odd";
export type ConfidenceTierKey = "ELIT" | "GUCLU" | "DENGELI";

export interface ConfidenceTierInfo {
  key: ConfidenceTierKey;
  label: string;
  stroke: string;
  textCls: string;
  badgeCls: string;
  glow?: string;
}

export interface HighlightsFilterState {
  search: string;
  market: MarketFilterKey;
  league: string;
  todayOnly: boolean;
  sort: SortKey;
}

export interface MarketChipOption {
  key: MarketFilterKey;
  label: string;
  count: number;
}

export interface LeagueOption {
  league: string;
  count: number;
  country?: string;
  logoUrl?: string | null;
}

export interface MarketRadarEntry {
  item: PredictionItem;
  signalLabel: "Oran Düşüşü" | "Global Değer";
  signalMetric: string;
  score: number;
}

export const DEFAULT_HIGHLIGHTS_FILTER: HighlightsFilterState = {
  search: "",
  market: "ALL",
  league: "ALL",
  todayOnly: false,
  sort: "confidence",
};

export function getTodayTrDdMm(now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "2-digit",
      timeZone: "Europe/Istanbul",
    }).formatToParts(now);
    const day = parts.find((p) => p.type === "day")?.value ?? "";
    const month = parts.find((p) => p.type === "month")?.value ?? "";
    if (day && month) return `${day}.${month}`;
  } catch {
    // fallback below
  }
  return "";
}

export function formatSyncTimeTr(isoUtc: string | null | undefined): string | null {
  if (!isoUtc) return null;
  const ms = Date.parse(isoUtc);
  if (Number.isNaN(ms)) return null;
  try {
    return new Intl.DateTimeFormat("tr-TR", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Europe/Istanbul",
    }).format(new Date(ms));
  } catch {
    return null;
  }
}

export function getEffectiveModelProb(item: PredictionItem): number {
  const raw = item.breakdown?.blended_prob_pct ?? item.breakdown?.true_prob_pct ?? 0;
  return Number.isFinite(raw) ? raw : 0;
}

export function getConfidenceTier(score: number): ConfidenceTierInfo {
  if (score >= 75) {
    return {
      key: "ELIT",
      label: "ELİT TERCİH",
      stroke: "#00FF85",
      textCls: "text-[#00FF85]",
      badgeCls:
        "bg-gradient-to-r from-[#00FF85]/25 via-emerald-400/20 to-amber-400/25 border-[#00FF85]/55 text-[#00FF85] shadow-[0_0_10px_-3px_rgba(0,255,133,0.55)]",
      glow: "drop-shadow(0 0 10px rgba(0, 255, 133, 0.45))",
    };
  }
  if (score >= 68) {
    return {
      key: "GUCLU",
      label: "GÜÇLÜ TERCİH",
      stroke: "#10B981",
      textCls: "text-emerald-400",
      badgeCls: "bg-teal-500/15 border-teal-400/40 text-teal-300",
      glow: "drop-shadow(0 0 6px rgba(16, 185, 129, 0.25))",
    };
  }
  return {
    key: "DENGELI",
    label: "DENGELİ TERCİH",
    stroke: "#F59E0B",
    textCls: "text-amber-300",
    badgeCls: "bg-amber-500/15 border-amber-400/35 text-amber-300",
  };
}

export function hasActiveHighlightsFilter(state: HighlightsFilterState): boolean {
  return (
    state.search.trim() !== "" ||
    state.market !== "ALL" ||
    state.league !== "ALL" ||
    state.todayOnly ||
    state.sort !== "confidence"
  );
}

export function selectHeroItem(allItems: PredictionItem[]): PredictionItem | null {
  if (allItems.length === 0) return null;
  return allItems.reduce((best, cur) => {
    if (cur.confidence_score !== best.confidence_score) {
      return cur.confidence_score > best.confidence_score ? cur : best;
    }
    const curProb = getEffectiveModelProb(cur);
    const bestProb = getEffectiveModelProb(best);
    if (curProb !== bestProb) {
      return curProb > bestProb ? cur : best;
    }
    return Date.parse(cur.start_time_utc) < Date.parse(best.start_time_utc) ? cur : best;
  }, allItems[0]);
}

export function matchesMarketFilter(item: PredictionItem, market: MarketFilterKey): boolean {
  switch (market) {
    case "ALL":
      return true;
    case "MS":
      return item.market_key === "MS";
    case "AU_25_UST":
      return item.market_key === "AU_25" && item.selection_key === "UST";
    case "AU_35_ALT":
      return item.market_key === "AU_35" && item.selection_key === "ALT";
    case "KG_VAR":
      return item.market_key === "KG" && item.selection_key === "VAR";
    case "CS":
      return item.market_key === "CS";
    default:
      return true;
  }
}

export function isItemTodayTr(item: PredictionItem, todayDdMmTr?: string): boolean {
  const target = todayDdMmTr || getTodayTrDdMm();
  if (target && item.start_date_tr) {
    return item.start_date_tr.trim() === target;
  }
  return Boolean(item.is_today_24h);
}

export function formatHeroKickoffLabel(
  item: Pick<PredictionItem, "start_date_tr" | "start_time_tr">,
  todayDdMmTr?: string
): string {
  const target = todayDdMmTr || getTodayTrDdMm();
  if (target && item.start_date_tr?.trim() === target) {
    return `Bugün · ${item.start_time_tr}`;
  }
  return `${item.start_date_tr} · ${item.start_time_tr}`;
}

export function getDynamicMarketChips(allItems: PredictionItem[]): MarketChipOption[] {
  const defs: { key: MarketFilterKey; label: string }[] = [
    { key: "ALL", label: "Tümü" },
    { key: "MS", label: "MS" },
    { key: "AU_25_UST", label: "2.5 Üst" },
    { key: "AU_35_ALT", label: "3.5 Alt" },
    { key: "KG_VAR", label: "KG Var" },
    { key: "CS", label: "ÇŞ" },
  ];
  const out: MarketChipOption[] = [];
  for (const d of defs) {
    const count =
      d.key === "ALL"
        ? allItems.length
        : allItems.filter((item) => matchesMarketFilter(item, d.key)).length;
    if (d.key === "ALL" || count > 0) {
      out.push({ key: d.key, label: d.label, count });
    }
  }
  return out;
}

export function getDynamicLeagues(allItems: PredictionItem[]): LeagueOption[] {
  const map = new Map<string, LeagueOption>();
  for (const item of allItems) {
    const lg = (item.league || "").trim();
    if (!lg) continue;
    const prev = map.get(lg);
    if (prev) {
      prev.count += 1;
    } else {
      map.set(lg, {
        league: lg,
        count: 1,
        country: item.country,
        logoUrl: item.league_logo_url,
      });
    }
  }
  return Array.from(map.values()).sort(
    (a, b) => b.count - a.count || a.league.localeCompare(b.league, "tr-TR")
  );
}

function normalizeSearch(value: string): string {
  return value
    .toLocaleLowerCase("tr-TR")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

export function filterAndSortHighlights(
  allItems: PredictionItem[],
  state: HighlightsFilterState,
  todayDdMmTr?: string
): {
  heroItem: PredictionItem | null;
  gridItems: PredictionItem[];
  totalMatched: number;
  hasActiveFilter: boolean;
} {
  const hasActiveFilter = hasActiveHighlightsFilter(state);
  // Hero is ALWAYS fixed from the unfiltered main pool
  const heroItem = selectHeroItem(allItems);
  const queryNorm = normalizeSearch(state.search);

  const filtered = allItems.filter((item) => {
    if (state.todayOnly && !isItemTodayTr(item, todayDdMmTr)) return false;
    if (state.market !== "ALL" && !matchesMarketFilter(item, state.market)) return false;
    if (state.league !== "ALL" && item.league !== state.league) return false;
    if (queryNorm) {
      const haystack = normalizeSearch(
        `${item.home_team} ${item.away_team} ${item.league} ${item.country || ""}`
      );
      if (!haystack.includes(queryNorm)) return false;
    }
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (state.sort === "time") {
      const ta = Date.parse(a.start_time_utc);
      const tb = Date.parse(b.start_time_utc);
      if (ta !== tb) return ta - tb;
      return b.confidence_score - a.confidence_score;
    }
    if (state.sort === "odd") {
      if (b.iddaa_odd !== a.iddaa_odd) return b.iddaa_odd - a.iddaa_odd;
      return b.confidence_score - a.confidence_score;
    }
    if (b.confidence_score !== a.confidence_score) {
      return b.confidence_score - a.confidence_score;
    }
    return getEffectiveModelProb(b) - getEffectiveModelProb(a);
  });

  const totalMatched = sorted.length;
  // When any filter/search is active, show ALL matching items in the grid (never drop Hero);
  // when completely unfiltered, omit only the Hero item from the grid to avoid duplication.
  const gridItems =
    !hasActiveFilter && heroItem
      ? sorted.filter(
          (item) =>
            item.prediction_id !== heroItem.prediction_id && item.match_id !== heroItem.match_id
        )
      : sorted;

  return {
    heroItem,
    gridItems,
    totalMatched,
    hasActiveFilter,
  };
}

export function getUpcomingSoonItems(
  allItems: PredictionItem[],
  limit = 5,
  nowMs: number = Date.now()
): PredictionItem[] {
  const future = allItems.filter((it) => {
    const ms = Date.parse(it.start_time_utc);
    return Number.isNaN(ms) || ms >= nowMs - 5 * 60_000;
  });
  const pool = future.length > 0 ? future : allItems;
  return [...pool]
    .sort((a, b) => {
      const ta = Date.parse(a.start_time_utc);
      const tb = Date.parse(b.start_time_utc);
      if (ta !== tb) return ta - tb;
      return b.confidence_score - a.confidence_score;
    })
    .slice(0, limit);
}

export function getMarketRadarItems(
  allItems: PredictionItem[],
  limit = 4
): MarketRadarEntry[] {
  const entries: MarketRadarEntry[] = [];
  for (const item of allItems) {
    const dropPct =
      item.odds_drop_pct != null && item.odds_drop_pct > 0
        ? item.odds_drop_pct
        : item.opening_odd > item.iddaa_odd && item.opening_odd > 0
        ? ((item.opening_odd - item.iddaa_odd) / item.opening_odd) * 100
        : 0;
    const edgePct = item.value_edge_pct != null ? item.value_edge_pct : 0;
    const moveRatio = (item.breakdown?.odds_move ?? 0) / 25;
    const valRatio = (item.breakdown?.value ?? 0) / 35;

    if (dropPct >= 2 || (moveRatio >= valRatio && (item.breakdown?.odds_move ?? 0) >= 12)) {
      const metric =
        dropPct >= 1
          ? `-%${dropPct.toFixed(0)} (${item.opening_odd.toFixed(2)}→${item.iddaa_odd.toFixed(2)})`
          : `${item.opening_odd.toFixed(2)}→${item.iddaa_odd.toFixed(2)}`;
      entries.push({
        item,
        signalLabel: "Oran Düşüşü",
        signalMetric: metric,
        score: dropPct * 2 + moveRatio * 10,
      });
    } else if (edgePct >= 1 || (item.breakdown?.value ?? 0) >= 18) {
      const metric =
        edgePct >= 1
          ? `+%${edgePct.toFixed(1)} Değer`
          : `Global ${item.global_odd ? item.global_odd.toFixed(2) : item.iddaa_odd.toFixed(2)}`;
      entries.push({
        item,
        signalLabel: "Global Değer",
        signalMetric: metric,
        score: edgePct * 2 + valRatio * 10,
      });
    }
  }
  return entries.sort((a, b) => b.score - a.score).slice(0, limit);
}

const FOTMOB_LEAGUE_IDS: { pattern: RegExp; id: number; abbr: string }[] = [
  { pattern: /şampiyonlar ligi|champions league/i, id: 42, abbr: "UCL" },
  { pattern: /avrupa ligi|europa league/i, id: 73, abbr: "UEL" },
  { pattern: /konferans ligi|conference league/i, id: 10216, abbr: "UECL" },
  { pattern: /türkiye süper lig|süper lig|super lig/i, id: 71, abbr: "SL" },
  { pattern: /ingiltere premier|premier lig|premier league/i, id: 47, abbr: "PL" },
  { pattern: /championship/i, id: 48, abbr: "CH" },
  { pattern: /la liga 2|laliga 2|segunda/i, id: 140, abbr: "LL2" },
  { pattern: /la liga|laliga/i, id: 87, abbr: "LL" },
  { pattern: /serie b/i, id: 86, abbr: "SB" },
  { pattern: /brezilya serie a|brazil.*serie a/i, id: 268, abbr: "BR" },
  { pattern: /serie a/i, id: 55, abbr: "SA" },
  { pattern: /2\.?\s*bundesliga/i, id: 146, abbr: "BL2" },
  { pattern: /avusturya bundesliga|austria/i, id: 38, abbr: "AUT" },
  { pattern: /bundesliga/i, id: 54, abbr: "BL" },
  { pattern: /ligue 2/i, id: 110, abbr: "L2" },
  { pattern: /ligue 1/i, id: 53, abbr: "L1" },
  { pattern: /eredivisie/i, id: 57, abbr: "ER" },
  { pattern: /portekiz|primeira/i, id: 61, abbr: "PT" },
  { pattern: /belçika|pro league|jupiler/i, id: 40, abbr: "BE" },
  { pattern: /iskoçya|premiership/i, id: 64, abbr: "SCO" },
  { pattern: /isviçre|swiss/i, id: 69, abbr: "CH" },
  { pattern: /yunanistan|greece/i, id: 135, abbr: "GR" },
  { pattern: /danimarka|superliga/i, id: 46, abbr: "DK" },
  { pattern: /norveç|eliteserien/i, id: 59, abbr: "NO" },
  { pattern: /isveç|allsvenskan/i, id: 67, abbr: "SE" },
  { pattern: /\bmls\b|major league soccer/i, id: 130, abbr: "MLS" },
  { pattern: /arjantin|liga profesional/i, id: 112, abbr: "ARG" },
  { pattern: /meksika|liga mx/i, id: 230, abbr: "MX" },
  { pattern: /japonya|j1|j-lig/i, id: 223, abbr: "J1" },
];

const SHIELD_PALETTES = [
  "from-emerald-500/25 to-teal-500/15 border-emerald-400/35 text-emerald-200",
  "from-cyan-500/25 to-sky-500/15 border-cyan-400/35 text-cyan-200",
  "from-amber-500/25 to-orange-500/15 border-amber-400/35 text-amber-200",
  "from-indigo-500/25 to-violet-500/15 border-indigo-400/35 text-indigo-200",
  "from-rose-500/25 to-pink-500/15 border-rose-400/35 text-rose-200",
];

function computeLeagueAbbr(league: string): string {
  const words = league
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "LG";
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  const tail = words.slice(1).map((w) => w[0]).join("");
  return (tail || words[0].slice(0, 2)).slice(0, 3).toUpperCase();
}

export function resolveLeagueVisual(
  league: string,
  country?: string | null,
  backendLogoUrl?: string | null
): { logoUrl: string | null; abbr: string; colorCls: string } {
  const cleanLeague = (league || "").trim();
  const keyForHash = `${country || ""}|${cleanLeague}`;
  let hash = 0;
  for (let i = 0; i < keyForHash.length; i++) {
    hash = keyForHash.charCodeAt(i) + ((hash << 5) - hash);
  }
  const colorCls = SHIELD_PALETTES[Math.abs(hash) % SHIELD_PALETTES.length];

  const matched = FOTMOB_LEAGUE_IDS.find((entry) => entry.pattern.test(cleanLeague));
  const logoUrl =
    backendLogoUrl ||
    (matched
      ? `https://images.fotmob.com/image_resources/logo/leaguelogo/${matched.id}.png`
      : null);
  const abbr = matched?.abbr ?? computeLeagueAbbr(cleanLeague);
  return { logoUrl, abbr, colorCls };
}

export type BackedTeamSide = "HOME" | "AWAY" | "NEUTRAL";

export function getBackedTeamSide(marketKey: string, selectionKey: string): BackedTeamSide {
  const m = (marketKey || "").toUpperCase();
  const s = (selectionKey || "").toUpperCase();
  if (m === "MS" || m === "IY_MS") {
    if (s === "1") return "HOME";
    if (s === "2") return "AWAY";
    return "NEUTRAL";
  }
  if (m === "CS") {
    if (s === "1X") return "HOME";
    if (s === "X2") return "AWAY";
    return "NEUTRAL";
  }
  return "NEUTRAL";
}
