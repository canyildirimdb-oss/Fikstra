import assert from "node:assert/strict";
import test from "node:test";
import {
  computeAverageWinningOdd,
  computeConfidenceTierBands,
  computeStrongestMarket,
  computeWinStreaks,
  filterAndPaginateArchive,
  getArchiveStatusCounts,
  getDynamicArchiveMarkets,
  type ArchiveItem,
} from "../src/utils/performanceMetrics.ts";

function makeArchive(overrides: Partial<ArchiveItem> = {}): ArchiveItem {
  return {
    prediction_id: 1,
    match_id: 101,
    league: "İtalya Serie A",
    home_team: "Torino",
    away_team: "Lazio",
    home_score: 0,
    away_score: 2,
    market_key: "MS",
    selection_key: "2",
    iddaa_odd: 1.9,
    confidence_score: 76,
    status: "WON",
    start_date_tr: "27.11",
    settled_at: "2026-11-27T21:00:00Z",
    ...overrides,
  };
}

const SAMPLE_ARCHIVE: ArchiveItem[] = [
  makeArchive({ prediction_id: 5, status: "WON", iddaa_odd: 1.6, market_key: "AU_25", selection_key: "UST", confidence_score: 77, settled_at: "2026-11-27T22:00:00Z" }),
  makeArchive({ prediction_id: 4, status: "WON", iddaa_odd: 1.8, market_key: "AU_25", selection_key: "UST", confidence_score: 72, settled_at: "2026-11-27T20:00:00Z" }),
  makeArchive({ prediction_id: 3, status: "LOST", iddaa_odd: 2.1, market_key: "MS", selection_key: "1", confidence_score: 64, settled_at: "2026-11-27T18:00:00Z" }),
  makeArchive({ prediction_id: 2, status: "WON", iddaa_odd: 1.5, market_key: "CS", selection_key: "1X", confidence_score: 69, settled_at: "2026-11-27T16:00:00Z" }),
  makeArchive({ prediction_id: 1, status: "WON", iddaa_odd: 1.7, market_key: "AU_25", selection_key: "UST", confidence_score: 75, settled_at: "2026-11-27T14:00:00Z" }),
];

test("computeAverageWinningOdd averages only WON predictions", () => {
  // (1.6 + 1.8 + 1.5 + 1.7) / 4 = 1.65
  assert.equal(computeAverageWinningOdd(SAMPLE_ARCHIVE), 1.65);
  assert.equal(computeAverageWinningOdd([makeArchive({ status: "LOST" })]), null);
});

test("computeWinStreaks computes longest and current chronological streaks", () => {
  // Chronological order: 1(W), 2(W), 3(L), 4(W), 5(W) -> longest=2, current=2
  const res = computeWinStreaks(SAMPLE_ARCHIVE);
  assert.equal(res.longestStreak, 2);
  assert.equal(res.currentStreak, 2);

  // Add an older 3-win streak
  const withOlder = [
    ...SAMPLE_ARCHIVE,
    makeArchive({ prediction_id: 10, status: "WON", settled_at: "2026-11-26T10:00:00Z" }),
    makeArchive({ prediction_id: 11, status: "WON", settled_at: "2026-11-26T12:00:00Z" }),
    makeArchive({ prediction_id: 12, status: "WON", settled_at: "2026-11-26T14:00:00Z" }),
    makeArchive({ prediction_id: 13, status: "LOST", settled_at: "2026-11-26T16:00:00Z" }),
  ];
  const res2 = computeWinStreaks(withOlder);
  assert.equal(res2.longestStreak, 3);
  assert.equal(res2.currentStreak, 2);
});

test("computeStrongestMarket finds highest win-rate market from breakdown or archive", () => {
  const bestFromArchive = computeStrongestMarket(undefined, SAMPLE_ARCHIVE);
  assert.ok(bestFromArchive);
  assert.equal(bestFromArchive.label, "Gol Alt/Üst");
  assert.equal(bestFromArchive.winRate, 100);
  assert.equal(bestFromArchive.won, 3);
  assert.equal(bestFromArchive.total, 3);
});

test("computeConfidenceTierBands aligns 60-67 DENGELİ, 68-74 GÜÇLÜ, 75+ ELİT", () => {
  const bands = computeConfidenceTierBands(SAMPLE_ARCHIVE);
  assert.equal(bands.length, 3);
  assert.deepEqual(
    bands.map((b) => [b.label, b.rangeLabel, b.won, b.total]),
    [
      ["DENGELİ", "60–67", 0, 1],
      ["GÜÇLÜ", "68–74", 2, 2],
      ["ELİT", "75+", 2, 2],
    ]
  );
});

test("archive status counts, dynamic markets, filtering and pagination work together", () => {
  const counts = getArchiveStatusCounts(SAMPLE_ARCHIVE);
  assert.deepEqual(counts, { all: 5, won: 4, lost: 1 });

  const markets = getDynamicArchiveMarkets(SAMPLE_ARCHIVE);
  assert.deepEqual(
    markets.map((m) => m.label),
    ["Tümü", "Gol Alt/Üst", "ÇŞ", "MS"]
  );

  const page1 = filterAndPaginateArchive(SAMPLE_ARCHIVE, "WON", "Gol Alt/Üst", 2);
  assert.equal(page1.filtered.length, 3);
  assert.equal(page1.visible.length, 2);
  assert.equal(page1.hasMore, true);
  assert.equal(page1.remainingCount, 1);

  const page2 = filterAndPaginateArchive(SAMPLE_ARCHIVE, "WON", "Gol Alt/Üst", 22);
  assert.equal(page2.visible.length, 3);
  assert.equal(page2.hasMore, false);
});
