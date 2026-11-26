import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  Plus,
  RefreshCw,
  Share2,
  Sparkles,
  TrendingDown,
  X,
} from "lucide-react";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { CustomSlipItem, MatchDetail, PulseData } from "../types";
import { formatMarketLabel, formatPickWithTeam } from "../utils/format";
import { generateMatchSharePng } from "../utils/matchShare";
import { ConfidenceRing } from "./ConfidenceRing";
import { FormStrip, LeagueBadge, TeamBadge } from "./TeamBadge";

const INJURY_TR_MAP: [string, string][] = [
  ["cruciate ligament tear", "Çapraz Bağ Yırtığı"],
  ["cruciate ligament", "Çapraz Bağ Sakatlığı"],
  ["syndesmotic ligament tear", "Sindesmoz Bağ Yırtığı"],
  ["ligament tear", "Bağ Yırtığı"],
  ["torn muscle fiber", "Kas Lifi Yırtığı"],
  ["torn muscle", "Kas Yırtığı"],
  ["partial muscle tear", "Kısmi Kas Yırtığı"],
  ["muscle tear", "Kas Yırtığı"],
  ["hamstring injury", "Arka Adale"],
  ["hamstring", "Arka Adale"],
  ["muscle injury", "Kas Sakatlığı"],
  ["adductor", "Kasık / Adduktor"],
  ["groin injury", "Kasık"],
  ["groin", "Kasık"],
  ["knee injury", "Diz"],
  ["knee", "Diz"],
  ["ankle injury", "Ayak Bileği"],
  ["ankle", "Ayak Bileği"],
  ["calf injury", "Baldır"],
  ["calf", "Baldır"],
  ["broken leg", "Bacak Kırığı"],
  ["cartilage damage", "Kıkırdak Hasarı"],
  ["concussion", "Beyin Sarsıntısı"],
  ["fracture", "Kırık"],
  ["hip", "Kalça"],
  ["sprain", "Burkulma"],
  ["strain", "Zorlanma"],
  ["red card suspension", "Kırmızı Kart Cezalı"],
  ["yellow card suspension", "Sarı Kart Cezalı"],
  ["suspension", "Cezalı"],
  ["unknown injury", "Sakatlık"],
  ["illness", "Hastalık"],
  ["fitness", "Kondisyon Eksikliği"],
  ["shoulder", "Omuz"],
  ["back injury", "Bel/Sırt"],
  ["thigh", "Uyluk"],
  ["foot", "Ayak"],
  ["achilles", "Aşil Tendonu"],
];

function translateInjuryReason(raw?: string | null): string {
  if (!raw) return "Sakatlık";
  const low = raw.trim().toLowerCase();
  for (const [en, tr] of INJURY_TR_MAP) {
    if (low.includes(en)) return tr;
  }
  return raw;
}

interface CouponPickContext {
  market_key: string;
  selection_key: string;
  iddaa_odd: number;
}

interface Props {
  matchId: number | null;
  couponPick?: CouponPickContext;
  onClose: () => void;
  onSettledDetected?: (detail: MatchDetail) => void;
  slipItems?: CustomSlipItem[];
  onToggleSlip?: (item: CustomSlipItem) => void;
}

export function MatchDetailDrawer({
  matchId,
  couponPick,
  onClose,
  onSettledDetected,
  slipItems = [],
  onToggleSlip,
}: Props) {
  const [detail, setDetail] = useState<MatchDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiExpanded, setAiExpanded] = useState(false);
  const [injuriesOpen, setInjuriesOpen] = useState(false);
  const [pulse, setPulse] = useState<PulseData | null>(null);
  const [pulseVoting, setPulseVoting] = useState(false);
  const [modelOpen, setModelOpen] = useState(false);
  const [oddsOpen, setOddsOpen] = useState(false);
  const [shareSheetOpen, setShareSheetOpen] = useState(false);
  const [sharePreview, setSharePreview] = useState<{
    dataUrl: string;
    blob: Blob;
    filename: string;
  } | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const [shareStatus, setShareStatus] = useState<string | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);

  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Lock body scroll & support Android back button via history.pushState / popstate
  useEffect(() => {
    if (matchId === null) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const stateMarker = { fikstraDrawer: matchId };
    window.history.pushState(stateMarker, "");
    let closedByPop = false;

    const onPopState = () => {
      closedByPop = true;
      onCloseRef.current();
    };
    window.addEventListener("popstate", onPopState);

    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("popstate", onPopState);
      if (!closedByPop && window.history.state?.fikstraDrawer === matchId) {
        window.history.back();
      }
    };
  }, [matchId]);

  useEffect(() => {
    if (!matchId) {
      setDetail(null);
      setError(null);
      return;
    }
    setError(null);
    setAiExpanded(false);
    setInjuriesOpen(false);
    setModelOpen(false);
    setOddsOpen(false);
    setShareSheetOpen(false);
    setSharePreview(null);
    setShareStatus(null);
    setLinkCopied(false);
    setPulse(null);
    let cancelled = false;
    setLoading(true);
    api
      .getMatchDetail(matchId, couponPick?.market_key, couponPick?.selection_key)
      .then((d) => {
        if (cancelled) return;
        const predStatus = (d.top_prediction?.status || "").toUpperCase();
        if (
          onSettledDetected &&
          (d.status === "FINISHED" || predStatus === "WON" || predStatus === "LOST" || predStatus === "VOID")
        ) {
          onSettledDetected(d);
          return;
        }
        setDetail(d);
        if (d.top_prediction?.prediction_id) {
          api.getPulse(d.top_prediction.prediction_id).then((p) => {
            if (!cancelled) setPulse(p);
          }).catch(() => {});
        }
        if (!d.context.ai_summary) {
          setAiLoading(true);
          api
            .generateAiSummary(d.match_id, false)
            .then((res) => {
              if (!cancelled) {
                setDetail((prev) =>
                  prev
                    ? {
                        ...prev,
                        context: {
                          ...prev.context,
                          ai_summary: res.ai_summary,
                          ai_source: res.ai_source,
                        },
                      }
                    : prev
                );
              }
            })
            .catch(() => {})
            .finally(() => {
              if (!cancelled) setAiLoading(false);
            });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDetail(null);
          setError("Bu maç bulunamadı veya kaldırıldı.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [matchId, couponPick?.market_key, couponPick?.selection_key]);

  const handleGenerateAi = async () => {
    if (!detail) return;
    setAiLoading(true);
    try {
      const res = await api.generateAiSummary(detail.match_id, false);
      setDetail({
        ...detail,
        context: {
          ...detail.context,
          ai_summary: res.ai_summary,
          ai_source: res.ai_source,
        },
      });
    } finally {
      setAiLoading(false);
    }
  };

  const historyKeys = detail?.odds_history?.length
    ? Object.keys(detail.odds_history[0]).filter((k) => k !== "time")
    : [];

  const activePick = couponPick ?? detail?.top_prediction ?? null;
  const inSlip = Boolean(
    detail &&
      activePick &&
      slipItems.some(
        (s) =>
          s.match_id === detail.match_id &&
          s.market_key === activePick.market_key &&
          s.selection_key === activePick.selection_key
      )
  );

  const isRecommendedCell = (mk: string, sk: string) =>
    Boolean(
      (detail?.top_prediction &&
        detail.top_prediction.market_key === mk &&
        detail.top_prediction.selection_key === sk) ||
        (couponPick && couponPick.market_key === mk && couponPick.selection_key === sk)
    );

  const openShareSheet = async () => {
    if (!detail || !activePick) return;
    setShareSheetOpen(true);
    setShareStatus(null);
    setLinkCopied(false);
    setShareLoading(true);
    try {
      const res = await generateMatchSharePng({
        matchId: detail.match_id,
        league: detail.league,
        startDateTr: detail.start_date_tr,
        startTimeTr: detail.start_time_tr,
        homeTeam: detail.home_team,
        awayTeam: detail.away_team,
        homeLogoUrl: detail.home_logo_url,
        awayLogoUrl: detail.away_logo_url,
        marketKey: activePick.market_key,
        selectionKey: activePick.selection_key,
        iddaaOdd: activePick.iddaa_odd,
        confidenceScore: detail.top_prediction?.confidence_score ?? 70,
        valueEdgePct: detail.top_prediction?.value_edge_pct ?? null,
        oddsDropPct: detail.top_prediction?.odds_drop_pct ?? null,
        breakdown: detail.top_prediction?.breakdown ?? null,
      });
      setSharePreview(res);
    } catch {
      setShareStatus("Görsel oluşturulurken bir hata oluştu.");
    } finally {
      setShareLoading(false);
    }
  };

  const handleDownloadMatchPng = () => {
    if (!sharePreview) return;
    const a = document.createElement("a");
    a.href = sharePreview.dataUrl;
    a.download = sharePreview.filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setShareStatus("PNG görseli indirildi.");
  };

  const handleNativeShareMatch = async () => {
    if (!sharePreview || !detail) return;
    const file = new File([sharePreview.blob], sharePreview.filename, { type: "image/png" });
    const matchUrl = `${window.location.origin}/?match=${detail.match_id}`;
    if (navigator.share && navigator.canShare?.({ files: [file] })) {
      try {
        await navigator.share({
          files: [file],
          title: `Fikstra • ${detail.home_team} - ${detail.away_team}`,
          text: `${detail.home_team} - ${detail.away_team} analiz kartı`,
          url: matchUrl,
        });
        return;
      } catch (err) {
        if ((err as Error)?.name === "AbortError") return;
      }
    }
    try {
      if (navigator.clipboard && typeof ClipboardItem !== "undefined") {
        await navigator.clipboard.write([new ClipboardItem({ "image/png": sharePreview.blob })]);
        setShareStatus("Görsel panoya kopyalandı.");
        return;
      }
    } catch {
      // fall back to download
    }
    handleDownloadMatchPng();
  };

  const handleCopyMatchLink = async () => {
    if (!detail) return;
    const matchUrl = `${window.location.origin}/?match=${detail.match_id}`;
    try {
      await navigator.clipboard.writeText(matchUrl);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 2000);
    } catch {
      setShareStatus(matchUrl);
    }
  };

  const auRows = (["AU_15", "AU_25", "AU_35"] as const)
    .map((mk) => ({
      mk,
      label: mk === "AU_15" ? "1.5 Gol" : mk === "AU_25" ? "2.5 Gol" : "3.5 Gol",
      alt: detail?.odds.find((o) => o.market_key === mk && o.selection_key === "ALT"),
      ust: detail?.odds.find((o) => o.market_key === mk && o.selection_key === "UST"),
    }))
    .filter((r) => r.alt || r.ust);

  return (
    <AnimatePresence>
      {matchId !== null && (
        <motion.div
          className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-black/75 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className="relative w-full md:max-w-2xl max-h-[92vh] flex flex-col rounded-t-3xl md:rounded-2xl bg-[#070B12]/98 backdrop-blur-2xl border border-emerald-500/25 shadow-[0_0_40px_-10px_rgba(0,255,133,0.22)] overflow-hidden"
            initial={{ y: 48, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 48, opacity: 0 }}
            transition={{ type: "spring", damping: 28, stiffness: 300 }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="shrink-0 flex items-center justify-between px-5 py-4 bg-[#070B12]/95 backdrop-blur-xl border-b border-emerald-500/15">
              <div className="min-w-0">
                <p className="text-[11px] font-medium text-cyan-400 uppercase tracking-wider flex items-center gap-1.5">
                  {detail && <LeagueBadge name={detail.league} logoUrl={detail.league_logo_url} />}
                  <span className="truncate">
                    {detail
                      ? `${detail.league} • ${detail.start_date_tr} ${detail.start_time_tr}`
                      : "Maç Detayı"}
                  </span>
                </p>
                <h3 className="text-base font-bold text-slate-100 mt-0.5 truncate">
                  {detail
                    ? `${detail.home_team} vs ${detail.away_team}`
                    : error
                    ? "Maç Bulunamadı"
                    : "Yükleniyor..."}
                </h3>
              </div>
              <button
                onClick={onClose}
                className="p-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400 hover:text-white shrink-0"
                aria-label="Kapat"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Scrollable Body: Decision First, Evidence Second */}
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {error ? (
                <div className="p-8 text-center space-y-4">
                  <p className="text-sm font-medium text-slate-300">{error}</p>
                  <button
                    type="button"
                    onClick={onClose}
                    className="px-4 py-2 rounded-xl bg-[#131926] border border-[#1F2937] hover:border-slate-500 text-xs font-bold text-slate-200"
                  >
                    Kapat
                  </button>
                </div>
              ) : loading || !detail ? (
                <div className="p-8 text-center text-sm text-slate-400">Veriler yükleniyor...</div>
              ) : (
                <>
                  {/* 1) Kupon Tercihi Band (if opened from a coupon) */}
                  {couponPick && (
                    <div className="p-3.5 rounded-2xl bg-emerald-500/12 border border-emerald-500/35 flex flex-col gap-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-extrabold text-emerald-300">
                          Kupon Tercihi:{" "}
                          {formatPickWithTeam(
                            couponPick.market_key,
                            couponPick.selection_key,
                            detail.home_team,
                            detail.away_team
                          )}{" "}
                          @ <span className="mono-nums text-white">{couponPick.iddaa_odd.toFixed(2)}</span>
                        </span>
                        <span className="px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-300 text-[10px] font-bold">
                          Kupon Ayağı
                        </span>
                      </div>
                      {detail.top_prediction &&
                        (detail.top_prediction.market_key !== couponPick.market_key ||
                          detail.top_prediction.selection_key !== couponPick.selection_key) && (
                          <p className="text-[11px] text-slate-300">
                            Maçın #1 Tercihi:{" "}
                            <span className="font-semibold text-cyan-300">
                              {formatPickWithTeam(
                                detail.top_prediction.market_key,
                                detail.top_prediction.selection_key,
                                detail.home_team,
                                detail.away_team
                              )}{" "}
                              @ {detail.top_prediction.iddaa_odd.toFixed(2)}
                            </span>
                          </p>
                        )}
                    </div>
                  )}

                  {/* 2) Pick + Large Confidence Ring + 4 Short Score Bars */}
                  {detail.top_prediction && (
                    <div className="p-4 rounded-2xl bg-[#0B0F17] border border-[#1F2937] space-y-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <span className="text-[11px] text-slate-400">Öne Çıkan Tercih</span>
                          <div className="flex items-center gap-2 mt-1 flex-wrap">
                            <span className="px-2.5 py-1 rounded-lg bg-[#00FF85]/15 border border-[#00FF85]/40 text-[#00FF85] text-xs font-bold">
                              {formatPickWithTeam(
                                detail.top_prediction.market_key,
                                detail.top_prediction.selection_key,
                                detail.home_team,
                                detail.away_team
                              )}
                            </span>
                            <span className="text-lg font-extrabold mono-nums text-white">
                              {detail.top_prediction.iddaa_odd.toFixed(2)}
                            </span>
                            {detail.top_prediction.global_odd && (
                              <span className="text-xs text-slate-400 mono-nums">
                                Global {detail.top_prediction.global_odd.toFixed(2)}
                              </span>
                            )}
                          </div>
                          {detail.top_prediction.breakdown.blended_prob_pct != null && (
                            <p className="text-[11px] text-slate-400 mono-nums mt-1.5">
                              Model Olasılığı: %
                              {detail.top_prediction.breakdown.blended_prob_pct ??
                                detail.top_prediction.breakdown.true_prob_pct}
                            </p>
                          )}
                        </div>
                        <ConfidenceRing score={detail.top_prediction.confidence_score} size={58} />
                      </div>

                      <div className="grid grid-cols-2 gap-3 pt-2 border-t border-[#1F2937]">
                        {[
                          {
                            label: "Global Değer",
                            val: detail.top_prediction.breakdown.value,
                            max: 35,
                            color: "bg-emerald-500",
                          },
                          {
                            label: "Oran Hareketi",
                            val: detail.top_prediction.breakdown.odds_move,
                            max: 25,
                            color: "bg-cyan-500",
                          },
                          {
                            label: "Form & Gol",
                            val: detail.top_prediction.breakdown.form_goals,
                            max: 25,
                            color: "bg-amber-500",
                          },
                          {
                            label: "Sakatlık Etkisi",
                            val: detail.top_prediction.breakdown.injury,
                            max: 15,
                            color: "bg-indigo-500",
                          },
                        ].map((bar) => (
                          <div key={bar.label}>
                            <div className="flex justify-between text-[11px] mb-1">
                              <span className="text-slate-400">{bar.label}</span>
                              <span className="mono-nums text-slate-200 font-semibold">
                                {bar.val}/{bar.max}
                              </span>
                            </div>
                            <div className="h-1.5 rounded-full bg-[#1F2937] overflow-hidden">
                              <div
                                className={`h-full rounded-full ${bar.color}`}
                                style={{ width: `${Math.min(100, (bar.val / bar.max) * 100)}%` }}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Bu tahmine katılıyor musun? (5 oy kilitli nabız barı) */}
                  {detail.top_prediction && (
                    <div className="px-3.5 py-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] space-y-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] font-bold text-slate-300">
                            Bu tahmine katılıyor musun?
                          </span>
                          {pulse?.is_community_favorite && (
                            <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-[#00FF85] text-[9px] font-bold">
                              Topluluk Favorisi
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            disabled={pulseVoting}
                            onClick={async () => {
                              if (!detail.top_prediction) return;
                              setPulseVoting(true);
                              try {
                                const updated = await api.votePulse(
                                  detail.top_prediction.prediction_id,
                                  "agree"
                                );
                                setPulse(updated);
                              } finally {
                                setPulseVoting(false);
                              }
                            }}
                            className={`px-2.5 py-1 rounded-lg text-[11px] font-bold border transition-colors ${
                              pulse?.user_vote === "agree"
                                ? "bg-[#00FF85]/20 border-[#00FF85]/60 text-[#00FF85]"
                                : "bg-[#131926] border-[#1F2937] text-slate-300 hover:border-[#00FF85]/40"
                            }`}
                          >
                            Katılıyorum
                          </button>
                          <button
                            type="button"
                            disabled={pulseVoting}
                            onClick={async () => {
                              if (!detail.top_prediction) return;
                              setPulseVoting(true);
                              try {
                                const updated = await api.votePulse(
                                  detail.top_prediction.prediction_id,
                                  "risky"
                                );
                                setPulse(updated);
                              } finally {
                                setPulseVoting(false);
                              }
                            }}
                            className={`px-2.5 py-1 rounded-lg text-[11px] font-bold border transition-colors ${
                              pulse?.user_vote === "risky"
                                ? "bg-amber-500/20 border-amber-500/60 text-amber-300"
                                : "bg-[#131926] border-[#1F2937] text-slate-300 hover:border-amber-500/40"
                            }`}
                          >
                            Riskli
                          </button>
                        </div>
                      </div>

                      {pulse && !pulse.revealed && pulse.user_vote && (
                        <p className="text-[10px] text-slate-400">
                          Oyun alındı · Topluluk oranı 5 oyda açılır ({pulse.total_votes}/5)
                        </p>
                      )}

                      {pulse && pulse.revealed && pulse.agree_pct !== null && pulse.risky_pct !== null && (
                        <div className="space-y-1">
                          <div className="flex items-center justify-between text-[10px] mono-nums text-slate-300">
                            <span className="text-[#00FF85] font-bold">
                              %{pulse.agree_pct} Katılıyor
                            </span>
                            <span className="text-slate-400">
                              %{pulse.risky_pct} Riskli · {pulse.total_votes} oy
                            </span>
                          </div>
                          <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden flex">
                            <div
                              className="h-full bg-[#00FF85]"
                              style={{ width: `${pulse.agree_pct}%` }}
                            />
                            <div
                              className="h-full bg-slate-500"
                              style={{ width: `${pulse.risky_pct}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 3) AI Summary (3 lines + Devamını oku) */}
                  <div className="p-4 rounded-2xl bg-gradient-to-br from-cyan-500/10 via-[#0B0F17] to-emerald-500/10 border border-cyan-500/25">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2 text-xs font-semibold text-cyan-300">
                        <Sparkles className="w-4 h-4" />
                        <span>Maç Analiz Özeti</span>
                        {detail.context.ai_source && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300">
                            {detail.context.ai_source === "gemini"
                              ? "Fikstra Maç Özeti"
                              : "Fikstra Analiz Özeti"}
                          </span>
                        )}
                      </div>
                      {!detail.context.ai_summary && (
                        <button
                          onClick={handleGenerateAi}
                          disabled={aiLoading}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 text-xs font-bold disabled:opacity-50"
                        >
                          {aiLoading ? (
                            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Sparkles className="w-3.5 h-3.5" />
                          )}
                          Maç Özetini Getir
                        </button>
                      )}
                    </div>
                    <p
                      className={`text-xs leading-relaxed text-slate-300 ${
                        aiExpanded ? "" : "line-clamp-3"
                      }`}
                    >
                      {detail.context.ai_summary ||
                        "Bu maç için istatistik özeti henüz üretilmedi. Tek dokunuşla üretebilirsiniz."}
                    </p>
                    {detail.context.ai_summary && detail.context.ai_summary.length > 170 && (
                      <button
                        type="button"
                        onClick={() => setAiExpanded((v) => !v)}
                        className="mt-1.5 text-[11px] font-bold text-cyan-300 hover:underline"
                      >
                        {aiExpanded ? "Daha az" : "Devamını oku"}
                      </button>
                    )}
                  </div>

                  {/* 4) Form & Goal Averages (Lettered boxes kept here!) */}
                  <div className="p-4 rounded-2xl bg-[#0B0F17] border border-[#1F2937] space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                        Son 5 Maç Formu & Gol Ortalamaları
                      </h4>
                      <span className="text-[10px] text-slate-500">En yeni solda</span>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      {[
                        {
                          team: detail.home_team,
                          logo: detail.home_logo_url,
                          form: detail.context.home_form,
                          recentMatches: detail.context.home_recent_matches || [],
                        },
                        {
                          team: detail.away_team,
                          logo: detail.away_logo_url,
                          form: detail.context.away_form,
                          recentMatches: detail.context.away_recent_matches || [],
                        },
                      ].map((side) => {
                        const top5 = side.recentMatches.slice(0, 5);
                        const displayedForm =
                          top5.length > 0 ? top5.map((r) => r.result) : side.form;
                        const gfAvg =
                          top5.length > 0
                            ? (
                                top5.reduce(
                                  (acc, r) => acc + (r.is_home ? r.home_score : r.away_score),
                                  0
                                ) / top5.length
                              ).toFixed(2)
                            : null;
                        const gaAvg =
                          top5.length > 0
                            ? (
                                top5.reduce(
                                  (acc, r) => acc + (r.is_home ? r.away_score : r.home_score),
                                  0
                                ) / top5.length
                              ).toFixed(2)
                            : null;

                        return (
                          <div
                            key={side.team}
                            className="p-3 rounded-xl bg-[#131926] border border-[#1F2937] space-y-2 min-w-0"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2 min-w-0">
                                <TeamBadge name={side.team} logoUrl={side.logo} />
                                <p className="text-xs font-semibold text-slate-200 truncate">
                                  {side.team}
                                </p>
                              </div>
                              <FormStrip form={displayedForm} variant="boxes" />
                            </div>
                            {top5.length > 0 ? (
                              <>
                                <div className="flex items-center justify-between text-[11px] text-slate-400 mono-nums pt-1 border-t border-[#1F2937]/70">
                                  <span>
                                    Atılan: <strong className="text-emerald-400">{gfAvg}</strong>
                                  </span>
                                  <span>
                                    Yenilen: <strong className="text-rose-400">{gaAvg}</strong>
                                  </span>
                                </div>
                                <div className="pt-1.5 border-t border-[#1F2937]/60 space-y-1.5">
                                  {top5.map((rm, rIdx) => {
                                    const resBadge =
                                      rm.result === "W"
                                        ? { lbl: "G", cls: "bg-emerald-500/20 text-emerald-400" }
                                        : rm.result === "D"
                                        ? { lbl: "B", cls: "bg-amber-500/20 text-amber-400" }
                                        : { lbl: "M", cls: "bg-rose-500/20 text-rose-400" };
                                    return (
                                      <div
                                        key={`${rm.event_id}-${rIdx}`}
                                        className="flex items-center gap-1.5 text-[10px] min-w-0"
                                        title={rm.competition ? `${rm.competition}: ${rm.home_team} ${rm.home_score}-${rm.away_score} ${rm.away_team}` : undefined}
                                      >
                                        <span className="text-slate-500 mono-nums shrink-0 w-8">
                                          {rm.date_tr}
                                        </span>
                                        <div className="flex-1 min-w-0 grid grid-cols-[1fr_auto_1fr] items-center gap-1">
                                          <span
                                            className={`truncate text-right ${
                                              rm.is_home
                                                ? "text-white font-semibold"
                                                : "text-slate-400"
                                            }`}
                                          >
                                            {rm.home_team}
                                          </span>
                                          <span className="px-1 py-0.5 rounded bg-white/[0.05] text-slate-100 font-bold mono-nums shrink-0">
                                            {rm.home_score}-{rm.away_score}
                                          </span>
                                          <span
                                            className={`truncate text-left ${
                                              !rm.is_home
                                                ? "text-white font-semibold"
                                                : "text-slate-400"
                                            }`}
                                          >
                                            {rm.away_team}
                                          </span>
                                        </div>
                                        <span
                                          className={`w-4 h-4 rounded-[4px] font-bold flex items-center justify-center shrink-0 ${resBadge.cls}`}
                                        >
                                          {resBadge.lbl}
                                        </span>
                                      </div>
                                    );
                                  })}
                                </div>
                              </>
                            ) : (
                              <p className="text-[10px] text-slate-500 pt-1.5 border-t border-[#1F2937]/60">
                                Son maç verisi bulunamadı
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* 5) Collapsible Injuries Summary Pill: Ev X · Dep Y */}
                  <div className="rounded-2xl bg-[#0B0F17] border border-[#1F2937] overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setInjuriesOpen((v) => !v)}
                      className="w-full px-4 py-3 flex items-center justify-between gap-2 text-xs hover:bg-white/[0.02]"
                    >
                      <span className="font-semibold text-slate-200 flex items-center gap-1.5">
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                        Sakat & Cezalı
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="px-2.5 py-0.5 rounded-full bg-slate-800 border border-white/10 text-[11px] font-bold text-slate-200 mono-nums">
                          Ev {detail.context.injuries.home.length} · Dep{" "}
                          {detail.context.injuries.away.length}
                        </span>
                        {injuriesOpen ? (
                          <ChevronUp className="w-4 h-4 text-slate-400" />
                        ) : (
                          <ChevronDown className="w-4 h-4 text-slate-400" />
                        )}
                      </div>
                    </button>
                    {injuriesOpen && (
                      <div className="px-4 pb-4 pt-2 border-t border-[#1F2937]">
                        {detail.context.injuries.home.length === 0 &&
                        detail.context.injuries.away.length === 0 ? (
                          <p className="text-xs text-slate-500">
                            Bu maç için kayıtlı sakat/cezalı verisi bulunamadı.
                          </p>
                        ) : (
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                            {[
                              { team: detail.home_team, list: detail.context.injuries.home },
                              { team: detail.away_team, list: detail.context.injuries.away },
                            ].map((side) => (
                              <div key={side.team} className="space-y-1.5">
                                <p className="font-semibold text-slate-200">{side.team}</p>
                                {side.list.length === 0 ? (
                                  <p className="text-slate-500 text-[11px]">Eksik bildirimi yok</p>
                                ) : (
                                  side.list.slice(0, 6).map((inj, idx) => (
                                    <div
                                      key={idx}
                                      className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-[#131926] border border-[#1F2937]"
                                    >
                                      <div className="flex items-center gap-1.5 min-w-0">
                                        <span className="text-slate-200 truncate">{inj.player}</span>
                                        {inj.market_value && (
                                          <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[10px] font-semibold shrink-0 mono-nums">
                                            {inj.market_value}
                                          </span>
                                        )}
                                      </div>
                                      <span className="text-rose-400 text-[11px] shrink-0">
                                        {translateInjuryReason(inj.reason)}
                                      </span>
                                    </div>
                                  ))
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* 6) Collapsible Model Detayı (Takım Güç Puanı, Gol Beklentisi, Şut/Dinlenme) */}
                  <div className="rounded-2xl bg-[#090D16] border border-[#1F2937] overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setModelOpen((v) => !v)}
                      className="w-full px-4 py-3 flex items-center justify-between text-xs font-semibold text-slate-200 hover:bg-white/[0.02]"
                    >
                      <span>Model Detayı (Güç Puanı, Gol Beklentisi & Şut/Dinlenme)</span>
                      {modelOpen ? (
                        <ChevronUp className="w-4 h-4 text-slate-400" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-slate-400" />
                      )}
                    </button>
                    {modelOpen && (
                      <div className="px-4 pb-4 pt-2 border-t border-[#1F2937] space-y-3">
                        {detail.context.home_elo != null && detail.context.away_elo != null && (() => {
                          const h = detail.context.home_elo!;
                          const a = detail.context.away_elo!;
                          const hPct = Math.round((h / (h + a)) * 100);
                          return (
                            <div>
                              <div className="flex justify-between text-[11px] mb-1 mono-nums">
                                <span className="font-bold text-emerald-400">{Math.round(h)}</span>
                                <span className="text-slate-400 font-medium">Takım Güç Puanı</span>
                                <span className="font-bold text-cyan-400">{Math.round(a)}</span>
                              </div>
                              <div className="h-2 rounded-full bg-[#131926] overflow-hidden flex">
                                <div className="bg-emerald-500 h-full" style={{ width: `${hPct}%` }} />
                                <div className="bg-cyan-500 h-full flex-1" />
                              </div>
                            </div>
                          );
                        })()}

                        {detail.top_prediction?.breakdown.xg_home != null &&
                          detail.top_prediction?.breakdown.xg_away != null && (() => {
                            const hx = detail.top_prediction!.breakdown.xg_home!;
                            const ax = detail.top_prediction!.breakdown.xg_away!;
                            const sum = hx + ax || 1;
                            const hPct = Math.round((hx / sum) * 100);
                            return (
                              <div>
                                <div className="flex justify-between text-[11px] mb-1 mono-nums">
                                  <span className="font-bold text-emerald-400">{hx.toFixed(2)}</span>
                                  <span className="text-slate-400 font-medium">
                                    Gol Beklentisi
                                  </span>
                                  <span className="font-bold text-cyan-400">{ax.toFixed(2)}</span>
                                </div>
                                <div className="h-2 rounded-full bg-[#131926] overflow-hidden flex">
                                  <div className="bg-emerald-500 h-full" style={{ width: `${hPct}%` }} />
                                  <div className="bg-cyan-500 h-full flex-1" />
                                </div>
                              </div>
                            );
                          })()}

                        {detail.context.home_true_xg != null && detail.context.away_true_xg != null && (
                          <div className="text-[11px] text-slate-300 mono-nums">
                            <span className="text-slate-400 mr-1.5">
                              {detail.context.xg_source === "espn_shots"
                                ? "Şut Üretimi:"
                                : "Şut Kalitesi Ortalaması:"}
                            </span>
                            <strong className="text-emerald-400">
                              Ev {detail.context.home_true_xg.toFixed(2)}
                            </strong>{" "}
                            •{" "}
                            <strong className="text-cyan-400">
                              Dep {detail.context.away_true_xg.toFixed(2)}
                            </strong>
                          </div>
                        )}

                        <div className="flex flex-wrap gap-1.5">
                          {detail.context.home_rank != null && detail.context.away_rank != null && (
                            <span className="px-2 py-0.5 rounded bg-[#131926] border border-[#1F2937] text-[10px] font-bold text-slate-300 mono-nums">
                              Sıra: #{detail.context.home_rank} vs #{detail.context.away_rank}
                            </span>
                          )}
                          {detail.top_prediction?.breakdown.kelly_units !== undefined && (
                            <span className="px-2 py-0.5 rounded bg-[#131926] border border-[#1F2937] text-[10px] font-semibold text-slate-200 mono-nums">
                              Önerilen Pay: {detail.top_prediction.breakdown.kelly_units.toFixed(1)} / 5
                            </span>
                          )}
                          {detail.top_prediction?.breakdown.home_rest_days != null &&
                            detail.top_prediction?.breakdown.away_rest_days != null && (
                              <span className="px-2 py-0.5 rounded bg-[#131926] border border-[#1F2937] text-[10px] font-medium text-slate-300 mono-nums">
                                Dinlenme: Ev {detail.top_prediction.breakdown.home_rest_days}g • Dep{" "}
                                {detail.top_prediction.breakdown.away_rest_days}g
                              </span>
                            )}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* 7) Collapsible Oranlar (Chart + Ordered Alt/Üst Table + Fikstra Badge) */}
                  <div className="rounded-2xl bg-[#090D16] border border-[#1F2937] overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setOddsOpen((v) => !v)}
                      className="w-full px-4 py-3 flex items-center justify-between text-xs font-semibold text-slate-200 hover:bg-white/[0.02]"
                    >
                      <span>Oranlar & Değişim Grafiği</span>
                      {oddsOpen ? (
                        <ChevronUp className="w-4 h-4 text-slate-400" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-slate-400" />
                      )}
                    </button>
                    {oddsOpen && (
                      <div className="px-4 pb-4 pt-2 border-t border-[#1F2937] space-y-4">
                        {detail.odds_history.length > 0 && historyKeys.length > 0 && (
                          <div>
                            <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5 mb-2">
                              <TrendingDown className="w-3.5 h-3.5 text-emerald-400" />
                              Oran Değişimi (
                              {formatMarketLabel(
                                detail.odds_history_market,
                                detail.odds_history_selection
                              )}
                              )
                            </h4>
                            <div className="h-40 w-full">
                              <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={detail.odds_history}>
                                  <XAxis dataKey="time" stroke="#64748B" fontSize={10} />
                                  <YAxis
                                    stroke="#64748B"
                                    fontSize={10}
                                    width={42}
                                    tickFormatter={(v) => Number(v).toFixed(2)}
                                    domain={[
                                      (dataMin: number) =>
                                        Math.max(1.01, Number((dataMin - 0.05).toFixed(2))),
                                      (dataMax: number) => Number((dataMax + 0.05).toFixed(2)),
                                    ]}
                                  />
                                  <Tooltip
                                    contentStyle={{
                                      backgroundColor: "#131926",
                                      borderColor: "#1F2937",
                                      borderRadius: "10px",
                                      fontSize: "12px",
                                    }}
                                  />
                                  {historyKeys.map((k, i) => (
                                    <Line
                                      key={k}
                                      type="monotone"
                                      dataKey={k}
                                      stroke={["#10B981", "#06B6D4", "#F59E0B"][i % 3]}
                                      strokeWidth={2}
                                      dot={{ r: 3 }}
                                    />
                                  ))}
                                </LineChart>
                              </ResponsiveContainer>
                            </div>
                          </div>
                        )}

                        {/* MS 1 - X - 2 */}
                        {(() => {
                          const msRows = [...detail.odds.filter((o) => o.market_key === "MS")].sort(
                            (a, b) =>
                              ({ "1": 1, X: 2, "2": 3 }[a.selection_key] ?? 9) -
                              ({ "1": 1, X: 2, "2": 3 }[b.selection_key] ?? 9)
                          );
                          if (!msRows.length) return null;
                          return (
                            <div className="space-y-1.5">
                              <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                                Maç Sonucu (1 - X - 2)
                              </p>
                              <div className="grid grid-cols-3 gap-2">
                                {msRows.map((o, i) => {
                                  const isRec = isRecommendedCell(o.market_key, o.selection_key);
                                  const hasEdge =
                                    o.global_avg_odd != null && o.iddaa_odd > o.global_avg_odd;
                                  return (
                                    <div
                                      key={i}
                                      className={`relative flex items-center justify-between px-2.5 py-2 rounded-xl bg-[#131926] border text-xs ${
                                        isRec
                                          ? "border-[#00FF85] shadow-[0_0_10px_-3px_rgba(0,255,133,0.45)]"
                                          : hasEdge
                                          ? "border-emerald-500/45"
                                          : "border-[#1F2937]"
                                      }`}
                                    >
                                      {isRec && (
                                        <span className="absolute -top-2 left-2 px-1.5 py-0.2 rounded bg-[#00FF85] text-[#05080D] text-[9px] font-extrabold">
                                          Fikstra
                                        </span>
                                      )}
                                      <span className="text-slate-400 font-medium">
                                        {formatMarketLabel(o.market_key, o.selection_key)}
                                      </span>
                                      <div className="text-right mono-nums">
                                        <span className="font-bold text-slate-100">
                                          {o.iddaa_odd.toFixed(2)}
                                        </span>
                                        {o.global_avg_odd && (
                                          <span className="block text-[10px] text-cyan-400">
                                            G: {o.global_avg_odd.toFixed(2)}
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })()}

                        {/* Ordered Gol Alt / Üst Table: 1 row per line (1.5, 2.5, 3.5), 2 cols (Alt | Üst) */}
                        {auRows.length > 0 && (
                          <div className="space-y-1.5">
                            <div className="grid grid-cols-[64px_1fr_1fr] gap-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-1">
                              <span>Barem</span>
                              <span>Alt</span>
                              <span>Üst</span>
                            </div>
                            <div className="space-y-2">
                              {auRows.map((row) => (
                                <div
                                  key={row.mk}
                                  className="grid grid-cols-[64px_1fr_1fr] gap-2 items-center"
                                >
                                  <span className="text-xs font-bold text-slate-300 pl-1">
                                    {row.label}
                                  </span>
                                  {[row.alt, row.ust].map((cell, ci) => {
                                    if (!cell) {
                                      return (
                                        <div
                                          key={ci}
                                          className="px-3 py-2 rounded-xl bg-[#131926]/50 border border-[#1F2937] text-xs text-slate-500 text-center"
                                        >
                                          —
                                        </div>
                                      );
                                    }
                                    const isRec = isRecommendedCell(
                                      cell.market_key,
                                      cell.selection_key
                                    );
                                    const hasEdge =
                                      cell.global_avg_odd != null &&
                                      cell.iddaa_odd > cell.global_avg_odd;
                                    return (
                                      <div
                                        key={ci}
                                        className={`relative flex items-center justify-between px-3 py-2 rounded-xl bg-[#131926] border text-xs ${
                                          isRec
                                            ? "border-[#00FF85] shadow-[0_0_10px_-3px_rgba(0,255,133,0.45)]"
                                            : hasEdge
                                            ? "border-emerald-500/45"
                                            : "border-[#1F2937]"
                                        }`}
                                      >
                                        {isRec && (
                                          <span className="absolute -top-2 left-2 px-1.5 rounded bg-[#00FF85] text-[#05080D] text-[9px] font-extrabold">
                                            Fikstra
                                          </span>
                                        )}
                                        <span className="text-slate-400 font-medium">
                                          {cell.selection_key === "ALT" ? "Alt" : "Üst"}
                                        </span>
                                        <div className="text-right mono-nums">
                                          <span className="font-bold text-slate-100">
                                            {cell.iddaa_odd.toFixed(2)}
                                          </span>
                                          {cell.global_avg_odd && (
                                            <span className="block text-[10px] text-cyan-400">
                                              G: {cell.global_avg_odd.toFixed(2)}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* KG & Çifte Şans */}
                        {(() => {
                          const otherRows = detail.odds.filter(
                            (o) => o.market_key === "KG" || o.market_key === "CS"
                          );
                          if (!otherRows.length) return null;
                          return (
                            <div className="space-y-1.5">
                              <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                                KG & Çifte Şans
                              </p>
                              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                {otherRows.map((o, i) => {
                                  const isRec = isRecommendedCell(o.market_key, o.selection_key);
                                  const hasEdge =
                                    o.global_avg_odd != null && o.iddaa_odd > o.global_avg_odd;
                                  return (
                                    <div
                                      key={i}
                                      className={`relative flex items-center justify-between px-3 py-2 rounded-xl bg-[#131926] border text-xs ${
                                        isRec
                                          ? "border-[#00FF85] shadow-[0_0_10px_-3px_rgba(0,255,133,0.45)]"
                                          : hasEdge
                                          ? "border-emerald-500/45"
                                          : "border-[#1F2937]"
                                      }`}
                                    >
                                      {isRec && (
                                        <span className="absolute -top-2 left-2 px-1.5 rounded bg-[#00FF85] text-[#05080D] text-[9px] font-extrabold">
                                          Fikstra
                                        </span>
                                      )}
                                      <span className="text-slate-400 font-medium">
                                        {formatMarketLabel(o.market_key, o.selection_key)}
                                      </span>
                                      <div className="text-right mono-nums">
                                        <span className="font-bold text-slate-100">
                                          {o.iddaa_odd.toFixed(2)}
                                        </span>
                                        {o.global_avg_odd && (
                                          <span className="block text-[10px] text-cyan-400">
                                            G: {o.global_avg_odd.toFixed(2)}
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Sticky Bottom Action Bar */}
            {detail && activePick && (
              <div className="shrink-0 px-5 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] bg-[#070B12]/98 border-t border-emerald-500/20 flex items-center gap-2">
                {onToggleSlip && (
                  <button
                    type="button"
                    onClick={() =>
                      onToggleSlip({
                        match_id: detail.match_id,
                        prediction_id: detail.top_prediction?.prediction_id,
                        home_team: detail.home_team,
                        away_team: detail.away_team,
                        market_key: activePick.market_key,
                        selection_key: activePick.selection_key,
                        iddaa_odd: activePick.iddaa_odd,
                        start_time_utc: detail.start_time_utc,
                      })
                    }
                    className={`flex-1 py-3 rounded-xl text-xs font-extrabold flex items-center justify-center gap-2 transition-all mono-nums ${
                      inSlip
                        ? "bg-emerald-500/15 border border-[#00FF85]/50 text-[#00FF85]"
                        : "bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.65)]"
                    }`}
                  >
                    {inSlip ? (
                      <>
                        <Check className="w-4 h-4" />
                        <span>Kuponda ✓ · Çıkar</span>
                      </>
                    ) : (
                      <>
                        <Plus className="w-4 h-4" />
                        <span>Kupona Ekle · {activePick.iddaa_odd.toFixed(2)}</span>
                      </>
                    )}
                  </button>
                )}
                <button
                  type="button"
                  onClick={openShareSheet}
                  className="w-11 h-11 rounded-xl bg-[#0B111C] border border-white/15 hover:border-[#00FF85]/50 text-slate-200 hover:text-[#00FF85] flex items-center justify-center shrink-0 transition-colors"
                  aria-label="Maç Kartını Paylaş"
                  title="Maç Kartını Paylaş"
                >
                  <Share2 className="w-4 h-4" />
                </button>
              </div>
            )}

            {/* Match Share Action Sheet Overlay */}
            <AnimatePresence>
              {shareSheetOpen && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  onClick={() => setShareSheetOpen(false)}
                  className="absolute inset-0 z-20 bg-black/75 backdrop-blur-sm flex items-end justify-center p-3"
                >
                  <motion.div
                    initial={{ y: 40, opacity: 0 }}
                    animate={{ y: 0, opacity: 1 }}
                    exit={{ y: 40, opacity: 0 }}
                    onClick={(e) => e.stopPropagation()}
                    className="w-full rounded-2xl bg-[#0B111C] border border-emerald-500/30 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] space-y-3 shadow-2xl"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-extrabold text-white">Maç Analiz Kartını Paylaş</span>
                      <button
                        type="button"
                        onClick={() => setShareSheetOpen(false)}
                        className="p-1.5 rounded-lg bg-[#070B12] border border-white/10 text-slate-400 hover:text-white"
                        aria-label="Paylaşımı kapat"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>

                    <div className="rounded-xl overflow-hidden bg-[#05080D] border border-white/10 flex items-center justify-center min-h-[180px] max-h-[260px]">
                      {shareLoading ? (
                        <span className="text-xs text-slate-400">Analiz kartı hazırlanıyor...</span>
                      ) : sharePreview ? (
                        <img
                          src={sharePreview.dataUrl}
                          alt="Fikstra Maç Analiz Kartı"
                          className="w-48 h-48 object-contain"
                        />
                      ) : (
                        <span className="text-xs text-rose-400">Önizleme oluşturulamadı.</span>
                      )}
                    </div>

                    {shareStatus && (
                      <p className="text-[11px] text-emerald-400 text-center font-medium">
                        {shareStatus}
                      </p>
                    )}

                    <div className="grid grid-cols-2 gap-2">
                      <button
                        type="button"
                        onClick={handleNativeShareMatch}
                        disabled={!sharePreview}
                        className="py-2.5 px-3 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-extrabold inline-flex items-center justify-center gap-1.5 disabled:opacity-50"
                      >
                        <Share2 className="w-3.5 h-3.5" />
                        Görseli Paylaş
                      </button>
                      <button
                        type="button"
                        onClick={handleDownloadMatchPng}
                        disabled={!sharePreview}
                        className="py-2.5 px-3 rounded-xl bg-cyan-500/15 border border-cyan-500/40 text-cyan-300 text-xs font-bold inline-flex items-center justify-center gap-1.5 disabled:opacity-50"
                      >
                        <Download className="w-3.5 h-3.5" />
                        PNG İndir
                      </button>
                    </div>

                    <button
                      type="button"
                      onClick={handleCopyMatchLink}
                      className="w-full py-2.5 px-3 rounded-xl bg-[#070B12] border border-white/15 hover:border-[#00FF85]/45 text-xs font-semibold text-slate-200 inline-flex items-center justify-center gap-1.5"
                    >
                      {linkCopied ? (
                        <>
                          <Check className="w-3.5 h-3.5 text-[#00FF85]" />
                          <span className="text-[#00FF85]">Maç Linki Kopyalandı</span>
                        </>
                      ) : (
                        <>
                          <Copy className="w-3.5 h-3.5" />
                          <span>Maç Bağlantısını Kopyala</span>
                        </>
                      )}
                    </button>
                  </motion.div>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
