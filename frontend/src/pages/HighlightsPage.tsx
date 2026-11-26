import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Check, Flame, Plus, Search, Send, ShieldCheck, X, Zap } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomSlipItem,
  DashboardSummary,
  PerformanceData,
  PredictionItem,
} from "../types";
import { formatMarketLabel, formatPickWithTeam, formatTurkishPossessive } from "../utils/format";
import { ConfidenceRing } from "../components/ConfidenceRing";
import { FormStrip, LeagueBadge, TeamBadge } from "../components/TeamBadge";

interface Props {
  summary: DashboardSummary | null;
  onSelectMatch: (matchId: number) => void;
  onGoPerformance: () => void;
  onGoCoupons: () => void;
  onOpenHowItWorks: () => void;
  telegramInviteUrl: string;
  slipItems: CustomSlipItem[];
  onToggleSlip: (item: CustomSlipItem) => void;
  refreshTrigger: number;
}

function getTopReasonChips(item: PredictionItem): string[] {
  const bd = item.breakdown;
  if (!bd) return [];
  const candidates: { ratio: number; label: string }[] = [
    {
      ratio: bd.value / 35,
      label:
        item.value_edge_pct != null && item.value_edge_pct >= 1
          ? `Global Değer +%${item.value_edge_pct.toFixed(0)}`
          : "Oran Değeri",
    },
    {
      ratio: bd.odds_move / 25,
      label: bd.smart_money
        ? "Akıllı Para Teyidi"
        : item.odds_drop_pct != null && item.odds_drop_pct >= 2
        ? `%${item.odds_drop_pct.toFixed(0)} Oran Düşüşü`
        : "Oran Dengesi",
    },
    {
      ratio: bd.form_goals / 25,
      label: "Form & Gol Gücü",
    },
    {
      ratio: bd.injury / 15,
      label: bd.injury_warning ? "Eksik Analizi" : "Kadro Sağlam",
    },
  ];
  candidates.sort((a, b) => b.ratio - a.ratio);
  return candidates.slice(0, 2).map((c) => c.label);
}

export function HighlightsPage({
  summary,
  onSelectMatch,
  onGoPerformance,
  onGoCoupons,
  onOpenHowItWorks,
  telegramInviteUrl,
  slipItems,
  onToggleSlip,
  refreshTrigger,
}: Props) {
  const [todayOnly, setTodayOnly] = useState(true);
  const [search, setSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [items, setItems] = useState<PredictionItem[]>([]);
  const [todayCount, setTodayCount] = useState<number | null>(null);
  const [perf, setPerf] = useState<PerformanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [slideIdx, setSlideIdx] = useState(0);
  const [tickerPaused, setTickerPaused] = useState(false);
  const prefersReducedMotion = useReducedMotion();
  const lastFilterRef = useRef<string>("");
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const filterKey = `${todayOnly}|${search}`;
    if (lastFilterRef.current !== filterKey) {
      setLoading(true);
      lastFilterRef.current = filterKey;
    }
    api
      .getPredictions({
        todayOnly,
        majorOnly: false,
        minOdd: 1.3,
        minConfidence: 0,
        search,
      })
      .then((res) => {
        if (!cancelled) {
          setItems(res.items);
          if (todayOnly && !search.trim()) {
            setTodayCount(res.count);
          }
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [todayOnly, search, refreshTrigger]);

  useEffect(() => {
    let cancelled = false;
    api
      .getPerformance()
      .then((p) => {
        if (!cancelled) setPerf(p);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]);

  useEffect(() => {
    if (searchOpen) {
      searchInputRef.current?.focus();
    }
  }, [searchOpen]);

  const slipIds = new Set(
    slipItems.map((s) => `${s.match_id}_${s.market_key}_${s.selection_key}`)
  );

  const heroItem = useMemo(() => {
    if (items.length === 0) return null;
    if (summary?.top_confidence) {
      const matched = items.find((i) => i.match_id === summary.top_confidence!.match_id);
      if (matched) return matched;
    }
    return items[0];
  }, [items, summary?.top_confidence]);

  const heroReasons = useMemo(() => (heroItem ? getTopReasonChips(heroItem) : []), [heroItem]);

  const slides = useMemo(() => {
    const analyzedToday = todayCount ?? summary?.active_matches_count ?? null;
    const trustLabel =
      perf && perf.total_settled > 0
        ? `Son ${perf.total_settled} tahminin ${formatTurkishPossessive(perf.won_count)} tuttu`
        : summary?.settled_total
        ? `${summary.settled_total} tahmin sonuçlandı (%${summary.overall_win_rate ?? 0})`
        : "İlk sonuçlar bekleniyor";

    return [
      {
        key: "trust",
        icon: "dots" as const,
        text: trustLabel,
        onClick: onGoPerformance,
      },
      {
        key: "telegram",
        icon: "telegram" as const,
        text: "Fikstra Telegram Terminali yayında · Anlık kupon, tekli ve günlük karne",
        onClick: () => window.open(telegramInviteUrl, "_blank", "noopener,noreferrer"),
      },
      {
        key: "coupons",
        icon: "zap" as const,
        text:
          analyzedToday !== null
            ? `Bugün ${analyzedToday} maç analiz edildi · Günün hazır kuponlarına göz at`
            : "Günün analizleri ve hazır kuponlarına göz at",
        onClick: onGoCoupons,
      },
      {
        key: "transparency",
        icon: "shield" as const,
        text: "Tüm tahminler oranıyla kilitlenir · Sahte geçmiş üretilmez",
        onClick: onOpenHowItWorks,
      },
    ];
  }, [
    perf,
    summary?.settled_total,
    summary?.overall_win_rate,
    summary?.active_matches_count,
    todayCount,
    onGoPerformance,
    onGoCoupons,
    onOpenHowItWorks,
    telegramInviteUrl,
  ]);

  useEffect(() => {
    if (tickerPaused) return;
    const id = window.setInterval(() => {
      setSlideIdx((prev) => (prev + 1) % slides.length);
    }, 5000);
    return () => window.clearInterval(id);
  }, [tickerPaused, slides.length]);

  const activeSlide = slides[slideIdx % slides.length];

  return (
    <div className="space-y-3.5">
      {/* Full-Width Hero Card: Günün En Güvenli Maçı */}
      {(heroItem || summary?.top_confidence) && (
        <button
          type="button"
          onClick={() => {
            const targetId = heroItem?.match_id ?? summary?.top_confidence?.match_id;
            if (targetId) onSelectMatch(targetId);
          }}
          className="w-full text-left relative overflow-hidden p-4 sm:p-5 rounded-2xl glass-card glass-card-hover neon-bottom-glow transition-all"
        >
          <div className="flex items-center justify-between gap-2 mb-2.5">
            <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-[#00FF85] uppercase tracking-wider">
              <Flame className="w-3.5 h-3.5" />
              Günün En Güvenli Maçı
            </span>
            {heroItem && (
              <span className="text-[11px] mono-nums text-slate-300 font-semibold">
                {heroItem.start_date_tr} {heroItem.start_time_tr}
              </span>
            )}
          </div>

          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-2">
              {heroItem ? (
                <div className="flex items-center gap-2.5 flex-wrap">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <TeamBadge name={heroItem.home_team} logoUrl={heroItem.home_logo_url} size="md" />
                    <span className="text-sm sm:text-base font-extrabold text-white truncate">
                      {heroItem.home_team}
                    </span>
                  </div>
                  <span className="text-xs text-slate-500 font-bold">vs</span>
                  <div className="flex items-center gap-1.5 min-w-0">
                    <TeamBadge name={heroItem.away_team} logoUrl={heroItem.away_logo_url} size="md" />
                    <span className="text-sm sm:text-base font-extrabold text-white truncate">
                      {heroItem.away_team}
                    </span>
                  </div>
                </div>
              ) : (
                <p className="text-sm sm:text-base font-extrabold text-white truncate">
                  {summary?.top_confidence?.match_name}
                </p>
              )}

              <div className="flex flex-wrap items-center gap-1.5">
                {heroItem ? (
                  <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#00FF85]/15 border border-[#00FF85]/45 text-xs font-bold text-[#00FF85] shadow-[0_0_12px_-4px_rgba(0,255,133,0.45)]">
                    <span>
                      {formatPickWithTeam(
                        heroItem.market_key,
                        heroItem.selection_key,
                        heroItem.home_team,
                        heroItem.away_team
                      )}
                    </span>
                    <span className="mono-nums text-white font-extrabold">
                      {heroItem.iddaa_odd.toFixed(2)}
                    </span>
                  </span>
                ) : (
                  summary?.top_confidence && (
                    <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#00FF85]/15 border border-[#00FF85]/45 text-xs font-bold text-[#00FF85]">
                      <span>
                        {formatMarketLabel(
                          summary.top_confidence.market_key,
                          summary.top_confidence.selection_key
                        )}
                      </span>
                      <span className="mono-nums text-white font-extrabold">
                        {summary.top_confidence.iddaa_odd.toFixed(2)}
                      </span>
                    </span>
                  )
                )}

                {heroReasons.map((reason) => (
                  <span
                    key={reason}
                    className="px-2.5 py-1 rounded-full bg-white/[0.05] border border-white/10 text-[11px] font-medium text-slate-300"
                  >
                    {reason}
                  </span>
                ))}
              </div>
            </div>

            <ConfidenceRing
              score={heroItem?.confidence_score ?? summary?.top_confidence?.confidence_score ?? 70}
              size={60}
            />
          </div>
        </button>
      )}

      {/* Animated Rotating Info & Trust Strip */}
      <div
        onMouseEnter={() => setTickerPaused(true)}
        onMouseLeave={() => setTickerPaused(false)}
        onTouchStart={() => setTickerPaused(true)}
        onTouchEnd={() => setTickerPaused(false)}
        className="w-full px-3.5 py-2.5 rounded-xl glass-card hover:border-[#00FF85]/40 transition-colors flex items-center justify-between gap-2 text-xs"
      >
        <button
          type="button"
          onClick={activeSlide.onClick}
          className="flex-1 min-w-0 flex items-center gap-2 text-left overflow-hidden"
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={activeSlide.key}
              initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 6 }}
              animate={prefersReducedMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
              exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: -6 }}
              transition={{ duration: prefersReducedMotion ? 0.12 : 0.22 }}
              className="flex items-center gap-2 min-w-0 w-full"
            >
              {activeSlide.icon === "telegram" && (
                <Send className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
              )}
              {activeSlide.icon === "zap" && (
                <Zap className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
              )}
              {activeSlide.icon === "shield" && (
                <ShieldCheck className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
              )}
              <span className="text-slate-200 font-semibold truncate">{activeSlide.text}</span>
              {activeSlide.icon === "dots" && perf && perf.archive.length > 0 && (
                <span className="hidden xs:inline-flex items-center gap-1 shrink-0">
                  {perf.archive.slice(0, 8).map((r) => (
                    <span
                      key={r.prediction_id}
                      className={`w-2 h-2 rounded-full ${
                        r.status === "WON" ? "bg-[#00FF85]" : "bg-rose-500/80"
                      }`}
                    />
                  ))}
                </span>
              )}
              <ArrowRight className="w-3.5 h-3.5 text-[#00FF85] shrink-0 ml-auto" />
            </motion.div>
          </AnimatePresence>
        </button>

        {/* Clickable Slide Indicator Dots */}
        <div className="flex items-center gap-1 shrink-0 pl-1.5 border-l border-white/10">
          {slides.map((s, idx) => (
            <button
              key={s.key}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setSlideIdx(idx);
              }}
              aria-label={`Duyuru ${idx + 1}`}
              className={`h-1.5 rounded-full transition-all ${
                idx === slideIdx % slides.length
                  ? "w-3.5 bg-[#00FF85]"
                  : "w-1.5 bg-white/20 hover:bg-white/40"
              }`}
            />
          ))}
        </div>
      </div>

      {/* Sticky Filter Row: Bugün · N / Tümü + Expandable 🔍 */}
      <div className="sticky top-16 z-20 -mx-4 px-4 py-2.5 bg-[#05080D]/90 backdrop-blur-xl border-b border-white/[0.05]">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setTodayOnly(true)}
              className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-xs font-bold transition-all ${
                todayOnly
                  ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_16px_-4px_rgba(0,255,133,0.65)]"
                  : "glass-card text-slate-300 hover:text-white"
              }`}
            >
              <Zap className="w-3.5 h-3.5" />
              Bugün{todayCount !== null ? ` · ${todayCount}` : ""}
            </button>
            <button
              onClick={() => setTodayOnly(false)}
              className={`px-3.5 py-2 rounded-full text-xs font-bold transition-all ${
                !todayOnly
                  ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_16px_-4px_rgba(0,255,133,0.65)]"
                  : "glass-card text-slate-300 hover:text-white"
              }`}
            >
              Tümü
            </button>
          </div>

          {!searchOpen && !search ? (
            <button
              type="button"
              onClick={() => setSearchOpen(true)}
              className="w-9 h-9 rounded-full glass-card flex items-center justify-center text-slate-300 hover:text-[#00FF85] hover:border-[#00FF85]/45 transition-colors shrink-0"
              aria-label="Takım veya lig ara"
              title="Ara"
            >
              <Search className="w-4 h-4" />
            </button>
          ) : (
            <div className="relative flex-1 max-w-[210px] sm:max-w-[260px]">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                ref={searchInputRef}
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Takım veya lig..."
                className="w-full pl-8 pr-7 py-1.5 rounded-full glass-card text-xs text-slate-100 placeholder:text-slate-500 focus:border-[#00FF85]/50 outline-none"
              />
              <button
                type="button"
                onClick={() => {
                  setSearch("");
                  setSearchOpen(false);
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                aria-label="Aramayı temizle"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Match Cards */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {[1, 2, 3, 4].map((n) => (
            <div key={n} className="h-36 rounded-2xl glass-card animate-pulse" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="p-8 text-center rounded-2xl glass-card">
          <p className="text-sm font-semibold text-slate-200">
            Seçili filtrelere uygun maç bulunamadı
          </p>
          <p className="text-xs text-slate-400 mt-1">
            "Tümü" sekmesini veya farklı bir arama terimini deneyebilirsiniz.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {items.map((item, idx) => {
            const inSlip = slipIds.has(`${item.match_id}_${item.market_key}_${item.selection_key}`);
            const isElite = item.confidence_score >= 80;

            return (
              <motion.div
                key={item.prediction_id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(idx * 0.025, 0.22) }}
                onClick={() => onSelectMatch(item.match_id)}
                className="relative cursor-pointer p-4 rounded-2xl glass-card glass-card-hover transition-all overflow-hidden"
              >
                {/* Top Meta: League Logo + Name | Date & Time */}
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <LeagueBadge name={item.league} logoUrl={item.league_logo_url} />
                    <span className="text-[11px] font-medium text-slate-400 truncate">
                      {item.league}
                    </span>
                  </div>
                  <span className="text-[11px] mono-nums text-slate-300 font-semibold shrink-0">
                    {item.start_date_tr} {item.start_time_tr}
                  </span>
                </div>

                {/* Middle: Home & Away Teams + Dot Form + Confidence Ring */}
                <div className="flex items-center justify-between gap-3">
                  <div className="space-y-2.5 min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TeamBadge name={item.home_team} logoUrl={item.home_logo_url} />
                        <span className="text-sm font-bold text-white truncate">
                          {item.home_team}
                        </span>
                      </div>
                      <FormStrip form={item.home_form} variant="dots" />
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TeamBadge name={item.away_team} logoUrl={item.away_logo_url} />
                        <span className="text-sm font-bold text-white truncate">
                          {item.away_team}
                        </span>
                      </div>
                      <FormStrip form={item.away_form} variant="dots" />
                    </div>
                  </div>

                  <ConfidenceRing score={item.confidence_score} size={54} />
                </div>

                {/* Bottom: Pick/Odd Pill + 44x44 Touch Target + Button */}
                <div className="mt-3.5 pt-3 border-t border-white/[0.06] flex items-center justify-between gap-2">
                  <div
                    className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#00FF85]/10 border border-[#00FF85]/45 min-w-0 ${
                      isElite ? "shadow-[0_0_12px_-4px_rgba(0,255,133,0.45)]" : ""
                    }`}
                  >
                    <span className="text-xs font-bold text-[#00FF85] truncate">
                      {formatPickWithTeam(
                        item.market_key,
                        item.selection_key,
                        item.home_team,
                        item.away_team
                      )}
                    </span>
                    <span className="text-xs font-extrabold mono-nums text-white shrink-0">
                      {item.iddaa_odd.toFixed(2)}
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleSlip({
                        match_id: item.match_id,
                        home_team: item.home_team,
                        away_team: item.away_team,
                        market_key: item.market_key,
                        selection_key: item.selection_key,
                        iddaa_odd: item.iddaa_odd,
                        start_time_utc: item.start_time_utc,
                      });
                    }}
                    className="w-11 h-11 -my-1.5 -mr-1.5 flex items-center justify-center shrink-0"
                    title="Kuponuma ekle"
                    aria-label="Kuponuma ekle"
                  >
                    <span
                      className={`w-8 h-8 rounded-full border flex items-center justify-center transition-all ${
                        inSlip
                          ? "bg-[#00FF85] text-[#05080D] border-[#00FF85] shadow-[0_0_12px_rgba(0,255,133,0.5)]"
                          : "bg-white/[0.04] text-slate-200 border-white/15 hover:border-[#00FF85]/60 hover:text-[#00FF85]"
                      }`}
                    >
                      {inSlip ? <Check className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
                    </span>
                  </button>
                </div>
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}
