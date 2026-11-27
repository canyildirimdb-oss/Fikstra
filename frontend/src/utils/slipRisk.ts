import type { CustomSlipItem } from "../types";

export interface SlipRiskAnalysis {
  combinedPct: number;
  riskLabel: "Düşük Risk" | "Orta Risk" | "Yüksek Risk";
  riskTone: "emerald" | "amber" | "rose";
  weakestItem: CustomSlipItem | null;
  weakestPct: number | null;
  withoutWeakestPct: number | null;
}

export function getLegProbability(item: CustomSlipItem): number {
  if (
    item.model_prob != null &&
    Number.isFinite(item.model_prob) &&
    item.model_prob > 0 &&
    item.model_prob < 100
  ) {
    return Math.max(0.02, Math.min(0.96, item.model_prob / 100));
  }
  const odd = item.iddaa_odd > 1.01 ? item.iddaa_odd : 1.5;
  return Math.max(0.05, Math.min(0.92, 0.94 / odd));
}

export function analyzeSlipRisk(items: CustomSlipItem[]): SlipRiskAnalysis {
  if (items.length === 0) {
    return {
      combinedPct: 0,
      riskLabel: "Düşük Risk",
      riskTone: "emerald",
      weakestItem: null,
      weakestPct: null,
      withoutWeakestPct: null,
    };
  }

  const probs = items.map((it) => ({ item: it, prob: getLegProbability(it) }));
  const combined = probs.reduce((acc, x) => acc * x.prob, 1);
  const combinedPct = Math.round(combined * 100);

  let riskLabel: SlipRiskAnalysis["riskLabel"] = "Yüksek Risk";
  let riskTone: SlipRiskAnalysis["riskTone"] = "rose";
  if (combined >= 0.4) {
    riskLabel = "Düşük Risk";
    riskTone = "emerald";
  } else if (combined >= 0.2) {
    riskLabel = "Orta Risk";
    riskTone = "amber";
  }

  let weakestItem: CustomSlipItem | null = null;
  let weakestPct: number | null = null;
  let withoutWeakestPct: number | null = null;

  if (probs.length >= 2) {
    const weakest = probs.reduce((min, cur) => (cur.prob < min.prob ? cur : min), probs[0]);
    weakestItem = weakest.item;
    weakestPct = Math.round(weakest.prob * 100);
    const withoutWeakest = weakest.prob > 0 ? Math.min(0.99, combined / weakest.prob) : combined;
    withoutWeakestPct = Math.round(withoutWeakest * 100);
  }

  return {
    combinedPct,
    riskLabel,
    riskTone,
    weakestItem,
    weakestPct,
    withoutWeakestPct,
  };
}
