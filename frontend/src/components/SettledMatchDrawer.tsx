import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, CheckCircle2, Copy, MinusCircle, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import type { MatchDetail, PulseData } from "../types";
import { formatPickWithTeam } from "../utils/format";
import { getMatchTopReasonChips } from "../utils/matchShare";
import { LeagueBadge, TeamBadge } from "./TeamBadge";

interface Props {
  matchId: number | null;
  initialDetail?: MatchDetail | null;
  onClose: () => void;
}

export function SettledMatchDrawer({ matchId, initialDetail, onClose }: Props) {
  const [detail, setDetail] = useState<MatchDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [pulse, setPulse] = useState<PulseData | null>(null);
  const [linkCopied, setLinkCopied] = useState(false);

  useEffect(() => {
    if (!matchId) {
      setDetail(null);
      setPulse(null);
      return;
    }
    setLinkCopied(false);
    setPulse(null);

    let cancelled = false;
    const applyDetail = (d: MatchDetail) => {
      setDetail(d);
      if (d.top_prediction?.prediction_id) {
        api
          .getPulse(d.top_prediction.prediction_id)
          .then((p) => {
            if (!cancelled) setPulse(p);
          })
          .catch(() => {});
      }
    };

    if (initialDetail && initialDetail.match_id === matchId) {
      applyDetail(initialDetail);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    api
      .getMatchDetail(matchId)
      .then((d) => {
        if (!cancelled) applyDetail(d);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [matchId, initialDetail]);

  const handleCopyResultLink = async () => {
    if (!detail) return;
    const url = `${window.location.origin}/?match=${detail.match_id}`;
    try {
      await navigator.clipboard.writeText(url);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 2200);
    } catch {
      setLinkCopied(false);
    }
  };

  const tp = detail?.top_prediction ?? null;
  const predStatus = (tp?.status || "").toUpperCase();
  const isWon = predStatus === "WON";
  const isLost = predStatus === "LOST";

  const lockedOdd = tp ? tp.published_odd ?? tp.iddaa_odd : null;
  const lockedConf = tp ? tp.published_confidence ?? tp.confidence_score : null;
  const reasonChips = tp
    ? getMatchTopReasonChips(tp.breakdown, tp.value_edge_pct, tp.odds_drop_pct)
    : [];

  return (
    <AnimatePresence>
      {matchId !== null && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-end md:items-center justify-center p-0 md:p-4"
        >
          <motion.div
            initial={{ y: 40, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 40, opacity: 0 }}
            transition={{ type: "spring", damping: 28, stiffness: 320 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full md:max-w-xl max-h-[92vh] bg-[#070B12] border border-emerald-500/25 rounded-t-3xl md:rounded-3xl overflow-hidden flex flex-col shadow-2xl"
          >
            {/* Header */}
            <div className="px-5 py-4 border-b border-[#1F2937] flex items-center justify-between gap-2 bg-[#0B0F17]/90">
              <div className="flex items-center gap-2 min-w-0">
                {detail && <LeagueBadge name={detail.league} logoUrl={detail.league_logo_url} />}
                <div className="min-w-0">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 truncate">
                    {detail ? detail.league : "Maç Sonucu"}
                  </p>
                  {detail && (
                    <p className="text-xs text-slate-300 font-medium mono-nums">
                      {detail.start_date_tr} · {detail.start_time_tr}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="px-2.5 py-1 rounded-full bg-slate-800/80 border border-white/10 text-[11px] font-bold text-slate-300">
                  Sonuçlandı
                </span>
                <button
                  type="button"
                  onClick={onClose}
                  className="p-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400 hover:text-white"
                  aria-label="Kapat"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="p-5 overflow-y-auto space-y-4 flex-1">
              {loading || !detail ? (
                <div className="space-y-3 animate-pulse">
                  <div className="h-24 rounded-2xl bg-[#131926]" />
                  <div className="h-12 rounded-xl bg-[#131926]" />
                  <div className="h-32 rounded-2xl bg-[#131926]" />
                </div>
              ) : (
                <>
                  {/* Large Final Scoreboard */}
                  <div className="p-4 sm:p-5 rounded-2xl glass-card border border-white/10">
                    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
                      <div className="flex flex-col items-center text-center gap-1.5 min-w-0">
                        <TeamBadge name={detail.home_team} logoUrl={detail.home_logo_url} size="md" />
                        <p className="text-xs sm:text-sm font-bold text-white leading-snug break-words">
                          {detail.home_team}
                        </p>
                      </div>

                      <div className="px-4 py-2 rounded-2xl bg-[#05080D] border border-white/15 text-center shrink-0">
                        <span className="text-2xl sm:text-3xl font-black text-white mono-nums tracking-tight">
                          {detail.home_score ?? "-"} - {detail.away_score ?? "-"}
                        </span>
                        <span className="block text-[10px] font-semibold text-slate-400 mt-0.5">
                          Maç Sonucu
                        </span>
                      </div>

                      <div className="flex flex-col items-center text-center gap-1.5 min-w-0">
                        <TeamBadge name={detail.away_team} logoUrl={detail.away_logo_url} size="md" />
                        <p className="text-xs sm:text-sm font-bold text-white leading-snug break-words">
                          {detail.away_team}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Result Strip */}
                  {tp && (
                    <div
                      className={`px-4 py-3 rounded-2xl border flex items-center justify-center gap-2 ${
                        isWon
                          ? "bg-emerald-500/15 border-[#00FF85]/45 text-[#00FF85]"
                          : isLost
                          ? "bg-rose-500/15 border-rose-500/45 text-rose-300"
                          : "bg-slate-800/70 border-slate-600/40 text-slate-300"
                      }`}
                    >
                      {isWon ? (
                        <CheckCircle2 className="w-5 h-5 shrink-0" />
                      ) : isLost ? (
                        <XCircle className="w-5 h-5 shrink-0" />
                      ) : (
                        <MinusCircle className="w-5 h-5 shrink-0" />
                      )}
                      <span className="text-sm font-black tracking-wide">
                        {isWon ? "TAHMİN TUTTU" : isLost ? "TAHMİN TUTMADI" : "İADE / GEÇERSİZ"}
                      </span>
                    </div>
                  )}

                  {/* Pre-match Locked Prediction Card */}
                  {tp && (
                    <div className="p-4 rounded-2xl bg-[#0B0F17] border border-[#1F2937] space-y-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                          Maç Öncesi Kilitlenen Tahmin
                        </span>
                        {lockedConf !== null && (
                          <span className="px-2.5 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/35 text-[#00FF85] text-xs font-extrabold mono-nums">
                            {Math.round(lockedConf)} Puan
                          </span>
                        )}
                      </div>

                      <div className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-xl bg-[#131926] border border-white/[0.06]">
                        <span className="text-sm font-extrabold text-white">
                          {formatPickWithTeam(
                            tp.market_key,
                            tp.selection_key,
                            detail.home_team,
                            detail.away_team
                          )}
                        </span>
                        {lockedOdd !== null && (
                          <span className="text-sm font-extrabold text-[#00FF85] mono-nums">
                            {lockedOdd.toFixed(2)} Yayın Oranı
                          </span>
                        )}
                      </div>

                      {(tp.closing_odd != null || tp.clv_pct != null) && (
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          {tp.closing_odd != null && (
                            <div className="px-3 py-2 rounded-xl bg-[#131926] border border-white/[0.06]">
                              <span className="text-[10px] text-slate-400 block">Kapanış Oranı</span>
                              <span className="font-bold text-slate-200 mono-nums">
                                {tp.closing_odd.toFixed(2)}
                              </span>
                            </div>
                          )}
                          {tp.clv_pct != null && (
                            <div className="px-3 py-2 rounded-xl bg-[#131926] border border-white/[0.06]">
                              <span className="text-[10px] text-slate-400 block">Oran Avantajı</span>
                              <span
                                className={`font-bold mono-nums ${
                                  tp.clv_pct >= 0 ? "text-[#00FF85]" : "text-slate-300"
                                }`}
                              >
                                {tp.clv_pct > 0 ? "+" : ""}
                                %{tp.clv_pct.toFixed(1)}
                              </span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Pre-match Analysis Summary (Read-Only) */}
                  {(reasonChips.length > 0 || detail.context.ai_summary) && (
                    <div className="p-4 rounded-2xl bg-[#0B0F17] border border-[#1F2937] space-y-2.5">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400 block">
                        Maç Öncesi Analiz Özeti
                      </span>
                      {reasonChips.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {reasonChips.map((chip) => (
                            <span
                              key={chip}
                              className="px-2.5 py-1 rounded-lg bg-[#131926] border border-white/10 text-[11px] font-semibold text-slate-200"
                            >
                              {chip}
                            </span>
                          ))}
                        </div>
                      )}
                      {detail.context.ai_summary && (
                        <p className="text-xs text-slate-300 leading-relaxed">
                          {detail.context.ai_summary}
                        </p>
                      )}
                    </div>
                  )}

                  {/* Read-only Community Opinion (only if >= 5 votes) */}
                  {pulse && pulse.revealed && pulse.total_votes >= 5 && (
                    <div className="p-3.5 rounded-2xl bg-[#0B0F17] border border-[#1F2937] space-y-2">
                      <div className="flex items-center justify-between text-[11px]">
                        <span className="font-bold text-slate-300">Maç öncesi topluluk görüşü</span>
                        <span className="text-slate-400 mono-nums">{pulse.total_votes} oy</span>
                      </div>
                      <div className="h-2 rounded-full bg-[#131926] overflow-hidden flex">
                        <div
                          className="bg-[#00FF85] h-full"
                          style={{ width: `${pulse.agree_pct ?? 50}%` }}
                        />
                        <div className="bg-rose-500/80 h-full flex-1" />
                      </div>
                      <div className="flex items-center justify-between text-[11px] mono-nums">
                        <span className="text-[#00FF85] font-bold">
                          %{pulse.agree_pct ?? 0} Katıldı
                        </span>
                        <span className="text-rose-300 font-bold">
                          %{pulse.risky_pct ?? 0} Riskli Buldu
                        </span>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Bottom Fixed Bar — NO + Kupona Ekle */}
            <div className="px-5 py-3.5 border-t border-[#1F2937] bg-[#0B0F17]/95 flex items-center gap-2.5">
              <button
                type="button"
                onClick={handleCopyResultLink}
                disabled={!detail}
                className="flex-1 py-2.5 px-3 rounded-xl bg-[#131926] border border-white/15 hover:border-[#00FF85]/45 text-xs font-bold text-slate-200 inline-flex items-center justify-center gap-1.5 disabled:opacity-50"
              >
                {linkCopied ? (
                  <>
                    <Check className="w-4 h-4 text-[#00FF85]" />
                    <span className="text-[#00FF85]">Sonuç Linki Kopyalandı</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-4 h-4" />
                    <span>Sonuç Linkini Kopyala</span>
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="px-5 py-2.5 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-extrabold"
              >
                Kapat
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
