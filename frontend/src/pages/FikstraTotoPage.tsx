import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BookmarkPlus,
  Check,
  Copy,
  Image as ImageIcon,
  Link2,
  Lock,
  Send,
  Shield,
  Sparkles,
  Trash2,
  Unlock,
  Wand2,
} from "lucide-react";
import { api } from "../api/client";
import type {
  SavedTotoSlip,
  SportotoCurrentResponse,
  SportotoHistoryItem,
  SportotoMatchItem,
} from "../types";
import {
  canonicalPickCode,
  computeLiveSurvival,
  computeMarginalAdvice,
  exactPoissonBinomial,
  pickProbForCode,
} from "../utils/totoMath";
import { generateTotoPngBlob } from "../utils/totoShare";

const SAVED_TOTO_KEY = "fikstra_saved_toto_v1";
const BUDGET_OPTIONS = [16, 32, 48, 64, 96, 128, 256] as const;

const MODES: { key: "hit15" | "shield13" | "contrarian"; label: string; desc: string }[] = [
  { key: "hit15", label: "15'li Hedef", desc: "Maksimum 15/15 olasılığı" },
  { key: "shield13", label: "13+ Hedefi", desc: "13 ve 14 barajını korur" },
  { key: "contrarian", label: "Sürpriz Odaklı", desc: "Riskli favoriler ve yüksek sürpriz" },
];

interface Props {
  telegramInviteUrl: string;
  initialSlipParam: string | null;
  onConsumeSlipParam: () => void;
  refreshTrigger: number;
}

export function FikstraTotoPage({
  telegramInviteUrl,
  initialSlipParam,
  onConsumeSlipParam,
  refreshTrigger,
}: Props) {
  const [current, setCurrent] = useState<SportotoCurrentResponse | null>(null);
  const [history, setHistory] = useState<SportotoHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [budget, setBudget] = useState<number>(32);
  const [mode, setMode] = useState<"hit15" | "shield13" | "contrarian">("hit15");
  const [picksBySlot, setPicksBySlot] = useState<Record<number, string>>({});
  const [lockedSlots, setLockedSlots] = useState<Record<number, boolean>>({});
  const [optimizing, setOptimizing] = useState(false);
  const [optError, setOptError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [pngBusy, setPngBusy] = useState(false);

  const [savedTotos, setSavedTotos] = useState<SavedTotoSlip[]>(() => {
    try {
      const raw = localStorage.getItem(SAVED_TOTO_KEY);
      return raw ? (JSON.parse(raw) as SavedTotoSlip[]) : [];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(SAVED_TOTO_KEY, JSON.stringify(savedTotos.slice(0, 15)));
    } catch {
      // ignore
    }
  }, [savedTotos]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2400);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([api.getSportotoCurrent(), api.getSportotoHistory()])
      .then(([cur, hist]) => {
        if (cancelled) return;
        setCurrent(cur);
        setHistory(hist.items || []);

        if (cur.active && cur.week) {
          const defaultPlan = cur.week.plans?.["32"] || cur.week.plans?.["16"];
          const nextPicks: Record<number, string> = {};
          if (initialSlipParam) {
            const parts = initialSlipParam.split(",").map((s) => s.trim());
            cur.week.matches.forEach((m, idx) => {
              nextPicks[m.slot_no] = canonicalPickCode(parts[idx] || "1");
            });
            onConsumeSlipParam();
          } else if (defaultPlan) {
            for (const pk of defaultPlan.picks) {
              nextPicks[pk.slot_no] = pk.pick_code;
            }
          } else {
            for (const m of cur.week.matches) {
              nextPicks[m.slot_no] = "1";
            }
          }
          setPicksBySlot(nextPicks);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCurrent({ active: false, week: null });
          setHistory([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]); // eslint-disable-line react-hooks/exhaustive-deps

  const week = current?.active ? current.week : null;

  // Live client-side calculations
  const liveStats = useMemo(() => {
    if (!week || week.matches.length === 0) return null;
    let cols = 1;
    let singles = 0;
    let doubles = 0;
    let triples = 0;
    const pickProbs: number[] = [];

    for (const m of week.matches) {
      const code = canonicalPickCode(picksBySlot[m.slot_no] || "1");
      const k = code.length;
      cols *= k;
      if (k === 1) singles += 1;
      else if (k === 2) doubles += 1;
      else triples += 1;
      pickProbs.push(pickProbForCode(m, code));
    }

    const probs = exactPoissonBinomial(pickProbs);
    const advice = computeMarginalAdvice(week.matches, picksBySlot);
    const survival = computeLiveSurvival(week.matches, picksBySlot);
    const colPrice = week.column_price || 10;
    return {
      columns: cols,
      estimatedCost: cols * colPrice,
      singles,
      doubles,
      triples,
      probs,
      advice,
      survival,
    };
  }, [week, picksBySlot]);

  const toggleOutcome = (slotNo: number, outcome: "1" | "X" | "2") => {
    setPicksBySlot((prev) => {
      const cur = canonicalPickCode(prev[slotNo] || "1");
      const has = cur.includes(outcome);
      if (has && cur.length === 1) return prev; // Keep at least 1 selection
      const nextSet = new Set(cur.split(""));
      if (has) nextSet.delete(outcome);
      else nextSet.add(outcome);
      const nextCode = ["1", "X", "2"].filter((c) => nextSet.has(c)).join("");
      return { ...prev, [slotNo]: nextCode };
    });
  };

  const toggleLock = (slotNo: number) => {
    setLockedSlots((prev) => ({ ...prev, [slotNo]: !prev[slotNo] }));
  };

  const handleDistributeBudget = async () => {
    if (!week) return;
    setOptimizing(true);
    setOptError(null);
    try {
      const locksPayload: Record<number, string> = {};
      for (const m of week.matches) {
        if (lockedSlots[m.slot_no]) {
          locksPayload[m.slot_no] = canonicalPickCode(picksBySlot[m.slot_no] || "1");
        }
      }
      const plan = await api.optimizeSportoto({
        week_id: week.id,
        budget,
        mode,
        locks: locksPayload,
      });
      const nextPicks: Record<number, string> = {};
      for (const pk of plan.picks) {
        nextPicks[pk.slot_no] = pk.pick_code;
      }
      setPicksBySlot(nextPicks);
      showToast(`${plan.used_columns} kolonluk şablon uygulandı`);
    } catch (e) {
      setOptError(e instanceof Error ? e.message : "Optimizasyon hatası");
    } finally {
      setOptimizing(false);
    }
  };

  const handleSaveToto = () => {
    if (!week || !liveStats) return;
    const entry: SavedTotoSlip = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      week_id: week.id,
      week_title: week.title,
      created_at: new Date().toISOString(),
      picks: { ...picksBySlot },
      columns: liveStats.columns,
      estimated_cost: liveStats.estimatedCost,
    };
    setSavedTotos((prev) => [entry, ...prev].slice(0, 15));
    showToast("Toto kuponu takibe alındı");
  };

  const handleCopyLink = () => {
    if (!week) return;
    const slipStr = week.matches.map((m) => picksBySlot[m.slot_no] || "1").join(",");
    const url = `${window.location.origin}/?tab=toto&slip=${encodeURIComponent(slipStr)}`;
    navigator.clipboard?.writeText(url);
    showToast("Paylaşım linki kopyalandı");
  };

  const handleCopyText = () => {
    if (!week || !liveStats) return;
    const lines = [
      `FİKSTRA TOTO — ${week.title} (${liveStats.columns} Kolon · ${liveStats.estimatedCost.toFixed(0)} ₺)`,
      "",
      ...week.matches.map(
        (m) => `${String(m.slot_no).padStart(2, "0")}. ${m.home_team} - ${m.away_team} -> ${picksBySlot[m.slot_no] || "1"}`
      ),
      "",
      `Beklenen İsabet: ${liveStats.probs.expected_hits.toFixed(1)}/15 · 13+: %${liveStats.probs.p13_plus.toFixed(1)}`,
    ];
    navigator.clipboard?.writeText(lines.join("\n"));
    showToast("Şablon metin olarak kopyalandı");
  };

  const handleSharePng = async () => {
    if (!week || !liveStats) return;
    setPngBusy(true);
    try {
      const blob = await generateTotoPngBlob({
        title: week.title,
        matches: week.matches,
        picksBySlot,
        columns: liveStats.columns,
        estimatedCost: liveStats.estimatedCost,
        probs: liveStats.probs,
      });
      const file = new File([blob], `fikstra-toto-${week.id}.png`, { type: "image/png" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: `Fikstra Toto — ${week.title}` });
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `fikstra-toto-${week.id}.png`;
        a.click();
        URL.revokeObjectURL(url);
      }
      showToast("PNG kartı hazırlandı");
    } catch {
      showToast("PNG oluşturulamadı");
    } finally {
      setPngBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-3">
        <div className="h-28 rounded-2xl glass-card animate-pulse" />
        <div className="h-64 rounded-2xl glass-card animate-pulse" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {toast && (
        <div className="fixed top-20 inset-x-0 z-50 flex justify-center px-4 pointer-events-none">
          <div className="px-4 py-2 rounded-full bg-[#080E18]/95 border border-[#00FF85]/45 text-xs font-bold text-[#00FF85] shadow-lg">
            {toast}
          </div>
        </div>
      )}

      {!week || !liveStats ? (
        /* Honest Empty State Showcase */
        <div className="p-6 sm:p-8 rounded-3xl glass-card space-y-5 text-center">
          <img
            src="/logo-profile.png"
            alt="Fikstra"
            className="w-12 h-12 rounded-2xl border border-[#00FF85]/35 shadow-[0_0_24px_-6px_rgba(0,255,133,0.4)] mx-auto object-cover"
          />
          <div className="space-y-1.5 max-w-lg mx-auto">
            <h2 className="text-base sm:text-lg font-extrabold text-white">
              Fikstra Toto — Yeni Hafta Bekleniyor
            </h2>
            <p className="text-xs text-slate-400 leading-relaxed">
              Haftanın 15 maçlık listesi açıldığında model olasılıkları ve güncel piyasa oranlarıyla
              hazırlanmış kolon şablonları burada açılır.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-left max-w-2xl mx-auto">
            {[
              {
                icon: Wand2,
                title: "Akıllı Kolon Dağıtımı",
                desc: "16–256 kolon bütçeni 15'li veya 13+ hedefine göre en verimli maçlara dağıt.",
              },
              {
                icon: Shield,
                title: "Anlık İhtimal Hesabı",
                desc: "Yaptığın her 1/X/2 seçiminde 12, 13, 14 ve 15 bilme ihtimalin anında hesaplanır.",
              },
              {
                icon: AlertTriangle,
                title: "Maç Risk Analizi",
                desc: "Banko maçları, riskli karşılaşmaları ve fazla yüklenilen favorileri tek bakışta gör.",
              },
              {
                icon: Sparkles,
                title: "Hamle Danışmanı",
                desc: "Bütçeni büyütürken en mantıklı çifte şansı, azaltırken en risksiz eksiltmeyi tek tıkla gör.",
              },
            ].map((f) => {
              const Icon = f.icon;
              return (
                <div
                  key={f.title}
                  className="p-3.5 rounded-2xl bg-[#05080D]/75 border border-white/[0.08] flex items-start gap-2.5"
                >
                  <Icon className="w-4 h-4 text-[#00FF85] shrink-0 mt-0.5" />
                  <div>
                    <p className="text-xs font-bold text-slate-100">{f.title}</p>
                    <p className="text-[11px] text-slate-400 mt-0.5">{f.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>

          <a
            href={telegramInviteUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold hover:bg-emerald-400 transition-colors"
          >
            <Send className="w-3.5 h-3.5" />
            Liste Açılınca Telegram&apos;da Haber Al
          </a>
        </div>
      ) : (
        <>
          {/* Top Live HUD Cockpit */}
          <div className="p-4 rounded-2xl glass-card space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-[#00FF85]/15 text-[#00FF85] border border-[#00FF85]/35">
                  FİKSTRA TOTO
                </span>
                <h2 className="text-sm sm:text-base font-extrabold text-white">{week.title}</h2>
              </div>
              <div className="flex items-center gap-2 mono-nums">
                <span className="px-2.5 py-1 rounded-xl bg-[#05080D] border border-white/10 text-xs font-extrabold text-white">
                  {liveStats.columns} Kolon
                </span>
                <span className="px-2.5 py-1 rounded-xl bg-[#00FF85]/15 border border-[#00FF85]/35 text-xs font-extrabold text-[#00FF85]">
                  {liveStats.estimatedCost.toFixed(0)} ₺
                </span>
              </div>
            </div>

            {/* 5 Live Counters */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {[
                { label: "15/15", val: `%${liveStats.probs.p15.toFixed(3)}`, hi: true },
                { label: "14+", val: `%${liveStats.probs.p14_plus.toFixed(2)}` },
                { label: "13+", val: `%${liveStats.probs.p13_plus.toFixed(1)}` },
                { label: "12+", val: `%${liveStats.probs.p12_plus.toFixed(1)}` },
                { label: "Beklenen", val: `${liveStats.probs.expected_hits.toFixed(1)} / 15`, hi: true },
              ].map((c) => (
                <div
                  key={c.label}
                  className="px-3 py-2 rounded-xl bg-[#05080D]/80 border border-white/10"
                >
                  <p className="text-[10px] text-slate-400">{c.label}</p>
                  <p
                    className={`text-sm font-extrabold mono-nums ${
                      c.hi ? "text-[#00FF85]" : "text-slate-100"
                    }`}
                  >
                    {c.val}
                  </p>
                </div>
              ))}
            </div>

            {/* Live Survival Strip */}
            {liveStats.survival && (
              <div className="px-3 py-2 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-bold text-cyan-200 mono-nums">
                  Canlı Durum: {liveStats.survival.settled_hits}/{liveStats.survival.settled_count} tuttu
                  ({liveStats.survival.remaining_count} maç kaldı)
                </span>
                <div className="flex flex-wrap items-center gap-2 mono-nums text-[11px]">
                  <span className={liveStats.survival.can_hit_15 ? "text-[#00FF85]" : "text-rose-400 line-through"}>
                    15/15: %{liveStats.survival.p15_live.toFixed(2)}
                  </span>
                  <span className={liveStats.survival.can_hit_14 ? "text-cyan-300" : "text-rose-400 line-through"}>
                    14+: %{liveStats.survival.p14_live.toFixed(1)}
                  </span>
                  <span className={liveStats.survival.can_hit_13 ? "text-slate-200" : "text-rose-400 line-through"}>
                    13+: %{liveStats.survival.p13_live.toFixed(1)}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Strategy & Budget Distributor */}
          <div className="p-4 rounded-2xl glass-card space-y-3">
            <div className="grid grid-cols-3 gap-1.5">
              {MODES.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => setMode(m.key)}
                  className={`p-2 rounded-xl text-left border transition-all ${
                    mode === m.key
                      ? "bg-[#00FF85]/15 border-[#00FF85]/50 text-[#00FF85]"
                      : "bg-[#05080D]/60 border-white/10 text-slate-300 hover:border-white/25"
                  }`}
                >
                  <p className="text-[11px] font-extrabold leading-tight">{m.label}</p>
                  <p className="text-[10px] text-slate-400 hidden sm:block mt-0.5">{m.desc}</p>
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-1.5">
                {BUDGET_OPTIONS.map((b) => (
                  <button
                    key={b}
                    type="button"
                    onClick={() => setBudget(b)}
                    className={`px-2.5 py-1.5 rounded-lg text-[11px] font-bold mono-nums transition-colors ${
                      budget === b
                        ? "bg-[#00FF85] text-[#05080D]"
                        : "bg-white/[0.04] text-slate-300 border border-white/10"
                    }`}
                  >
                    {b}
                  </button>
                ))}
              </div>

              <button
                type="button"
                onClick={handleDistributeBudget}
                disabled={optimizing}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold hover:bg-emerald-400 disabled:opacity-50"
              >
                <Wand2 className="w-3.5 h-3.5" />
                {optimizing ? "Hesaplanıyor..." : "Bütçeyi Dağıt"}
              </button>
            </div>
            {optError && (
              <p className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-xl px-3 py-2">
                {optError}
              </p>
            )}
          </div>

          {/* 15-Match Interactive Toto Table */}
          <div className="rounded-2xl glass-card divide-y divide-white/[0.06] overflow-hidden">
            {week.matches.map((m: SportotoMatchItem) => {
              const code = canonicalPickCode(picksBySlot[m.slot_no] || "1");
              const locked = Boolean(lockedSlots[m.slot_no]);
              const p1 = Math.round((m.prob_1 || 0) * 100);
              const px = Math.round((m.prob_x || 0) * 100);
              const p2 = Math.max(0, 100 - p1 - px);
              const chaosCls =
                m.chaos?.label === "Banko"
                  ? "bg-emerald-500/15 text-[#00FF85] border-emerald-500/30"
                  : m.chaos?.label === "Çok Riskli"
                  ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
                  : "bg-amber-500/15 text-amber-300 border-amber-500/30";
              const hit = m.actual_result ? code.includes(m.actual_result) : null;

              return (
                <div key={m.slot_no} className="p-3 sm:px-4 space-y-2 hover:bg-white/[0.02]">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-start gap-2 min-w-0 flex-1">
                      <span className="w-6 h-6 rounded-lg bg-white/[0.06] border border-white/10 flex items-center justify-center text-[11px] font-bold text-slate-300 mono-nums shrink-0 mt-0.5">
                        {m.slot_no}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs sm:text-sm font-bold text-slate-100 truncate">
                          {m.home_team} - {m.away_team}
                        </p>
                        <div className="flex flex-wrap items-center gap-1.5 mt-1">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                              m.source === "fikstra_model"
                                ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/25"
                                : "bg-cyan-500/10 text-cyan-300 border-cyan-500/25"
                            }`}
                          >
                            {m.source === "fikstra_model" ? "Model" : "Piyasa"}
                          </span>
                          {m.chaos && (
                            <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${chaosCls}`}>
                              {m.chaos.label}
                            </span>
                          )}
                          {m.trap_signal?.is_trap && (
                            <span
                              title={
                                m.trap_signal.explanation ||
                                "Halkın büyük bölümü bu tarafa yüklenmiş, sürpriz ihtimali yüksek."
                              }
                              className="px-1.5 py-0.5 rounded text-[9px] font-extrabold bg-rose-500/20 text-rose-300 border border-rose-500/40"
                            >
                              RİSKLİ FAVORİ
                            </span>
                          )}
                          <span className="text-[10px] text-slate-400 mono-nums">
                            %{p1} · %{px} · %{p2}
                          </span>
                          {m.actual_result && (
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] font-extrabold mono-nums ${
                                hit ? "bg-emerald-500/20 text-[#00FF85]" : "bg-rose-500/20 text-rose-300"
                              }`}
                            >
                              MS:{m.actual_result} {hit ? "✓" : "✕"}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Interactive 1 / X / 2 + Lock */}
                    <div className="flex items-center gap-1 shrink-0">
                      {(["1", "X", "2"] as const).map((out) => {
                        const active = code.includes(out);
                        return (
                          <button
                            key={out}
                            type="button"
                            onClick={() => toggleOutcome(m.slot_no, out)}
                            className={`w-8 h-8 rounded-lg text-xs font-extrabold mono-nums border transition-all ${
                              active
                                ? "bg-[#00FF85] text-[#05080D] border-[#00FF85] shadow-[0_0_12px_-3px_rgba(0,255,133,0.65)]"
                                : "bg-[#05080D]/80 text-slate-400 border-white/10 hover:text-white"
                            }`}
                          >
                            {out}
                          </button>
                        );
                      })}
                      <button
                        type="button"
                        onClick={() => toggleLock(m.slot_no)}
                        aria-label={`Maç ${m.slot_no} kilitle`}
                        className={`w-8 h-8 rounded-lg border flex items-center justify-center transition-colors ${
                          locked
                            ? "bg-amber-500/20 border-amber-500/50 text-amber-300"
                            : "bg-[#05080D]/60 border-white/10 text-slate-500 hover:text-slate-300"
                        }`}
                      >
                        {locked ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Marginal Advisor */}
          {(liveStats.advice.best_upgrade || liveStats.advice.safest_downgrade) && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {liveStats.advice.best_upgrade && (
                <button
                  type="button"
                  onClick={() => {
                    const up = liveStats.advice.best_upgrade!;
                    setPicksBySlot((prev) => ({ ...prev, [up.slot_no]: up.to_code }));
                    showToast(`Maç ${up.slot_no} -> ${up.to_code} yükseltildi`);
                  }}
                  className="p-3 rounded-2xl glass-card hover:border-[#00FF85]/45 text-left space-y-1 transition-colors"
                >
                  <p className="text-[10px] font-extrabold uppercase tracking-wider text-[#00FF85]">
                    Önerilen Ekleme (Tıkla Uygula)
                  </p>
                  <p className="text-xs font-bold text-slate-100 truncate">
                    #{liveStats.advice.best_upgrade.slot_no} {liveStats.advice.best_upgrade.home_team} -{" "}
                    {liveStats.advice.best_upgrade.away_team}
                  </p>
                  <p className="text-[11px] text-slate-400 mono-nums">
                    {liveStats.advice.best_upgrade.from_code} → {liveStats.advice.best_upgrade.to_code} (+%
                    {liveStats.advice.best_upgrade.gain_prob_pct} ihtimal)
                  </p>
                </button>
              )}
              {liveStats.advice.safest_downgrade && (
                <button
                  type="button"
                  onClick={() => {
                    const dn = liveStats.advice.safest_downgrade!;
                    setPicksBySlot((prev) => ({ ...prev, [dn.slot_no]: dn.to_code }));
                    showToast(`Maç ${dn.slot_no} -> ${dn.to_code} indirildi`);
                  }}
                  className="p-3 rounded-2xl glass-card hover:border-cyan-500/45 text-left space-y-1 transition-colors"
                >
                  <p className="text-[10px] font-extrabold uppercase tracking-wider text-cyan-300">
                    Önerilen Eksiltme (Tıkla Uygula)
                  </p>
                  <p className="text-xs font-bold text-slate-100 truncate">
                    #{liveStats.advice.safest_downgrade.slot_no} {liveStats.advice.safest_downgrade.home_team} -{" "}
                    {liveStats.advice.safest_downgrade.away_team}
                  </p>
                  <p className="text-[11px] text-slate-400 mono-nums">
                    {liveStats.advice.safest_downgrade.from_code} → {liveStats.advice.safest_downgrade.to_code} (-%
                    {liveStats.advice.safest_downgrade.loss_prob_pct} kayıp)
                  </p>
                </button>
              )}
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleSaveToto}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-emerald-500/15 border border-emerald-500/35 text-[#00FF85] text-xs font-bold"
            >
              <BookmarkPlus className="w-3.5 h-3.5" />
              Toto&apos;yu Takibe Al
            </button>
            <button
              type="button"
              onClick={handleCopyLink}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl glass-card text-slate-200 hover:border-[#00FF85]/45 text-xs font-bold"
            >
              <Link2 className="w-3.5 h-3.5 text-[#00FF85]" />
              Linkle Gönder
            </button>
            <button
              type="button"
              onClick={handleSharePng}
              disabled={pngBusy}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold disabled:opacity-50"
            >
              <ImageIcon className="w-3.5 h-3.5" />
              {pngBusy ? "Hazırlanıyor..." : "PNG Kartı Paylaş / İndir"}
            </button>
            <button
              type="button"
              onClick={handleCopyText}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl glass-card text-slate-300 hover:text-white text-xs font-bold"
            >
              <Copy className="w-3.5 h-3.5" />
              Metin Kopyala
            </button>
          </div>
        </>
      )}

      {/* Saved Toto Slips */}
      {savedTotos.length > 0 && (
        <div className="p-4 rounded-2xl glass-card space-y-2.5">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
            Takip Ettiğim Toto Şablonları
          </h3>
          <div className="space-y-2">
            {savedTotos.map((st) => {
              const surv =
                week && week.id === st.week_id
                  ? computeLiveSurvival(week.matches, st.picks)
                  : null;
              return (
                <div
                  key={st.id}
                  className="p-3 rounded-xl bg-[#05080D]/80 border border-white/10 flex flex-wrap items-center justify-between gap-2 text-xs"
                >
                  <div>
                    <p className="font-bold text-slate-100">
                      {st.week_title} · {st.columns} Kolon ({st.estimated_cost.toFixed(0)} ₺)
                    </p>
                    {surv ? (
                      <p className="text-[11px] text-[#00FF85] mono-nums">
                        Canlı: {surv.settled_hits}/{surv.settled_count} tuttu · 13+: %{surv.p13_live.toFixed(1)}
                      </p>
                    ) : (
                      <p className="text-[11px] text-slate-400">Kayıtlı özel şablon</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {week && week.id === st.week_id && (
                      <button
                        type="button"
                        onClick={() => {
                          setPicksBySlot(st.picks);
                          showToast("Şablon yüklendi");
                        }}
                        className="px-2.5 py-1 rounded-lg bg-[#00FF85]/15 text-[#00FF85] font-bold inline-flex items-center gap-1"
                      >
                        <Check className="w-3 h-3" />
                        Yükle
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setSavedTotos((prev) => prev.filter((x) => x.id !== st.id))}
                      className="p-1.5 rounded-lg bg-white/5 text-slate-400 hover:text-rose-400"
                      aria-label="Sil"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div className="p-4 rounded-2xl glass-card space-y-2.5">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
            Geçmiş Fikstra Toto Karnesi
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
