import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowDownRight,
  Flame,
  Plus,
  Check,
  Search,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { api } from "../api/client";
import type { CustomSlipItem, DashboardSummary, PredictionItem } from "../types";
import { formatMarketLabel, getConfidenceTone } from "../utils/format";
import { ConfidenceRing } from "../components/ConfidenceRing";
import { FormStrip, LeagueBadge, TeamBadge } from "../components/TeamBadge";

interface Props {
  summary: DashboardSummary | null;
  onSelectMatch: (matchId: number) => void;
  slipItems: CustomSlipItem[];
  onToggleSlip: (item: CustomSlipItem) => void;
  refreshTrigger: number;
}

export function HighlightsPage({
  summary,
  onSelectMatch,
  slipItems,
  onToggleSlip,
  refreshTrigger,
}: Props) {
  const [todayOnly, setTodayOnly] = useState(true);
  const [majorOnly, setMajorOnly] = useState(true);
  const [minOdd, setMinOdd] = useState(1.3);
  const [minConf, setMinConf] = useState(0);
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<PredictionItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getPredictions({
        todayOnly,
        majorOnly,
        minOdd,
        minConfidence: minConf,
        search,
      })
      .then((res) => {
        if (!cancelled) setItems(res.items);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [todayOnly, majorOnly, minOdd, minConf, search, refreshTrigger]);

  const slipIds = new Set(slipItems.map((s) => `${s.match_id}_${s.market_key}_${s.selection_key}`));

  return (
    <div className="space-y-4">
      {/* Compact Horizontal Summary Strip */}
      <div className="flex gap-3 overflow-x-auto no-scrollbar pb-1">
        <div className="min-w-[165px] flex-1 p-3.5 rounded-2xl bg-[#131926] border border-[#1F2937]">
          <p className="text-[11px] text-slate-400">Genel Tutturma</p>
          <p className="text-xl font-extrabold mono-nums text-emerald-400 mt-1">
            {summary?.overall_win_rate !== null && summary?.overall_win_rate !== undefined
              ? `%${summary.overall_win_rate}`
              : "Bekleniyor"}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">
            {summary?.settled_total ? `${summary.settled_total} sonuçlanan` : "İlk sonuçlar 130 dk sonra"}
          </p>
        </div>

        <button
          onClick={() => summary?.top_confidence && onSelectMatch(summary.top_confidence.match_id)}
          className="min-w-[205px] flex-1 text-left p-3.5 rounded-2xl bg-[#131926] border border-[#1F2937] hover:border-emerald-500/40 transition-colors"
        >
          <p className="text-[11px] text-slate-400 flex items-center gap-1">
            <Flame className="w-3.5 h-3.5 text-emerald-400" /> Günün En Güvenli Maçı
          </p>
          <p className="text-xs font-bold text-slate-100 truncate mt-1">
            {summary?.top_confidence?.match_name || "Veri hazırlanıyor"}
          </p>
          <p className="text-[11px] mono-nums text-emerald-400 mt-0.5">
            {summary?.top_confidence
              ? `${formatMarketLabel(summary.top_confidence.market_key, summary.top_confidence.selection_key)} @ ${summary.top_confidence.iddaa_odd.toFixed(2)} • ${Math.round(summary.top_confidence.confidence_score)} Puan`
              : "—"}
          </p>
        </button>

        <button
          onClick={() => summary?.best_value && onSelectMatch(summary.best_value.match_id)}
          className="min-w-[205px] flex-1 text-left p-3.5 rounded-2xl bg-[#131926] border border-[#1F2937] hover:border-cyan-500/40 transition-colors"
        >
          <p className="text-[11px] text-slate-400 flex items-center gap-1">
            <Sparkles className="w-3.5 h-3.5 text-cyan-400" /> En İyi Global Fark
          </p>
          <p className="text-xs font-bold text-slate-100 truncate mt-1">
            {summary?.best_value?.match_name || "Kıyaslama bekleniyor"}
          </p>
          <p className="text-[11px] mono-nums text-cyan-400 mt-0.5">
            {summary?.best_value
              ? `${formatMarketLabel(summary.best_value.market_key, summary.best_value.selection_key)} • ${summary.best_value.iddaa_odd.toFixed(2)} vs G ${summary.best_value.global_odd?.toFixed(2) ?? "-"}`
              : "—"}
          </p>
        </button>

        <button
          onClick={() => summary?.sharpest_drop && onSelectMatch(summary.sharpest_drop.match_id)}
          className="min-w-[205px] flex-1 text-left p-3.5 rounded-2xl bg-[#131926] border border-[#1F2937] hover:border-rose-500/40 transition-colors"
        >
          <p className="text-[11px] text-slate-400 flex items-center gap-1">
            <TrendingUp className="w-3.5 h-3.5 text-rose-400" /> En Sert Oran Düşüşü
          </p>
          <p className="text-xs font-bold text-slate-100 truncate mt-1">
            {summary?.sharpest_drop?.match_name || "Takip ediliyor"}
          </p>
          <p className="text-[11px] mono-nums text-rose-400 mt-0.5">
            {summary?.sharpest_drop
              ? `${formatMarketLabel(summary.sharpest_drop.market_key, summary.sharpest_drop.selection_key)} • -%${summary.sharpest_drop.odds_drop_pct?.toFixed(1)} (${summary.sharpest_drop.iddaa_odd.toFixed(2)})`
              : "Henüz düşüş yok"}
          </p>
        </button>
      </div>

      {/* Filter Chips + Search */}
      <div className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setTodayOnly(true)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
              todayOnly
                ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/40"
                : "bg-[#131926] text-slate-400 border-[#1F2937]"
            }`}
          >
            Bugün (24s)
          </button>
          <button
            onClick={() => setTodayOnly(false)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
              !todayOnly
                ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/40"
                : "bg-[#131926] text-slate-400 border-[#1F2937]"
            }`}
          >
            Tüm Bülten
          </button>

          <span className="w-px h-4 bg-[#1F2937]" />

          <button
            onClick={() => setMajorOnly(true)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
              majorOnly
                ? "bg-cyan-500/15 text-cyan-400 border-cyan-500/40"
                : "bg-[#131926] text-slate-400 border-[#1F2937]"
            }`}
          >
            Büyük Ligler
          </button>
          <button
            onClick={() => setMajorOnly(false)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
              !majorOnly
                ? "bg-cyan-500/15 text-cyan-400 border-cyan-500/40"
                : "bg-[#131926] text-slate-400 border-[#1F2937]"
            }`}
          >
            Tüm Ligler
          </button>

          <select
            value={minOdd}
            onChange={(e) => setMinOdd(Number(e.target.value))}
            className="px-3 py-1.5 rounded-full text-xs font-semibold bg-[#131926] text-slate-200 border border-[#1F2937] outline-none"
          >
            <option value={1.3}>Min 1.30 Oran</option>
            <option value={1.5}>Min 1.50 Oran</option>
            <option value={1.8}>Min 1.80 Oran</option>
          </select>

          <select
            value={minConf}
            onChange={(e) => setMinConf(Number(e.target.value))}
            className="px-3 py-1.5 rounded-full text-xs font-semibold bg-[#131926] text-slate-200 border border-[#1F2937] outline-none"
          >
            <option value={0}>Tüm Güven</option>
            <option value={65}>65+ Güven</option>
            <option value={75}>75+ Güven</option>
          </select>
        </div>

        <div className="relative">
          <Search className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Takım veya lig ara (örn. Fenerbahçe, Şampiyonlar Ligi)..."
            className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-[#131926] border border-[#1F2937] text-xs text-slate-100 placeholder:text-slate-500 focus:border-emerald-500/50 outline-none"
          />
        </div>
      </div>

      {/* Match Cards */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4].map((n) => (
            <div
              key={n}
              className="h-32 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse"
            />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="p-8 text-center rounded-2xl bg-[#131926] border border-[#1F2937]">
          <p className="text-sm font-semibold text-slate-200">
            Seçili filtrelere uygun maç bulunamadı
          </p>
          <p className="text-xs text-slate-400 mt-1">
            "Tüm Ligler" veya "Tüm Bülten" filtresini deneyebilirsiniz.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {items.map((item, idx) => {
            const tone = getConfidenceTone(item.confidence_score);
            const inSlip = slipIds.has(`${item.match_id}_${item.market_key}_${item.selection_key}`);
            const hasDrop = (item.odds_drop_pct || 0) >= 2.0;

            return (
              <motion.div
                key={item.prediction_id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(idx * 0.03, 0.25) }}
                onClick={() => onSelectMatch(item.match_id)}
                className={`cursor-pointer p-4 rounded-2xl bg-[#131926] border border-[#1F2937] hover:border-slate-600 transition-all ${tone.glow}`}
              >
                {/* Top Meta */}
                <div className="flex items-center justify-between gap-2 mb-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <LeagueBadge name={item.league} logoUrl={item.league_logo_url} />
                    {item.is_major_league && !item.league_logo_url && (
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 shrink-0" />
                    )}
                    <span className="text-[11px] font-medium text-slate-400 truncate">
                      {item.league}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {item.injuries_count >= 1 && (
                      <span
                        title={`${item.injuries_count} sakat/cezalı oyuncu`}
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px] font-semibold"
                      >
                        <AlertTriangle className="w-3 h-3" /> {item.injuries_count} Eksik
                      </span>
                    )}
                    <span className="text-[11px] mono-nums text-slate-300 font-semibold">
                      {item.start_date_tr} {item.start_time_tr}
                    </span>
                  </div>
                </div>

                {/* Teams + Confidence Ring */}
                <div className="flex items-center justify-between gap-3">
                  <div className="space-y-2 min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TeamBadge name={item.home_team} logoUrl={item.home_logo_url} />
                        <span className="text-sm font-bold text-slate-100 truncate">
                          {item.home_team}
                        </span>
                      </div>
                      <FormStrip form={item.home_form} />
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TeamBadge name={item.away_team} logoUrl={item.away_logo_url} />
                        <span className="text-sm font-bold text-slate-100 truncate">
                          {item.away_team}
                        </span>
                      </div>
                      <FormStrip form={item.away_form} />
                    </div>
                  </div>
                  <ConfidenceRing score={item.confidence_score} size={50} />
                </div>

                {/* Bottom Pick & Odds Comparison Bar */}
                <div className="mt-3.5 pt-3 border-t border-[#1F2937] flex items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                    <span
                      className={`px-2.5 py-1 rounded-lg border text-xs font-bold ${tone.bg} ${tone.text} ${tone.border}`}
                    >
                      {formatMarketLabel(item.market_key, item.selection_key)}
                    </span>
                    <span className="text-sm font-extrabold mono-nums text-white">
                      {item.iddaa_odd.toFixed(2)}
                    </span>
                    {item.breakdown.kelly_units !== undefined && (
                      <span className="px-1.5 py-0.5 rounded bg-slate-800/90 border border-[#1F2937] text-[10px] font-semibold text-slate-300 mono-nums">
                        {item.breakdown.kelly_units.toFixed(1)}/5 Birim
                      </span>
                    )}
                    {item.breakdown.smart_money && (
                      <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-[10px] font-bold text-emerald-400">
                        Akıllı Para
                      </span>
                    )}
                    {item.breakdown.trap_warning && (
                      <span className="px-1.5 py-0.5 rounded bg-rose-500/15 border border-rose-500/30 text-[10px] font-bold text-rose-400">
                        Tuzak
                      </span>
                    )}
                    {item.global_odd && (
                      <span className="text-[11px] mono-nums text-cyan-400">
                        Global {item.global_odd.toFixed(2)}
                      </span>
                    )}
                    {hasDrop && (
                      <span className="inline-flex items-center gap-0.5 text-[11px] mono-nums text-emerald-400 font-semibold">
                        <ArrowDownRight className="w-3.5 h-3.5" />
                        {item.opening_odd.toFixed(2)}→{item.iddaa_odd.toFixed(2)}
                      </span>
                    )}
                  </div>

                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleSlip({
                        match_id: item.match_id,
                        home_team: item.home_team,
                        away_team: item.away_team,
                        market_key: item.market_key,
                        selection_key: item.selection_key,
                        iddaa_odd: item.iddaa_odd,
                      });
                    }}
                    className={`p-2 rounded-xl border transition-colors shrink-0 ${
                      inSlip
                        ? "bg-emerald-500 text-slate-950 border-emerald-400"
                        : "bg-[#0B0F17] text-slate-300 border-[#1F2937] hover:border-emerald-500/50"
                    }`}
                    title="Kuponuma ekle"
                  >
                    {inSlip ? <Check className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
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
