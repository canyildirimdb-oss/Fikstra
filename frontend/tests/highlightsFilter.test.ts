import assert from "node:assert/strict";
import test from "node:test";
import type { PredictionItem } from "../types";
import {
  DEFAULT_HIGHLIGHTS_FILTER,
  filterAndSortHighlights,
  formatHeroKickoffLabel,
  formatSyncTimeTr,
  getConfidenceTier,
  getDynamicLeagues,
  getDynamicMarketChips,
  resolveLeagueVisual,
} from "../src/utils/highlightsFilter.ts";

function makePred(overrides: Partial<PredictionItem> = {}): PredictionItem {
  return {
    prediction_id: 1,
    match_id: 101,
    iddaa_id: "101",
    league: "İtalya Serie A",
    country: "IT",
    is_major_league: true,
    home_team: "Torino",
    away_team: "Lazio",
    home_logo_url: null,
    away_logo_url: null,
    league_logo_url: null,
    start_time_utc: "2026-11-28T17:00:00Z",
    start_time_tr: "20:00",
    start_date_tr: "28.11",
    is_today_24h: true,
    data_status: "ok",
    market_key: "MS",
    selection_key: "2",
    iddaa_odd: 1.92,
    opening_odd: 2.0,
    global_odd: 1.95,
    confidence_score: 76,
    value_edge_pct: 4.2,
    odds_drop_pct: 4.0,
    breakdown: {
      value: 25,
      odds_move: 20,
      form_goals: 18,
      injury: 13,
      true_prob_pct: 52,
      blended_prob_pct: 54,
    },
    home_form: ["W", "D", "L", "W", "L"],
    away_form: ["W", "W", "D", "W", "W"],
    injuries_count: 1,
    ...overrides,
  };
}

const SAMPLE_POOL: PredictionItem[] = [
  makePred({
    prediction_id: 1,
    match_id: 101,
    league: "İtalya Serie A",
    home_team: "Torino",
    away_team: "Lazio",
    market_key: "MS",
    selection_key: "2",
    iddaa_odd: 1.92,
    confidence_score: 76,
    start_date_tr: "28.11",
    start_time_tr: "20:00",
    start_time_utc: "2026-11-28T17:00:00Z",
  }),
  makePred({
    prediction_id: 2,
    match_id: 102,
    league: "İspanya La Liga",
    country: "ES",
    home_team: "Deportivo La Coruna",
    away_team: "Barcelona",
    market_key: "AU_25",
    selection_key: "UST",
    iddaa_odd: 1.55,
    confidence_score: 71,
    start_date_tr: "29.11",
    start_time_tr: "18:15",
    start_time_utc: "2026-11-29T15:15:00Z",
    is_today_24h: false,
  }),
  makePred({
    prediction_id: 3,
    match_id: 103,
    league: "İngiltere Premier Lig",
    country: "GB",
    home_team: "QPR",
    away_team: "Leeds",
    market_key: "CS",
    selection_key: "1X",
    iddaa_odd: 1.44,
    confidence_score: 64,
    start_date_tr: "28.11",
    start_time_tr: "15:30",
    start_time_utc: "2026-11-28T12:30:00Z",
  }),
];

test("unfiltered state shows hero and removes only hero from grid (no duplicate)", () => {
  const res = filterAndSortHighlights(SAMPLE_POOL, DEFAULT_HIGHLIGHTS_FILTER, "28.11");
  assert.equal(res.hasActiveFilter, false);
  assert.equal(res.heroItem?.prediction_id, 1);
  assert.equal(res.gridItems.length, 2);
  assert.equal(
    res.gridItems.some((x) => x.prediction_id === res.heroItem?.prediction_id),
    false
  );
  assert.equal(formatHeroKickoffLabel(res.heroItem!, "28.11"), "Bugün · 20:00");
  assert.equal(formatHeroKickoffLabel(SAMPLE_POOL[1], "28.11"), "29.11 · 18:15");
});

test("single-letter search hides hero and renders single matching match in grid (never empty)", () => {
  const res = filterAndSortHighlights(
    SAMPLE_POOL,
    { ...DEFAULT_HIGHLIGHTS_FILTER, search: "q" },
    "28.11"
  );
  assert.equal(res.hasActiveFilter, true);
  assert.equal(res.heroItem, null);
  assert.equal(res.totalMatched, 1);
  assert.equal(res.gridItems.length, 1);
  assert.equal(res.gridItems[0].home_team, "QPR");
});

test("confidence tiers map 75+ -> ELİT, 68-74 -> GÜÇLÜ, 60-67 -> DENGELİ", () => {
  assert.equal(getConfidenceTier(75).label, "ELİT");
  assert.equal(getConfidenceTier(82).label, "ELİT");
  assert.equal(getConfidenceTier(74).label, "GÜÇLÜ");
  assert.equal(getConfidenceTier(68).label, "GÜÇLÜ");
  assert.equal(getConfidenceTier(67).label, "DENGELİ");
  assert.equal(getConfidenceTier(60).label, "DENGELİ");
});

test("dynamic market chips omit 0-count markets and dynamic leagues sort by count", () => {
  const chips = getDynamicMarketChips(SAMPLE_POOL);
  const keys = chips.map((c) => c.key);
  assert.deepEqual(keys, ["ALL", "MS", "AU_25_UST", "CS"]);
  assert.equal(keys.includes("KG_VAR"), false);

  const leagues = getDynamicLeagues(SAMPLE_POOL);
  assert.equal(leagues.length, 3);
  assert.equal(leagues[0].count, 1);
});

test("todayOnly + market + league + sort combine cleanly without conflict", () => {
  const res = filterAndSortHighlights(
    SAMPLE_POOL,
    {
      search: "",
      market: "ALL",
      league: "ALL",
      todayOnly: true,
      sort: "time",
    },
    "28.11"
  );
  assert.equal(res.heroItem, null);
  assert.equal(res.gridItems.length, 2);
  assert.equal(res.gridItems[0].prediction_id, 3); // 15:30 before 20:00
  assert.equal(res.gridItems[1].prediction_id, 1);
});

test("league visual resolves major FotMob logo and deterministic shield fallback", () => {
  const pl = resolveLeagueVisual("İngiltere Premier Lig", "GB", null);
  assert.equal(pl.logoUrl, "https://images.fotmob.com/image_resources/logo/leaguelogo/47.png");
  assert.equal(pl.abbr, "PL");

  const unknown = resolveLeagueVisual("Bölgesel Amatör Lig", "TR", null);
  assert.equal(unknown.logoUrl, null);
  assert.equal(unknown.abbr, "AL");
  assert.ok(unknown.colorCls.length > 0);
});

test("formatSyncTimeTr formats UTC ISO string in Europe/Istanbul HH:MM", () => {
  assert.equal(formatSyncTimeTr("2026-11-28T00:17:55+00:00"), "03:17");
  assert.equal(formatSyncTimeTr(null), null);
});
