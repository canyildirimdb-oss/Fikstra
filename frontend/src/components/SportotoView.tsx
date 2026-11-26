import { useEffect, useState } from "react";
import { Check, Copy, Grid3X3, Info } from "lucide-react";
import { api } from "../api/client";
import type { SportotoCurrentResponse, SportotoHistoryItem } from "../types";

type BudgetKey = "16" | "32" | "64";

const BUDGET_TABS: { key: BudgetKey; label: string; short: string }[] = [
  { key: "16", label: "Ekonomik (16 Kolon)", short: "16 Kolon" },
  { key: "32", label: "Dengeli (32 Kolon)", short: "32 Kolon" },
  { key: "64", label: "Geniş (64 Kolon)", short: "64 Kolon" },
];

export function SportotoView({ refreshTrigger }: { refreshTrigger: number }) {
  const [data, setData] = useState<SportotoCurrentResponse | null>(null);
  const [history, setHistory] = useState<SportotoHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [budget, setBudget] = useState<BudgetKey>("32");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([api.getSportotoCurrent(), api.getSportotoHistory()])
      .then(([cur, hist]) => {
        if (cancelled) return;
        setData(cur);
        setHistory(hist.items || []);
      })
      .catch(() => {
        if (!cancelled) {
          setData({ active: false, week: null });
          setHistory([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]);

  if (loading) {
    return (
      <div className="space-y-3">
        <div className="h-24 rounded-2xl glass-card animate-pulse" />
        <div className="h-64 rounded-2xl glass-card animate-pulse" />
      </div>
    );
  }

  const week = data?.active ? data.week : null;
  const plan = week?.plans?.[budget] || null;

  const handleCopyText = () => {
    if (!week || !plan) return;
    const pickMap = new Map(plan.picks.map((p) => [p.slot_no, p]));
    const lines = [
      `FİKSTRA SPOR TOTO 15'Lİ — ${week.title} (${plan.used_columns} Kolon)`,
      "",
      ...week.matches.map((m) => {
        const pk = pickMap.get(m.slot_no)?.pick_code || "-";
        return `${String(m.slot_no).padStart(2, "0")}. ${m.home_team} - ${m.away_team} -> ${pk}`;
      }),
      "",
      `Dağılım: ${plan.single_count} Tek / ${plan.double_count} Çifte / ${plan.triple_count} Kapalı`,
      `Beklenen İsabet: ${plan.expected_hits.toFixed(1)} / 15`,
    ];
    navigator.clipboard?.writeText(lines.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 2200);
  };

  return (
    <div className="space-y-4">
      {!week || !plan ? (
        <div className="p-7 sm:p-9 text-center rounded-2xl glass-card space-y-2.5">
          <Grid3X3 className="w-8 h-8 text-[#00FF85] mx-auto" />
          <h3 className="text-sm font-bold text-slate-100">
            Spor Toto 15&apos;li Henüz Yayınlanmadı
          </h3>
          <p className="text-xs text-slate-400 max-w-md mx-auto leading-relaxed">
            Bu haftanın Spor Toto 15&apos;lisi henüz yayınlanmadı. Haftalık liste açıldığında
            matematiksel 16 / 32 / 64 kolon şablonları burada yer alır.
          </p>
        </div>
      ) : (
        <div className="space-y-3.5">
          {/* Header & Budget Selector */}
          <div className="p-4 rounded-2xl glass-card space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-[#00FF85]/15 text-[#00FF85] border border-[#00FF85]/35">
                    SPOR TOTO 15&apos;Lİ
                  </span>
                  <h2 className="text-sm sm:text-base font-extrabold text-white">{week.title}</h2>
                </div>
                <p className="text-[11px] text-slate-400 mt-0.5 flex items-center gap-1">
                  <Info className="w-3 h-3 text-slate-500 shrink-0" />
                  <span>Dinamik programlama ile bütçe içi maksimum isabet olasılığı</span>
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                {BUDGET_TABS.map((b) => (
                  <button
                    key={b.key}
                    type="button"
                    onClick={() => setBudget(b.key)}
                    className={`px-3 py-1.5 rounded-xl text-[11px] font-bold transition-all ${
                      budget === b.key
                        ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_16px_-4px_rgba(0,255,133,0.7)]"
                        : "bg-white/[0.04] text-slate-300 border border-white/10 hover:text-white"
                    }`}
                  >
                    <span className="hidden sm:inline">{b.label}</span>
                    <span className="sm:hidden">{b.short}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Summary Strip */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
              <div className="px-3 py-2 rounded-xl bg-[#05080D]/75 border border-white/10">
                <p className="text-[10px] text-slate-400">Kullanılan Kolon</p>
                <p className="text-sm font-extrabold text-white mono-nums">
                  {plan.used_columns}{" "}
                  <span className="text-[10px] font-normal text-slate-500">/ {plan.budget}</span>
                </p>
              </div>
              <div className="px-3 py-2 rounded-xl bg-[#05080D]/75 border border-white/10">
                <p className="text-[10px] text-slate-400">Dağılım</p>
                <p className="text-xs font-bold text-slate-200 mono-nums mt-0.5">
                  {plan.single_count}T · {plan.double_count}Ç · {plan.triple_count}K
                </p>
              </div>
              <div className="px-3 py-2 rounded-xl bg-[#05080D]/75 border border-white/10">
                <p className="text-[10px] text-slate-400">Beklenen İsabet</p>
                <p className="text-sm font-extrabold text-[#00FF85] mono-nums">
                  {plan.expected_hits.toFixed(1)} <span className="text-[10px] text-slate-400">/ 15</span>
                </p>
              </div>
              <div className="px-3 py-2 rounded-xl bg-[#05080D]/75 border border-white/10">
                <p className="text-[10px] text-slate-400">
                  {plan.settled_count > 0 ? "Gerçekleşen" : "15/15 Olasılık"}
                </p>
                {plan.settled_count > 0 && plan.hits !== null ? (
                  <p className="text-sm font-extrabold text-cyan-300 mono-nums">
                    {plan.hits}/{plan.settled_count} tuttu
                  </p>
                ) : (
                  <p className="text-sm font-extrabold text-slate-200 mono-nums">
                    %{plan.full_hit_prob_pct.toFixed(3)}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* 15 Match Rows */}
          <div className="rounded-2xl glass-card divide-y divide-white/[0.06] overflow-hidden">
            {week.matches.map((m) => {
              const pick = plan.picks.find((p) => p.slot_no === m.slot_no);
              const p1 = Math.round((m.prob_1 || 0) * 100);
              const px = Math.round((m.prob_x || 0) * 100);
              const p2 = Math.max(0, 100 - p1 - px);
              const hit = pick?.hit;
              return (
                <div
                  key={m.slot_no}
                  className="px-3 sm:px-4 py-2.5 flex items-center justify-between gap-2.5 hover:bg-white/[0.02]"
                >
                  <div className="flex items-center gap-2.5 min-w-0 flex-1">
                    <span className="w-6 h-6 rounded-lg bg-white/[0.06] border border-white/10 flex items-center justify-center text-[11px] font-bold text-slate-300 mono-nums shrink-0">
                      {m.slot_no}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <p className="text-xs sm:text-sm font-semibold text-slate-100 truncate">
                          {m.home_team} - {m.away_team}
                        </p>
                        <span
                          className={`shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                            m.source === "fikstra_model"
                              ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/25"
                              : "bg-cyan-500/10 text-cyan-300 border-cyan-500/25"
                          }`}
                        >
                          {m.source === "fikstra_model" ? "Model" : "Piyasa"}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <div className="flex-1 max-w-[160px] h-1.5 rounded-full overflow-hidden bg-slate-800 flex">
                          <div className="bg-[#00FF85]" style={{ width: `${p1}%` }} />
                          <div className="bg-amber-400" style={{ width: `${px}%` }} />
                          <div className="bg-cyan-400" style={{ width: `${p2}%` }} />
                        </div>
                        <span className="text-[10px] text-slate-400 mono-nums">
                          {p1}/{px}/{p2}%
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {m.actual_result && (
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-extrabold mono-nums ${
                          hit
                            ? "bg-emerald-500/20 text-[#00FF85]"
                            : "bg-rose-500/20 text-rose-300"
                        }`}
                      >
                        MS:{m.actual_result} {hit ? "✓" : "✕"}
                      </span>
                    )}
                    <span
                      className={`min-w-[44px] text-center px-2.5 py-1 rounded-xl text-xs font-extrabold mono-nums border ${
                        pick?.kind === "triple"
                          ? "bg-amber-500/20 text-amber-300 border-amber-500/40"
                          : pick?.kind === "double"
                          ? "bg-cyan-500/15 text-cyan-300 border-cyan-500/40"
                          : "bg-[#00FF85]/15 text-[#00FF85] border-[#00FF85]/45 shadow-[0_0_12px_-3px_rgba(0,255,133,0.45)]"
                      }`}
                    >
                      {pick?.pick_code || "-"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Copy Text Button */}
          <div className="flex justify-end">
            <button
              type="button"
              onClick={handleCopyText}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#00FF85]/15 border border-[#00FF85]/40 text-[#00FF85] text-xs font-bold hover:bg-[#00FF85]/25 transition-colors"
            >
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? "Şablon Kopyalandı" : "Şablonu Metin Olarak Kopyala"}
            </button>
          </div>
        </div>
      )}

      {/* Compact History Table */}
      {history.length > 0 && (
        <div className="p-4 rounded-2xl glass-card space-y-2.5">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
            Geçmiş Spor Toto Karnesi
          </h3>
          <div className="divide-y divide-white/[0.06] text-xs">
            {history.map((h) => (
              <div key={h.id} className="py-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-semibold text-slate-200 truncate">{h.title}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                    {h.status === "SETTLED" ? "Sonuçlandı" : "Yayında"}
                  </span>
                </div>
                <div className="flex items-center gap-2 mono-nums text-[11px]">
                  {(["16", "32", "64"] as const).map((b) => {
                    const s = h.plans_summary?.[b];
                    if (!s) return null;
                    return (
                      <span
                        key={b}
                        className="px-2 py-0.5 rounded-lg bg-[#05080D]/80 border border-white/10 text-slate-300"
                      >
                        {b}K:{" "}
                        <strong className="text-[#00FF85]">
                          {s.hits !== null ? `${s.hits}/${s.settled_count}` : `~${s.expected_hits.toFixed(1)}`}
                        </strong>
                      </span>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
