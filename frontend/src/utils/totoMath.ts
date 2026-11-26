import type {
  SportotoMatchItem,
  TotoAdvice,
  TotoProbs,
  TotoSurvival,
} from "../types";

export const VALID_TOTO_RESULTS = new Set(["1", "X", "2"]);

export function canonicalPickCode(code: string): string {
  const up = (code || "").toUpperCase();
  const ordered = ["1", "X", "2"].filter((c) => up.includes(c)).join("");
  return ordered || "1";
}

export function pickProbForCode(m: SportotoMatchItem, code: string): number {
  const p1 = m.prob_1 ?? 0;
  const px = m.prob_x ?? 0;
  const p2 = m.prob_2 ?? 0;
  let total = 0;
  if (code.includes("1")) total += p1;
  if (code.includes("X")) total += px;
  if (code.includes("2")) total += p2;
  return Math.min(1, Math.max(0, total));
}

export function exactPoissonBinomial(pickProbs: number[]): TotoProbs {
  const n = pickProbs.length;
  const dist = new Array<number>(n + 1).fill(0);
  dist[0] = 1;
  for (const rawP of pickProbs) {
    const p = Math.min(1, Math.max(0, rawP));
    const q = 1 - p;
    for (let k = n; k >= 1; k--) {
      dist[k] = dist[k] * q + dist[k - 1] * p;
    }
    dist[0] *= q;
  }
  const p15 = n >= 15 ? dist[15] : dist[n];
  let p14Plus = 0;
  let p13Plus = 0;
  let p12Plus = 0;
  for (let k = Math.min(14, n); k <= n; k++) p14Plus += dist[k];
  for (let k = Math.min(13, n); k <= n; k++) p13Plus += dist[k];
  for (let k = Math.min(12, n); k <= n; k++) p12Plus += dist[k];
  const expectedHits = pickProbs.reduce((a, b) => a + b, 0);
  return {
    p15: Number((p15 * 100).toFixed(4)),
    p14_plus: Number((p14Plus * 100).toFixed(4)),
    p13_plus: Number((p13Plus * 100).toFixed(4)),
    p12_plus: Number((p12Plus * 100).toFixed(4)),
    expected_hits: Number(expectedHits.toFixed(2)),
  };
}

export function computeLiveSurvival(
  matches: SportotoMatchItem[],
  picksBySlot: Record<number, string>
): TotoSurvival | null {
  let settledCount = 0;
  let settledHits = 0;
  const remainingProbs: number[] = [];

  for (const m of matches) {
    const code = canonicalPickCode(picksBySlot[m.slot_no] || "1");
    const actual = (m.actual_result || "").toUpperCase();
    if (VALID_TOTO_RESULTS.has(actual)) {
      settledCount += 1;
      if (code.includes(actual)) settledHits += 1;
    } else {
      remainingProbs.push(pickProbForCode(m, code));
    }
  }
  if (settledCount === 0) return null;

  const misses = settledCount - settledHits;
  const remN = remainingProbs.length;
  const dist = new Array<number>(remN + 1).fill(0);
  dist[0] = 1;
  for (const rawP of remainingProbs) {
    const p = Math.min(1, Math.max(0, rawP));
    const q = 1 - p;
    for (let k = remN; k >= 1; k--) {
      dist[k] = dist[k] * q + dist[k - 1] * p;
    }
    dist[0] *= q;
  }

  const probAtLeast = (target: number) => {
    const needed = target - settledHits;
    if (needed <= 0) return 100;
    if (needed > remN) return 0;
    let sum = 0;
    for (let k = needed; k <= remN; k++) sum += dist[k];
    return Number((sum * 100).toFixed(4));
  };

  return {
    settled_count: settledCount,
    settled_hits: settledHits,
    settled_misses: misses,
    remaining_count: remN,
    can_hit_15: misses === 0,
    can_hit_14: misses <= 1,
    can_hit_13: misses <= 2,
    can_hit_12: misses <= 3,
    p15_live: probAtLeast(15),
    p14_live: probAtLeast(14),
    p13_live: probAtLeast(13),
    p12_live: probAtLeast(12),
  };
}

function bestOptions(m: SportotoMatchItem) {
  const p1 = m.prob_1 ?? 0;
  const px = m.prob_x ?? 0;
  const p2 = m.prob_2 ?? 0;
  const singles: [string, number][] = [
    ["1", p1],
    ["X", px],
    ["2", p2],
  ];
  singles.sort((a, b) => b[1] - a[1]);
  const doubles: [string, number][] = [
    ["1X", p1 + px],
    ["12", p1 + p2],
    ["X2", px + p2],
  ];
  doubles.sort((a, b) => b[1] - a[1]);
  return {
    1: singles[0],
    2: [doubles[0][0], Math.min(1, doubles[0][1])] as [string, number],
    3: ["1X2", 1] as [string, number],
  };
}

export function computeMarginalAdvice(
  matches: SportotoMatchItem[],
  picksBySlot: Record<number, string>
): TotoAdvice {
  let bestUpgrade: TotoAdvice["best_upgrade"] = null;
  let bestUpGain = -1;
  let safestDowngrade: TotoAdvice["safest_downgrade"] = null;
  let minDownLoss = 1e9;

  for (const m of matches) {
    if (m.prob_1 === null) continue;
    const curCode = canonicalPickCode(picksBySlot[m.slot_no] || "1");
    const k = curCode.length;
    const curP = pickProbForCode(m, curCode);
    const opts = bestOptions(m);

    if (k < 3) {
      const nxtK = (k + 1) as 2 | 3;
      const [toCode, toP] = opts[nxtK];
      const gain = Math.log(Math.max(toP, 1e-12)) - Math.log(Math.max(curP, 1e-12));
      if (gain > bestUpGain) {
        bestUpGain = gain;
        bestUpgrade = {
          slot_no: m.slot_no,
          home_team: m.home_team,
          away_team: m.away_team,
          from_code: curCode,
          to_code: toCode,
          gain_prob_pct: Number(((toP - curP) * 100).toFixed(1)),
          cost_multiplier: `${nxtK}/${k}x`,
        };
      }
    }

    if (k > 1) {
      const prvK = (k - 1) as 1 | 2;
      const [toCode, toP] = opts[prvK];
      const loss = Math.log(Math.max(curP, 1e-12)) - Math.log(Math.max(toP, 1e-12));
      if (loss < minDownLoss) {
        minDownLoss = loss;
        safestDowngrade = {
          slot_no: m.slot_no,
          home_team: m.home_team,
          away_team: m.away_team,
          from_code: curCode,
          to_code: toCode,
          loss_prob_pct: Number(((curP - toP) * 100).toFixed(1)),
        };
      }
    }
  }

  return { best_upgrade: bestUpgrade, safest_downgrade: safestDowngrade };
}
