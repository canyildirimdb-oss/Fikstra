import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  BookmarkPlus,
  Check,
  CheckCircle2,
  Copy,
  Image as ImageIcon,
  Link2,
  Lock,
  RotateCcw,
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
  computeCouponCheck,
  computeLiveSurvival,
  exactPoissonBinomial,
  getMatchRiskLabel,
  pickProbForCode,
} from "../utils/totoMath";
import { generateTotoPngBlob } from "../utils/totoShare";

const SAVED_TOTO_KEY = "fikstra_saved_toto_v1";
const BUDGET_OPTIONS = [8, 16, 32, 48, 64, 96, 128, 256] as const;

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
  onSelectMatch?: (matchId: number, isSettled?: boolean) => void;
}

export function FikstraTotoPage({
  telegramInviteUrl,
  initialSlipParam,
  onConsumeSlipParam,
  refreshTrigger,
  onSelectMatch,
}: Props) {
  const [current, setCurrent] = useState<SportotoCurrentResponse | null>(null);
  const [history, setHistory] = useState<SportotoHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);

  const [budget, setBudget] = useState<number>(32);
  const [mode, setMode] = useState<"hit15" | "shield13" | "contrarian">("hit15");
  const [picksBySlot, setPicksBySlot] = useState<Record<number, string>>({});
  const [lockedSlots, setLockedSlots] = useState<Record<number, boolean>>({});
  const [baselinePicks, setBaselinePicks] = useState<Record<number, string>>({});
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

  const loadedWeekIdRef = useRef<number | null>(null);
  const TOTO_DRAFT_PREFIX = "fikstra_toto_draft_v1:";

  useEffect(() => {
    let cancelled = false;
    if (loadedWeekIdRef.current === null) {
      setLoading(true);
    }
    Promise.all([api.getSportotoCurrent(), api.getSportotoHistory()])
      .then(([cur, hist]) => {
        if (cancelled) return;
        setCurrent(cur);
        setHistory((hist.items || []).filter((h) => h.status === "SETTLED"));

        if (cur.active && cur.week) {
          const defaultPlan = cur.week.plans?.["32"] || cur.week.plans?.["16"];
          const planBaseline: Record<number, string> = {};
          if (defaultPlan) {
            for (const pk of defaultPlan.picks) {
              planBaseline[pk.slot_no] = pk.pick_code;
            }
          }
          setBaselinePicks(planBaseline);

          const isSameWeek = loadedWeekIdRef.current === cur.week.id;
          loadedWeekIdRef.current = cur.week.id;

          if (initialSlipParam) {
            const nextPicks: Record<number, string> = {};
            const parts = initialSlipParam.split(",").map((s) => s.trim());
            cur.week.matches.forEach((m, idx) => {
              nextPicks[m.slot_no] = canonicalPickCode(parts[idx] || "1");
            });
            onConsumeSlipParam();
            setPicksBySlot(nextPicks);
            return;
          }

          if (isSameWeek) {
            return;
          }

          let restored = false;
          try {
            const rawDraft = localStorage.getItem(`${TOTO_DRAFT_PREFIX}${cur.week.id}`);
            if (rawDraft) {
              const parsed = JSON.parse(rawDraft) as {
                picksBySlot?: Record<number, string>;
                lockedSlots?: Record<number, boolean>;
                budget?: number;
                mode?: "hit15" | "shield13" | "contrarian";
              };
              if (parsed && parsed.picksBySlot && Object.keys(parsed.picksBySlot).length > 0) {
                setPicksBySlot(parsed.picksBySlot);
                if (parsed.lockedSlots) setLockedSlots(parsed.lockedSlots);
                if (typeof parsed.budget === "number") setBudget(parsed.budget);
                if (parsed.mode && ["hit15", "shield13", "contrarian"].includes(parsed.mode)) {
                  setMode(parsed.mode);
                }
                restored = true;
              }
            }
          } catch {
            // ignore corrupted draft
          }

          if (!restored) {
            const nextPicks: Record<number, string> = {};
            if (defaultPlan) {
              Object.assign(nextPicks, planBaseline);
            } else {
              for (const m of cur.week.matches) {
                nextPicks[m.slot_no] = "1";
              }
            }
            setPicksBySlot(nextPicks);
          }
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

  useEffect(() => {
    if (!week || Object.keys(picksBySlot).length === 0) return;
    try {
      localStorage.setItem(
        `${TOTO_DRAFT_PREFIX}${week.id}`,
        JSON.stringify({ picksBySlot, lockedSlots, budget, mode })
      );
    } catch {
      // ignore storage quota
    }
  }, [week, picksBySlot, lockedSlots, budget, mode]);

  // Single source of truth: 100% live from active picksBySlot & lockedSlots
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
    const couponCheck = computeCouponCheck(week.matches, picksBySlot, lockedSlots, cols, probs, mode, budget);
    const survival = computeLiveSurvival(week.matches, picksBySlot);
    const colPrice = week.column_price || 10;
    return {
      columns: cols,
      estimatedCost: cols * colPrice,
      singles,
      doubles,
      triples,
      probs,
      couponCheck,
      survival,
    };
  }, [week, picksBySlot, lockedSlots, mode, budget]);

  const hasLocksOrManualEdits = useMemo(() => {
    if (!week) return false;
    if (Object.values(lockedSlots).some(Boolean)) return true;
    return week.matches.some(
      (m) =>
        baselinePicks[m.slot_no] &&
        canonicalPickCode(picksBySlot[m.slot_no] || "1") !== baselinePicks[m.slot_no]
    );
  }, [week, lockedSlots, picksBySlot, baselinePicks]);

  const distributeBudget = async (
    nextBudget: number,
    nextMode: "hit15" | "shield13" | "contrarian",
    overrideLocks?: Record<number, boolean>
  ) => {
    if (!week) return;
    setOptimizing(true);
    setOptError(null);
    const activeLocks = overrideLocks ?? lockedSlots;
    try {
      const locksPayload: Record<number, string> = {};
      for (const m of week.matches) {
        if (activeLocks[m.slot_no]) {
          locksPayload[m.slot_no] = canonicalPickCode(picksBySlot[m.slot_no] || "1");
        }
      }
      const plan = await api.optimizeSportoto({
        week_id: week.id,
        budget: nextBudget,
        mode: nextMode,
        locks: locksPayload,
      });
      const nextPicks: Record<number, string> = {};
      for (const pk of plan.picks) {
        nextPicks[pk.slot_no] = pk.pick_code;
      }
      setPicksBySlot(nextPicks);
      setBaselinePicks(nextPicks);
    } catch (e) {
      setOptError(e instanceof Error ? e.message : "Optimizasyon hatası");
    } finally {
      setOptimizing(false);
    }
  };

  const handleSelectBudget = (b: number) => {
    setBudget(b);
    void distributeBudget(b, mode);
  };

  const handleSelectMode = (mKey: "hit15" | "shield13" | "contrarian") => {
    setMode(mKey);
    void distributeBudget(budget, mKey);
  };

  const handleResetLocks = () => {
    setLockedSlots({});
    void distributeBudget(budget, mode, {});
    showToast("Kilitler kaldırıldı ve kupon sıfırlandı");
  };

  const toggleOutcome = (slotNo: number, outcome: "1" | "X" | "2") => {
    setPicksBySlot((prev) => {
      const cur = canonicalPickCode(prev[slotNo] || "1");
      const has = cur.includes(outcome);
      if (has && cur.length === 1) return prev;
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
      `${liveStats.singles} Tek · ${liveStats.doubles} Çifte · ${liveStats.triples} Kapalı`,
      "",
      ...week.matches.map(
        (m) =>
          `${String(m.slot_no).padStart(2, "0")}. ${m.home_team} - ${m.away_team} -> ${picksBySlot[m.slot_no] || "1"}`
      ),
      "",
      `Beklenen Doğru: ${liveStats.probs.expected_hits.toFixed(1)}/15 · 13+: %${liveStats.probs.p13_plus.toFixed(1)}`,
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
        singles: liveStats.singles,
        doubles: liveStats.doubles,
        triples: liveStats.triples,
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

  const settledHistory = history.filter((h) => h.status === "SETTLED");

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
                desc: "8–256 kolon bütçeni 15'li veya 13+ hedefine göre en verimli maçlara dağıt.",
              },
              {
                icon: Shield,
                title: "Anlık İhtimal Hesabı",
                desc: "Yaptığın her 1/X/2 seçiminde 12, 13, 14 ve 15 bilme ihtimalin anında hesaplanır.",
              },
              {
                icon: AlertTriangle,
                title: "Maç Risk Analizi",
                desc: "Banko maçları, ortada karşılaşmaları ve halkın yüklendiği favorileri tek bakışta gör.",
              },
              {
                icon: Sparkles,
                title: "Canlı Kupon Kontrolü",
                desc: "Aynı bütçede daha güçlü bir dağılım varsa tek tıkla iyileştir.",
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
          {/* Top Live Summary Strip */}
          <div className="p-4 rounded-2xl glass-card space-y-3">
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-[#00FF85]/15 text-[#00FF85] border border-[#00FF85]/35">
                    FİKSTRA TOTO
                  </span>
                  <h2 className="text-sm sm:text-base font-extrabold text-white">{week.title}</h2>
                </div>
                {optimizing && (
                  <span className="text-[11px] font-semibold text-[#00FF85] animate-pulse">
                    Dağıtılıyor...
                  </span>
                )}
              </div>
              <p className="text-[11px] sm:text-xs text-slate-400">
                Güvendiğin maçları kilitle, kolon bütçeni seç; Fikstra çifte şansları en ortada maçlara otomatik dağıtsın.
              </p>
            </div>

            {/* 4 Clean Summary Cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div className="px-3 py-2.5 rounded-xl bg-[#05080D]/80 border border-white/10">
                <p className="text-[10px] text-slate-400">Kupon Yapısı</p>
                <p className="text-xs sm:text-sm font-extrabold text-slate-100 mono-nums mt-0.5">
                  {liveStats.singles} Tek · {liveStats.doubles} Çifte · {liveStats.triples} Kapalı
                </p>
              </div>
              <div className="px-3 py-2.5 rounded-xl bg-[#05080D]/80 border border-white/10">
                <p className="text-[10px] text-slate-400">Kolon &amp; Tutar</p>
                <p className="text-xs sm:text-sm font-extrabold text-[#00FF85] mono-nums mt-0.5">
                  {liveStats.columns} Kolon · {liveStats.estimatedCost.toFixed(0)} ₺
                </p>
              </div>
              <div className="px-3 py-2.5 rounded-xl bg-[#05080D]/80 border border-white/10">
                <p className="text-[10px] text-slate-400">Beklenen Doğru</p>
                <p className="text-xs sm:text-sm font-extrabold text-[#00FF85] mono-nums mt-0.5">
                  {liveStats.probs.expected_hits.toFixed(1)} / 15 Maç
                </p>
              </div>
              <div className="px-3 py-2.5 rounded-xl bg-[#05080D]/80 border border-white/10">
                <div className="flex items-center justify-between gap-1">
                  <p className="text-[10px] text-slate-400">İkramiye Şansı (13+)</p>
                  <span className="px-1.5 py-0.2 rounded bg-white/[0.06] text-[9px] font-semibold text-slate-300">
                    Başlangıç
                  </span>
                </div>
                <p className="text-xs sm:text-sm font-extrabold text-slate-100 mono-nums mt-0.5">
                  %{liveStats.probs.p13_plus.toFixed(1)}
                </p>
                <p className="text-[10px] text-slate-400 mono-nums mt-0.5">
                  12+: %{liveStats.probs.p12_plus.toFixed(1)} · 14+: %{liveStats.probs.p14_plus.toFixed(1)}
                </p>
              </div>
            </div>

            {/* Live Survival Strip */}
            {liveStats.survival && (
              <div className="px-3 py-2 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-bold text-cyan-200 mono-nums inline-flex items-center gap-1.5">
                  <span>
                    Canlı Durum: {liveStats.survival.settled_hits}/{liveStats.survival.settled_count} tuttu
                    ({liveStats.survival.remaining_count} maç kaldı)
                  </span>
                  <span className="px-1.5 py-0.2 rounded bg-cyan-500/20 border border-cyan-400/35 text-[9px] font-extrabold text-cyan-200">
                    Güncel
                  </span>
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

          {/* Instant Strategy & Budget Bar */}
          <div className="p-4 rounded-2xl glass-card space-y-3">
            <div className="grid grid-cols-3 gap-1.5">
              {MODES.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  disabled={optimizing}
                  onClick={() => handleSelectMode(m.key)}
                  className={`p-2 rounded-xl text-left border transition-all disabled:opacity-60 ${
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
                    disabled={optimizing}
                    onClick={() => handleSelectBudget(b)}
                    className={`px-2.5 py-1.5 rounded-lg text-[11px] font-bold mono-nums transition-colors disabled:opacity-60 ${
                      budget === b
                        ? "bg-[#00FF85] text-[#05080D]"
                        : "bg-white/[0.04] text-slate-300 border border-white/10 hover:border-white/25"
                    }`}
                  >
                    {b}
                  </button>
                ))}
              </div>

              <button
                type="button"
                onClick={handleResetLocks}
                disabled={!hasLocksOrManualEdits || optimizing}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-white/[0.04] border border-white/10 text-slate-300 hover:text-white hover:border-white/25 text-[11px] font-bold disabled:opacity-40 disabled:pointer-events-none transition-colors"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Kilitleri Kaldır &amp; Sıfırla
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
            {/* Desktop Column Headers */}
            <div className="hidden sm:flex items-center justify-between px-4 py-2 bg-[#05080D]/60 text-[10px] font-bold uppercase tracking-wider text-slate-400">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <span className="w-6 text-center">#</span>
                <span>Maç &amp; İhtimaller</span>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <span className="w-[104px] text-center">Seçim (1 · X · 2)</span>
                <span className="w-8 text-center">Kilitle</span>
              </div>
            </div>

            {week.matches.map((m: SportotoMatchItem) => {
              const code = canonicalPickCode(picksBySlot[m.slot_no] || "1");
              const locked = Boolean(lockedSlots[m.slot_no]);
              const p1 = Math.round((m.prob_1 || 0) * 100);
              const px = Math.round((m.prob_x || 0) * 100);
              const p2 = Math.max(0, 100 - p1 - px);
              const maxPct = Math.max(p1, px, p2);
              const riskLabel = getMatchRiskLabel(m);

              const riskCls =
                riskLabel === "Banko"
                  ? "bg-emerald-500/15 text-[#00FF85] border-emerald-500/35"
                  : riskLabel === "Favori"
                  ? "bg-lime-500/15 text-lime-300 border-lime-500/30"
                  : riskLabel === "Ortada"
                  ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
                  : "bg-rose-500/15 text-rose-300 border-rose-500/30";

              const kindLabel = code.length === 1 ? "Tek" : code.length === 2 ? "Çifte" : "Kapalı";
              const kindCls =
                code.length === 3
                  ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
                  : code.length === 2
                  ? "bg-cyan-500/15 text-cyan-300 border-cyan-500/30"
                  : "bg-white/[0.06] text-slate-300 border-white/10";

              const isFinished = Boolean(m.actual_result);
              const hit = m.actual_result ? code.includes(m.actual_result) : null;
              const hasPublic =
                m.public_pct_1 !== null && m.public_pct_x !== null && m.public_pct_2 !== null;

              return (
                <div key={m.slot_no} className="p-3 sm:px-4 space-y-2 hover:bg-white/[0.02]">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-start gap-2 min-w-0 flex-1">
                      <span className="w-6 h-6 rounded-lg bg-white/[0.06] border border-white/10 flex items-center justify-center text-[11px] font-bold text-slate-300 mono-nums shrink-0 mt-0.5">
                        {m.slot_no}
                      </span>
                      <div className="min-w-0 flex-1 space-y-1">
                        <div className="flex items-center justify-between gap-2 min-w-0">
                          <p className="text-xs sm:text-sm font-bold text-slate-100 truncate min-w-0 flex-1">
                            {m.home_team.replace(/\s+A\.?\s*Ş\.?$/i, "").trim()} -{" "}
                            {m.away_team.replace(/\s+A\.?\s*Ş\.?$/i, "").trim()}
                          </p>
                          {m.match_id && onSelectMatch && (
                            <button
                              type="button"
                              onClick={() => onSelectMatch(m.match_id!, Boolean(m.actual_result))}
                              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-white/[0.06] hover:bg-[#00FF85]/15 text-[10px] font-bold text-slate-300 hover:text-[#00FF85] border border-white/10 transition-colors shrink-0"
                            >
                              Analiz
                              <ArrowUpRight className="w-2.5 h-2.5" />
                            </button>
                          )}
                        </div>

                        <div className="flex flex-wrap items-center gap-1.5">
                          <span
                            className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                              m.source === "fikstra_model"
                                ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/25"
                                : "bg-cyan-500/10 text-cyan-300 border-cyan-500/25"
                            }`}
                          >
                            {m.source === "fikstra_model" ? "Model" : "Piyasa"}
                          </span>
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${riskCls}`}>
                            {riskLabel}
                          </span>
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${kindCls}`}>
                            {kindLabel}
                          </span>
                          {m.trap_signal?.is_trap && (
                            <span
                              title={
                                m.trap_signal.explanation ||
                                "Halkın büyük bölümü bu tarafa yüklenmiş, sürpriz ihtimali yüksek."
                              }
                              className="px-1.5 py-0.5 rounded text-[9px] font-extrabold bg-rose-500/20 text-rose-300 border border-rose-500/40"
                            >
                              Halk %{Math.round(m.trap_signal.fav_public_pct)} (Sürpriz Riski)
                            </span>
                          )}
                          {m.actual_result && (
                            <span
                              className={`px-1.5 py-0.5 rounded text-[10px] font-extrabold mono-nums ${
                                hit ? "bg-emerald-500/20 text-[#00FF85]" : "bg-rose-500/20 text-rose-300"
                              }`}
                            >
                              MS:{m.actual_result}
                              {m.home_score != null && m.away_score != null ? ` (${m.home_score}-${m.away_score})` : ""}{" "}
                              {hit ? "✓" : "✕"}
                            </span>
                          )}
                        </div>

                        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px] mono-nums">
                          <span className="text-slate-400 whitespace-nowrap">
                            <span className={p1 === maxPct ? "font-extrabold text-slate-100" : ""}>
                              1: %{p1}
                            </span>
                            {" · "}
                            <span className={px === maxPct ? "font-extrabold text-slate-100" : ""}>
                              X: %{px}
                            </span>
                            {" · "}
                            <span className={p2 === maxPct ? "font-extrabold text-slate-100" : ""}>
                              2: %{p2}
                            </span>
                          </span>
                          {hasPublic && (
                            <span className="text-[10px] text-slate-500 whitespace-nowrap">
                              İddaa Halkı: 1:%{Math.round(m.public_pct_1!)} · X:%{Math.round(m.public_pct_x!)} · 2:%{Math.round(m.public_pct_2!)}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Interactive 1 / X / 2 + Lock (disabled once finished) */}
                    <div className="flex items-center gap-1 shrink-0">
                      {(["1", "X", "2"] as const).map((out) => {
                        const active = code.includes(out);
                        return (
                          <button
                            key={out}
                            type="button"
                            disabled={isFinished}
                            onClick={() => toggleOutcome(m.slot_no, out)}
                            className={`w-8 h-8 rounded-lg text-xs font-extrabold mono-nums border transition-all disabled:opacity-60 disabled:cursor-not-allowed ${
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
                        disabled={isFinished}
                        onClick={() => toggleLock(m.slot_no)}
                        aria-label={isFinished ? `Maç ${m.slot_no} sonuçlandı` : `Maç ${m.slot_no} kilitle`}
                        className={`w-8 h-8 rounded-lg border flex items-center justify-center transition-colors disabled:opacity-60 disabled:cursor-not-allowed ${
                          isFinished || locked
                            ? "bg-amber-500/20 border-amber-500/50 text-amber-300"
                            : "bg-[#05080D]/60 border-white/10 text-slate-500 hover:text-slate-300"
                        }`}
                      >
                        {isFinished || locked ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Smart Coupon Check (Replaces Ping-Pong Add/Remove) */}
          <div className="p-4 rounded-2xl glass-card space-y-2.5">
            <div className="flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-[#00FF85]" />
              <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-300">
                Kupon Kontrolü
              </h3>
            </div>

            {liveStats.couponCheck.status === "improvable" && liveStats.couponCheck.improvedPicks ? (
              <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="space-y-1">
                  <p className="text-xs font-extrabold text-amber-300">
                    Aynı bütçeyle daha güçlü bir dağılım mümkün
                  </p>
                  <p className="text-xs text-slate-200 leading-relaxed">
                    {liveStats.couponCheck.message}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setPicksBySlot(liveStats.couponCheck.improvedPicks!);
                    showToast("Aynı bütçe için en verimli dağılım uygulandı");
                  }}
                  className="px-4 py-2 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold hover:bg-emerald-400 transition-colors shrink-0"
                >
                  Aynı Bütçeyle İyileştir
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="px-3 py-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center gap-2 text-xs font-bold text-[#00FF85]">
                  <CheckCircle2 className="w-4 h-4 shrink-0" />
                  <span>
                    {liveStats.couponCheck.settledCount > 0
                      ? `Sonuçlanan ${liveStats.couponCheck.settledCount} maç kilitli; kalan ${liveStats.couponCheck.remainingCount} maç için kuponun ${liveStats.columns} kolon bütçesinde en verimli dağılımda.`
                      : `Kuponun ${liveStats.columns} kolon için en verimli dağılımda.`}
                  </span>
                </div>
                {liveStats.couponCheck.nextDouble && (
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-300 px-1">
                    <span>
                      Bütçeyi 2 katına çıkarırsan ilk çifte şans için en mantıklı maç:{" "}
                      <strong className="text-white">
                        #{liveStats.couponCheck.nextDouble.slot_no}{" "}
                        {liveStats.couponCheck.nextDouble.home_team} -{" "}
                        {liveStats.couponCheck.nextDouble.away_team} (
                        {liveStats.couponCheck.nextDouble.from_code} →{" "}
                        {liveStats.couponCheck.nextDouble.to_code})
                      </strong>
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        const nd = liveStats.couponCheck.nextDouble!;
                        setPicksBySlot((prev) => ({ ...prev, [nd.slot_no]: nd.to_code }));
                        if ((BUDGET_OPTIONS as readonly number[]).includes(nd.new_columns)) {
                          setBudget(nd.new_columns);
                        }
                        showToast(`${nd.new_columns} kolona yükseltildi`);
                      }}
                      className="px-3 py-1 rounded-lg bg-white/[0.06] hover:bg-white/[0.12] border border-white/15 text-[11px] font-bold text-slate-200 mono-nums shrink-0 transition-colors"
                    >
                      Uygula ({liveStats.couponCheck.nextDouble.new_columns} Kolon)
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

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

      {/* Settled History Only */}
      {settledHistory.length > 0 && (
        <div className="p-4 rounded-2xl glass-card space-y-2.5">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300">
            Geçmiş Fikstra Toto Karnesi
          </h3>
          <div className="divide-y divide-white/[0.06] text-xs">
            {settledHistory.map((h) => (
              <div key={h.id} className="py-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-semibold text-slate-200 truncate">{h.title}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-[#00FF85] border border-emerald-500/25">
                    Sonuçlandı
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2 mono-nums text-[11px]">
                  {(["16", "32", "64"] as const).map((b) => {
                    const s = h.plans_summary?.[b];
                    if (!s || s.hits === null) return null;
                    return (
                      <span
                        key={b}
                        className="px-2 py-0.5 rounded-lg bg-[#05080D]/80 border border-white/10 text-slate-300"
                      >
                        {b} Kolon:{" "}
                        <strong className="text-[#00FF85]">
                          {s.hits}/{s.settled_count}
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
