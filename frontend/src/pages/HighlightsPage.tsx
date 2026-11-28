import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowRight,
  BarChart3,
  BookmarkPlus,
  CalendarClock,
  Check,
  Flame,
  Grid3X3,
  Layers,
  Link2,
  Plus,
  RotateCcw,
  Search,
  Send,
  Share2,
  Ticket,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { api } from "../api/client";
import type {
  CustomSlipItem,
  DashboardSummary,
  PerformanceData,
  PredictionItem,
  SiteAnnouncement,
  SportotoWeekData,
} from "../types";
import { formatMarketLabel, formatPickWithTeam, formatTurkishPossessive } from "../utils/format";
import {
  DEFAULT_HIGHLIGHTS_FILTER,
  filterAndSortHighlights,
  formatHeroKickoffLabel,
  getDynamicLeagues,
  getDynamicMarketChips,
  getEffectiveModelProb,
  getTodayTrDdMm,
  isItemTodayTr,
  type HighlightsFilterState,
  type SortKey,
} from "../utils/highlightsFilter";
import { analyzeSlipRisk } from "../utils/slipRisk";
import { ConfidenceRing } from "../components/ConfidenceRing";
import { StakeCalculator } from "../components/StakeCalculator";
import { FormStrip, LeagueLogoColumn, TeamBadge } from "../components/TeamBadge";

interface Props {
  summary: DashboardSummary | null;
  onSelectMatch: (matchId: number, predictionId?: number) => void;
  onGoPerformance: () => void;
  onGoCoupons: () => void;
  onGoToto: () => void;
  telegramInviteUrl: string;
  slipItems: CustomSlipItem[];
  onToggleSlip: (item: CustomSlipItem) => void;
  stake: number;
  onChangeStake: (val: number) => void;
  onRemoveSlipItem: (matchId: number) => void;
  onClearSlip: () => void;
  onSaveSlip: () => void;
  onCopySlipLink: () => void;
  onShareSlip: () => void;
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
      label: bd.injury_warning ? "Kadro Avantajı" : "Kadro Sağlam",
    },
  ];
  candidates.sort((a, b) => b.ratio - a.ratio);
  return candidates.slice(0, 2).map((c) => c.label);
}

function toSlipItem(item: PredictionItem): CustomSlipItem {
  return {
    match_id: item.match_id,
    prediction_id: item.prediction_id,
    home_team: item.home_team,
    away_team: item.away_team,
    market_key: item.market_key,
    selection_key: item.selection_key,
    iddaa_odd: item.iddaa_odd,
    model_prob: item.breakdown?.blended_prob_pct ?? item.breakdown?.true_prob_pct ?? null,
    start_time_utc: item.start_time_utc,
  };
}

export function HighlightsPage({
  summary,
  onSelectMatch,
  onGoPerformance,
  onGoCoupons,
  onGoToto,
  telegramInviteUrl,
  slipItems,
  onToggleSlip,
  stake,
  onChangeStake,
  onRemoveSlipItem,
  onClearSlip,
  onSaveSlip,
  onCopySlipLink,
  onShareSlip,
  refreshTrigger,
}: Props) {
  const [filterState, setFilterState] = useState<HighlightsFilterState>(DEFAULT_HIGHLIGHTS_FILTER);
  const [items, setItems] = useState<PredictionItem[]>([]);
  const [perf, setPerf] = useState<PerformanceData | null>(null);
  const [announcements, setAnnouncements] = useState<SiteAnnouncement[]>([]);
  const [totoWeek, setTotoWeek] = useState<SportotoWeekData | null>(null);
  const [activeCouponsCount, setActiveCouponsCount] = useState<number>(0);
  const [communityFavIds, setCommunityFavIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);
  const [slideIdx, setSlideIdx] = useState(0);
  const [tickerPaused, setTickerPaused] = useState(false);
  const prefersReducedMotion = useReducedMotion();

  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    if (items.length === 0) {
      setLoading(true);
    }
    setLoadError(false);

    const fetchPredictions = (attempt: number) => {
      api
        .getPredictions({
          todayOnly: false,
          majorOnly: false,
          minOdd: 1.3,
          minConfidence: 60,
        })
        .then((res) => {
          if (!cancelled) {
            setItems(res.items);
            setLoadError(false);
            setLoading(false);
          }
        })
        .catch(() => {
          if (cancelled) return;
          if (attempt === 0) {
            retryTimer = window.setTimeout(() => {
              if (!cancelled) fetchPredictions(1);
            }, 1800);
          } else {
            setLoadError(true);
            setLoading(false);
          }
        });
    };

    fetchPredictions(0);
    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [refreshTrigger, retryNonce]);

  useEffect(() => {
    let cancelled = false;
    api
      .getPerformance()
      .then((p) => {
        if (!cancelled) setPerf(p);
      })
      .catch(() => {});
    api
      .getActiveAnnouncements()
      .then((rows) => {
        if (!cancelled && Array.isArray(rows)) setAnnouncements(rows);
      })
      .catch(() => {});
    api
      .getCommunityFavorites()
      .then((res) => {
        if (!cancelled && Array.isArray(res.prediction_ids)) {
          setCommunityFavIds(new Set(res.prediction_ids));
        }
      })
      .catch(() => {});
    api
      .getSportotoCurrent()
      .then((res) => {
        if (!cancelled) setTotoWeek(res.active ? res.week : null);
      })
      .catch(() => {});
    api
      .getCoupons()
      .then((res) => {
        if (!cancelled) setActiveCouponsCount(res.coupons?.length || 0);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]);

  const todayDdMmTr = getTodayTrDdMm();
  const todayCount = useMemo(
    () => items.filter((it) => isItemTodayTr(it, todayDdMmTr)).length,
    [items, todayDdMmTr]
  );
  const marketChips = useMemo(() => getDynamicMarketChips(items), [items]);
  const leagueOptions = useMemo(() => getDynamicLeagues(items), [items]);

  const { heroItem, gridItems, totalMatched, hasActiveFilter } = useMemo(
    () => filterAndSortHighlights(items, filterState, todayDdMmTr),
    [items, filterState, todayDdMmTr]
  );

  const slipIds = useMemo(
    () => new Set(slipItems.map((s) => `${s.match_id}_${s.market_key}_${s.selection_key}`)),
    [slipItems]
  );
  const customTotalOdd = useMemo(
    () => Math.round(slipItems.reduce((acc, x) => acc * x.iddaa_odd, 1) * 100) / 100,
    [slipItems]
  );
  const slipRisk = useMemo(() => analyzeSlipRisk(slipItems), [slipItems]);

  const heroReasons = useMemo(() => (heroItem ? getTopReasonChips(heroItem) : []), [heroItem]);

  const slides = useMemo(() => {
    const analyzedTotal = items.length > 0 ? items.length : summary?.active_matches_count ?? null;
    const trustLabel =
      perf && perf.total_settled > 0
        ? `Son ${perf.total_settled} tahminin ${formatTurkishPossessive(perf.won_count)} tuttu`
        : summary?.settled_total
        ? `${summary.settled_total} tahmin sonuçlandı (%${summary.overall_win_rate ?? 0})`
        : loadError || (perf === null && summary === null)
        ? "Veriler güncelleniyor..."
        : "İlk sonuçlar bekleniyor";

    const announcementSlides = announcements.map((ann) => ({
      key: `ann-${ann.id}`,
      icon: "announcement" as const,
      text: ann.text,
      onClick: () => {
        if (!ann.link_url) return;
        if (ann.link_url.startsWith("/")) {
          window.location.href = ann.link_url;
        } else {
          window.open(ann.link_url, "_blank", "noopener,noreferrer");
        }
      },
    }));

    const totoSlides = totoWeek
      ? [
          {
            key: `toto-${totoWeek.id}`,
            icon: "toto" as const,
            text: `Fikstra Toto ${totoWeek.title} yayında · 8–256 kolon şablonlarını hemen kur`,
            onClick: onGoToto,
          },
        ]
      : [];

    const bulletinText =
      analyzedTotal !== null
        ? activeCouponsCount > 0
          ? `${analyzedTotal} analiz · ${activeCouponsCount} hazır kupon →`
          : `${analyzedTotal} analiz · Öne çıkanlara göz at →`
        : activeCouponsCount > 0
        ? `${activeCouponsCount} hazır kupon yayında →`
        : "Günün analizleri ve hazır kuponları →";

    return [
      ...announcementSlides,
      ...totoSlides,
      {
        key: "trust",
        icon: "dots" as const,
        text: trustLabel,
        onClick: onGoPerformance,
      },
      {
        key: "coupons",
        icon: "zap" as const,
        text: bulletinText,
        onClick: onGoCoupons,
      },
      {
        key: "telegram",
        icon: "telegram" as const,
        text: "Telegram grubumuza katıl · Günün kuponları ve maç sonuçları anında cebine gelsin",
        onClick: () => window.open(telegramInviteUrl, "_blank", "noopener,noreferrer"),
      },
    ];
  }, [
    items.length,
    perf,
    announcements,
    totoWeek,
    activeCouponsCount,
    loadError,
    summary,
    onGoPerformance,
    onGoCoupons,
    onGoToto,
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
  const heroInSlip = heroItem
    ? slipIds.has(`${heroItem.match_id}_${heroItem.market_key}_${heroItem.selection_key}`)
    : false;
  const heroProb = heroItem ? Math.round(getEffectiveModelProb(heroItem)) : 0;

  const winRateDisplay =
    perf && perf.total_settled > 0
      ? Math.round((perf.won_count / perf.total_settled) * 100)
      : summary?.overall_win_rate ?? null;
  const settledTotalDisplay = perf?.total_settled ?? summary?.settled_total ?? 0;

  const resetFilters = () => setFilterState(DEFAULT_HIGHLIGHTS_FILTER);

  return (
    <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_360px] lg:gap-5 lg:items-start">
      {/* Left Main Content Area (~68-70%) */}
      <div className="min-w-0 space-y-3.5">
        {/* Hero Card: FİKSTRA'NIN TERCİHİ (only when completely unfiltered) */}
        {heroItem && (
          <div
            role="button"
            tabIndex={0}
            onClick={() => onSelectMatch(heroItem.match_id, heroItem.prediction_id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelectMatch(heroItem.match_id, heroItem.prediction_id);
              }
            }}
            className="w-full text-left relative overflow-hidden rounded-2xl glass-card glass-card-hover neon-bottom-glow transition-all cursor-pointer flex items-stretch"
          >
            <LeagueLogoColumn
              name={heroItem.league}
              country={heroItem.country}
              logoUrl={heroItem.league_logo_url}
            />

            <div className="flex-1 min-w-0 p-4 sm:p-5 space-y-3">
              {/* Top Row: Fixed Brand Title + Real TR Kickoff Time */}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="inline-flex items-center gap-1.5 text-[11px] font-extrabold text-[#00FF85] uppercase tracking-wider shrink-0">
                    <Flame className="w-3.5 h-3.5" />
                    FİKSTRA&apos;NIN TERCİHİ
                  </span>
                  <span className="hidden sm:inline text-[11px] text-slate-400 truncate">
                    · {heroItem.league}
                  </span>
                </div>
                <span className="text-[11px] mono-nums text-slate-200 font-semibold shrink-0 px-2 py-0.5 rounded-full bg-white/[0.05] border border-white/10">
                  {formatHeroKickoffLabel(heroItem, todayDdMmTr)}
                </span>
              </div>

              {/* Middle: Matchup + Pick + Reasons + Tiered Confidence Ring */}
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-2.5">
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

                  <div className="flex flex-wrap items-center gap-1.5">
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

                    {heroProb > 0 && (
                      <span className="px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/25 text-[11px] font-bold mono-nums text-emerald-300">
                        %{heroProb}
                      </span>
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

                <ConfidenceRing score={heroItem.confidence_score} size={62} />
              </div>

              {/* Action Footer */}
              <div className="pt-2.5 border-t border-white/[0.06] flex flex-wrap items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleSlip(toSlipItem(heroItem));
                  }}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold border transition-all ${
                    heroInSlip
                      ? "bg-[#00FF85] text-[#05080D] border-[#00FF85] shadow-[0_0_12px_rgba(0,255,133,0.45)]"
                      : "bg-white/[0.04] text-slate-100 border-white/15 hover:border-[#00FF85]/60 hover:text-[#00FF85]"
                  }`}
                >
                  {heroInSlip ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                  {heroInSlip ? "Kuponda" : "+ Kupona Ekle"}
                </button>

                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectMatch(heroItem.match_id, heroItem.prediction_id);
                  }}
                  className="inline-flex items-center gap-1 text-xs font-bold text-[#00FF85] hover:underline"
                >
                  Analizi Gör
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Animated Rotating Info & Trust Strip */}
        <div
          onMouseEnter={() => setTickerPaused(true)}
          onMouseLeave={() => setTickerPaused(false)}
          onTouchStart={() => setTickerPaused(true)}
          onTouchEnd={() => setTickerPaused(false)}
          className="relative overflow-hidden w-full px-3.5 py-2.5 rounded-xl glass-card hover:border-[#00FF85]/40 transition-colors flex items-center justify-between gap-2 text-xs"
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
                {activeSlide.icon === "announcement" && (
                  <span className="px-1.5 py-0.5 rounded bg-[#00FF85]/20 border border-[#00FF85]/40 text-[#00FF85] text-[9px] font-extrabold tracking-wider shrink-0">
                    DUYURU
                  </span>
                )}
                {activeSlide.icon === "toto" && (
                  <Grid3X3 className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
                )}
                {activeSlide.icon === "telegram" && (
                  <Send className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
                )}
                {activeSlide.icon === "zap" && (
                  <Zap className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
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
          {!prefersReducedMotion && (
            <motion.div
              key={`${activeSlide.key}-${tickerPaused ? "paused" : "run"}`}
              initial={{ scaleX: 0 }}
              animate={tickerPaused ? { scaleX: 0 } : { scaleX: 1 }}
              transition={{ duration: 5, ease: "linear" }}
              style={{ transformOrigin: "left" }}
              className="absolute bottom-0 inset-x-0 h-[2px] bg-gradient-to-r from-[#00FF85]/30 via-[#00FF85] to-emerald-300/60"
            />
          )}
        </div>

        {/* Dynamic Smart Filter Bar */}
        <div className="sticky top-16 z-20 -mx-4 px-4 lg:mx-0 lg:px-3 py-2.5 bg-[#05080D]/92 backdrop-blur-xl border-b lg:border lg:rounded-2xl border-white/[0.06] space-y-2">
          <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-2">
            {/* Left Group: Dynamic Market Chips */}
            <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar pb-0.5 xl:pb-0">
              {marketChips.map((chip) => {
                const active = filterState.market === chip.key;
                return (
                  <button
                    key={chip.key}
                    type="button"
                    onClick={() => setFilterState((prev) => ({ ...prev, market: chip.key }))}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold whitespace-nowrap transition-all shrink-0 ${
                      active
                        ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_14px_-4px_rgba(0,255,133,0.6)]"
                        : "bg-white/[0.04] border border-white/10 text-slate-300 hover:text-white hover:border-white/25"
                    }`}
                  >
                    <span>{chip.label}</span>
                    <span
                      className={`mono-nums text-[10px] ${
                        active ? "text-[#05080D]/80 font-extrabold" : "text-slate-400"
                      }`}
                    >
                      {chip.count}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Right Group: Sadece Bugün + Ligler + Sırala + Arama */}
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => setFilterState((prev) => ({ ...prev, todayOnly: !prev.todayOnly }))}
                className={`inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-[11px] font-bold border transition-all shrink-0 ${
                  filterState.todayOnly
                    ? "bg-[#00FF85]/20 border-[#00FF85]/55 text-[#00FF85]"
                    : "bg-white/[0.04] border-white/10 text-slate-300 hover:text-white"
                }`}
              >
                <CalendarClock className="w-3.5 h-3.5" />
                <span>Sadece Bugün</span>
                <span className="mono-nums opacity-85">({todayCount})</span>
              </button>

              <select
                value={filterState.league}
                onChange={(e) => setFilterState((prev) => ({ ...prev, league: e.target.value }))}
                aria-label="Lig seç"
                className="px-2.5 py-1.5 rounded-xl bg-[#0B111C] border border-white/10 text-[11px] font-semibold text-slate-200 focus:border-[#00FF85]/50 outline-none max-w-[165px]"
              >
                <option value="ALL">Tüm Ligler ({leagueOptions.length})</option>
                {leagueOptions.map((opt) => (
                  <option key={opt.league} value={opt.league}>
                    {opt.league} ({opt.count})
                  </option>
                ))}
              </select>

              <select
                value={filterState.sort}
                onChange={(e) =>
                  setFilterState((prev) => ({ ...prev, sort: e.target.value as SortKey }))
                }
                aria-label="Sıralama seç"
                className="px-2.5 py-1.5 rounded-xl bg-[#0B111C] border border-white/10 text-[11px] font-semibold text-slate-200 focus:border-[#00FF85]/50 outline-none"
              >
                <option value="confidence">Güven (Yüksek→Düşük)</option>
                <option value="time">Saat (En Yakın)</option>
                <option value="odd">Oran (Yüksek→Düşük)</option>
              </select>

              <div className="relative flex-1 min-w-[165px] sm:min-w-[190px]">
                <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  type="text"
                  value={filterState.search}
                  onChange={(e) => setFilterState((prev) => ({ ...prev, search: e.target.value }))}
                  placeholder="Takım veya lig ara..."
                  className="w-full pl-8 pr-7 py-1.5 rounded-xl bg-[#0B111C] border border-white/10 text-xs text-slate-100 placeholder:text-slate-500 focus:border-[#00FF85]/50 outline-none"
                />
                {filterState.search && (
                  <button
                    type="button"
                    onClick={() => setFilterState((prev) => ({ ...prev, search: "" }))}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white"
                    aria-label="Aramayı temizle"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
          </div>

          {hasActiveFilter && (
            <div className="flex items-center justify-between gap-2 pt-1 border-t border-white/[0.05] text-[11px]">
              <span className="text-slate-300 font-medium mono-nums">
                {totalMatched} maç listeleniyor
              </span>
              <button
                type="button"
                onClick={resetFilters}
                className="inline-flex items-center gap-1 text-[#00FF85] font-bold hover:underline"
              >
                <RotateCcw className="w-3 h-3" />
                Filtreleri Temizle
              </button>
            </div>
          )}
        </div>

        {/* Match Cards */}
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[1, 2, 3, 4].map((n) => (
              <div key={n} className="h-36 rounded-2xl glass-card animate-pulse" />
            ))}
          </div>
        ) : loadError ? (
          <div className="p-8 text-center rounded-2xl glass-card border border-amber-500/20">
            <p className="text-sm font-semibold text-slate-100">
              Sunucu hazırlanıyor veya bağlantı geçici olarak kesildi
            </p>
            <p className="text-xs text-slate-400 mt-1">
              Veriler alınırken geçici bir sunucu hatası oluştu. Birkaç saniye sonra tekrar deneyebilirsiniz.
            </p>
            <button
              type="button"
              onClick={() => {
                setLoadError(false);
                setLoading(true);
                setRetryNonce((n) => n + 1);
              }}
              className="mt-4 inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-[#00FF85] text-[#05080D] text-xs font-extrabold hover:brightness-110 transition-all"
            >
              Tekrar Dene
            </button>
          </div>
        ) : gridItems.length === 0 && !heroItem ? (
          <div className="p-8 text-center rounded-2xl glass-card space-y-3">
            <p className="text-sm font-semibold text-slate-200">
              Bu filtreye uyan maç bulunamadı
            </p>
            <p className="text-xs text-slate-400">
              Arama terimini değiştirebilir veya aktif filtreleri tek tıkla sıfırlayabilirsiniz.
            </p>
            {hasActiveFilter && (
              <button
                type="button"
                onClick={resetFilters}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-[#00FF85] text-[#05080D] text-xs font-extrabold hover:brightness-110 transition-all"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Filtreleri Temizle
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {gridItems.map((item, idx) => {
              const inSlip = slipIds.has(`${item.match_id}_${item.market_key}_${item.selection_key}`);
              const isElite = item.confidence_score >= 75;
              const probPct = Math.round(getEffectiveModelProb(item));

              return (
                <motion.div
                  key={item.prediction_id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(idx * 0.02, 0.2) }}
                  onClick={() => onSelectMatch(item.match_id, item.prediction_id)}
                  className="relative cursor-pointer rounded-2xl glass-card glass-card-hover transition-all overflow-hidden flex items-stretch"
                >
                  {/* Left Prominent 48px League Logo Column */}
                  <LeagueLogoColumn
                    name={item.league}
                    country={item.country}
                    logoUrl={item.league_logo_url}
                  />

                  <div className="flex-1 min-w-0 p-3.5 sm:p-4 flex flex-col justify-between">
                    {/* Top Meta: League Name | Community Fav + Date & Time */}
                    <div className="flex items-center justify-between gap-2 mb-2.5">
                      <span className="text-[11px] font-medium text-slate-400 truncate">
                        {item.league}
                      </span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {communityFavIds.has(item.prediction_id) && (
                          <span className="px-1.5 py-0.5 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-[#00FF85] text-[9px] font-bold">
                            Favori
                          </span>
                        )}
                        <span className="text-[11px] mono-nums text-slate-300 font-semibold">
                          {item.start_date_tr} {item.start_time_tr}
                        </span>
                      </div>
                    </div>

                    {/* Middle: Home & Away Teams + Dot Form + Tiered Confidence Ring */}
                    <div className="flex items-center justify-between gap-2.5">
                      <div className="space-y-2 min-w-0 flex-1">
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

                    {/* Bottom: Pick/Odd Pill + Model Prob + 44x44 Touch Target + Button */}
                    <div className="mt-3 pt-2.5 border-t border-white/[0.06] flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <div
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#00FF85]/10 border border-[#00FF85]/45 min-w-0 ${
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
                        {probPct > 0 && (
                          <span className="px-1.5 py-0.5 rounded-md bg-white/[0.05] border border-white/10 text-[10px] font-bold mono-nums text-emerald-300 shrink-0">
                            %{probPct}
                          </span>
                        )}
                      </div>

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleSlip(toSlipItem(item));
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
                  </div>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>

      {/* Right Sticky SaaS Panel (Desktop lg+ only) */}
      <aside className="hidden lg:block lg:sticky lg:top-20 space-y-4">
        {/* 1. Live Kuponum Fişi */}
        <div className="rounded-2xl glass-card p-4 space-y-3.5 border-[#00FF85]/25">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Ticket className="w-4 h-4 text-[#00FF85]" />
              <h2 className="text-sm font-extrabold text-white">Kuponum Fişi</h2>
            </div>
            {slipItems.length > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-[#00FF85]/15 border border-[#00FF85]/40 text-[#00FF85] text-[11px] font-extrabold mono-nums">
                {slipItems.length} Maç · {customTotalOdd.toFixed(2)}
              </span>
            )}
          </div>

          {slipItems.length === 0 ? (
            <div className="p-3.5 rounded-xl bg-[#0B111C]/80 border border-white/[0.06] space-y-2.5 text-center">
              <p className="text-xs text-slate-300 leading-relaxed">
                Maç kartlarındaki <strong className="text-[#00FF85]">+</strong> butonuna tıklayarak
                kendi özel kuponunu anında oluşturabilirsin.
              </p>
              <button
                type="button"
                onClick={onGoCoupons}
                className="w-full py-2 rounded-xl bg-[#00FF85]/15 border border-[#00FF85]/35 text-[#00FF85] text-xs font-bold hover:bg-[#00FF85]/25 transition-colors inline-flex items-center justify-center gap-1.5"
              >
                <Layers className="w-3.5 h-3.5" />
                Hazır Kuponları Gör
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2">
                <span
                  className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold border mono-nums ${
                    slipRisk.riskTone === "emerald"
                      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/35"
                      : slipRisk.riskTone === "amber"
                      ? "bg-amber-500/15 text-amber-300 border-amber-500/35"
                      : "bg-rose-500/15 text-rose-300 border-rose-500/35"
                  }`}
                >
                  {slipRisk.riskLabel} · %{slipRisk.combinedPct}
                </span>
                <button
                  type="button"
                  onClick={onClearSlip}
                  className="text-[11px] text-slate-400 hover:text-rose-400 inline-flex items-center gap-1 font-semibold"
                >
                  <Trash2 className="w-3 h-3" />
                  Temizle
                </button>
              </div>

              <div className="space-y-1.5 max-h-60 overflow-y-auto pr-0.5">
                {slipItems.map((s) => (
                  <div
                    key={s.match_id}
                    className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-xl bg-[#0B111C] border border-white/[0.07] text-xs"
                  >
                    <div className="min-w-0">
                      <p className="font-semibold text-slate-100 truncate">
                        {s.home_team} - {s.away_team}
                      </p>
                      <p className="text-[11px] text-[#00FF85] font-bold">
                        {formatMarketLabel(s.market_key, s.selection_key)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="mono-nums font-extrabold text-white">
                        {s.iddaa_odd.toFixed(2)}
                      </span>
                      <button
                        type="button"
                        onClick={() => onRemoveSlipItem(s.match_id)}
                        className="w-6 h-6 rounded-lg bg-white/5 text-slate-400 hover:text-rose-400 flex items-center justify-center"
                        aria-label="Çıkar"
                      >
                        ×
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              {slipRisk.weakestItem &&
                slipRisk.weakestPct !== null &&
                slipRisk.withoutWeakestPct !== null && (
                  <div className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-xl bg-amber-500/10 border border-amber-500/25 text-[11px] text-amber-200">
                    <span className="truncate">
                      En zayıf:{" "}
                      <strong className="text-white">{slipRisk.weakestItem.home_team}</strong>{" "}
                      <span className="mono-nums">
                        (%{slipRisk.weakestPct} → %{slipRisk.withoutWeakestPct})
                      </span>
                    </span>
                    <button
                      type="button"
                      onClick={() => onRemoveSlipItem(slipRisk.weakestItem!.match_id)}
                      className="px-2 py-0.5 rounded-lg bg-amber-500/20 hover:bg-rose-500/25 text-amber-100 hover:text-rose-200 font-bold shrink-0 text-[10px]"
                    >
                      Çıkar
                    </button>
                  </div>
                )}

              <StakeCalculator
                totalOdd={customTotalOdd}
                stake={stake}
                onChangeStake={onChangeStake}
                compact
              />

              <div className="grid grid-cols-2 gap-1.5">
                <button
                  type="button"
                  onClick={onCopySlipLink}
                  className="inline-flex items-center justify-center gap-1 py-2 rounded-xl glass-card text-slate-200 hover:border-[#00FF85]/45 text-[11px] font-bold"
                >
                  <Link2 className="w-3.5 h-3.5 text-[#00FF85]" />
                  Linkle Gönder
                </button>
                <button
                  type="button"
                  onClick={onSaveSlip}
                  className="inline-flex items-center justify-center gap-1 py-2 rounded-xl bg-emerald-500/15 border border-emerald-500/35 text-[#00FF85] text-[11px] font-bold hover:bg-emerald-500/25"
                >
                  <BookmarkPlus className="w-3.5 h-3.5" />
                  Takibe Al
                </button>
                <button
                  type="button"
                  onClick={onShareSlip}
                  className="col-span-2 inline-flex items-center justify-center gap-1.5 py-2 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-extrabold"
                >
                  <Share2 className="w-3.5 h-3.5" />
                  Kuponu Paylaş
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 2. Live Performance & Ready Coupons Summary */}
        <div className="rounded-2xl glass-card p-4 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-[#00FF85]" />
              <h2 className="text-sm font-extrabold text-white">Performans &amp; Kuponlar</h2>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="p-2.5 rounded-xl bg-[#0B111C] border border-white/[0.06]">
              <p className="text-[10px] text-slate-400 font-medium">Genel Başarı</p>
              <p className="text-base font-extrabold mono-nums text-[#00FF85] mt-0.5">
                {winRateDisplay !== null ? `%${winRateDisplay}` : "—"}
              </p>
              <p className="text-[10px] text-slate-500 mono-nums">
                {settledTotalDisplay} sonuçlanan
              </p>
            </div>
            <div className="p-2.5 rounded-xl bg-[#0B111C] border border-white/[0.06]">
              <p className="text-[10px] text-slate-400 font-medium">Hazır Kuponlar</p>
              <p className="text-base font-extrabold mono-nums text-white mt-0.5">
                {activeCouponsCount}
              </p>
              <p className="text-[10px] text-slate-500">aktif kupon</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={onGoCoupons}
              className="py-2 px-2.5 rounded-xl bg-white/[0.04] border border-white/10 hover:border-[#00FF85]/45 text-xs font-bold text-slate-200 hover:text-[#00FF85] transition-colors inline-flex items-center justify-center gap-1"
            >
              Kuponlara Git
            </button>
            <button
              type="button"
              onClick={onGoPerformance}
              className="py-2 px-2.5 rounded-xl bg-white/[0.04] border border-white/10 hover:border-[#00FF85]/45 text-xs font-bold text-slate-200 hover:text-[#00FF85] transition-colors inline-flex items-center justify-center gap-1"
            >
              Performansı Gör
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}
