import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Check, Flame, Plus, Search, Zap } from "lucide-react";
import { api } from "../api/client";
import type { CustomSlipItem, DashboardSummary, PredictionItem } from "../types";
import { formatMarketLabel } from "../utils/format";
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
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<PredictionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const lastFilterRef = useRef<string>("");

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
        if (!cancelled) setItems(res.items);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [todayOnly, search, refreshTrigger]);

  const slipIds = new Set(slipItems.map((s) => `${s.match_id}_${s.market_key}_${s.selection_key}`));

  return (
    <div className="space-y-4">
      {/* Top 2 Summary Cards */}
      <div className="grid grid-cols-2 gap-3">
        {/* Left Card: Başarı oranımız */}
        <div className="relative overflow-hidden p-4 rounded-2xl glass-card neon-bottom-glow">
          {/* Subtle Rising Green Arrow SVG Silhouette */}
          <svg
            viewBox="0 0 120 80"
            className="pointer-events-none absolute -right-2 -bottom-2 w-28 h-20 text-[#00FF85] opacity-[0.09]"
            fill="none"
          >
            <path
              d="M10 66 L42 42 L64 52 L106 14 M106 14 L84 14 M106 14 L106 36"
              stroke="currentColor"
              strokeWidth="8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <p className="text-[11px] font-medium text-slate-400">Başarı oranımız</p>
          <p className="text-2xl sm:text-3xl font-extrabold mono-nums text-[#00FF85] mt-1 tracking-tight drop-shadow-[0_0_12px_rgba(0,255,133,0.3)]">
            {summary?.overall_win_rate !== null && summary?.overall_win_rate !== undefined
              ? `%${summary.overall_win_rate}`
              : "Bekleniyor"}
          </p>
          <p className="text-[10px] text-slate-400 mt-1">
            {summary?.settled_total
              ? `${summary.settled_total} sonuçlanan`
              : "İlk sonuçlar 130 dk sonra"}
          </p>
        </div>

        {/* Right Card: Günün En Güvenli Maçı */}
        <button
          onClick={() => summary?.top_confidence && onSelectMatch(summary.top_confidence.match_id)}
          className="relative overflow-hidden text-left p-4 rounded-2xl glass-card glass-card-hover neon-bottom-glow transition-all"
        >
          {/* Subtle Ball / Stadium SVG Silhouette */}
          <svg
            viewBox="0 0 100 100"
            className="pointer-events-none absolute -right-4 -bottom-4 w-24 h-24 text-[#00FF85] opacity-[0.08]"
            fill="none"
          >
            <circle cx="50" cy="50" r="42" stroke="currentColor" strokeWidth="5" />
            <polygon
              points="50,24 69,38 62,61 38,61 31,38"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              d="M50 8 L50 24 M90 38 L69 38 M75 84 L62 61 M25 84 L38 61 M10 38 L31 38"
              stroke="currentColor"
              strokeWidth="3.5"
            />
          </svg>
          <p className="text-[11px] font-medium text-slate-400 flex items-center gap-1">
            <Flame className="w-3.5 h-3.5 text-[#00FF85]" />
            <span className="truncate">Günün En Güvenli Maçı</span>
          </p>
          <p className="text-xs sm:text-sm font-bold text-white truncate mt-1.5">
            {summary?.top_confidence?.match_name || "Veri hazırlanıyor"}
          </p>
          <p className="text-[11px] font-semibold mono-nums text-[#00FF85] mt-1 truncate">
            {summary?.top_confidence
              ? `${formatMarketLabel(summary.top_confidence.market_key, summary.top_confidence.selection_key)} @ ${summary.top_confidence.iddaa_odd.toFixed(2)} • ${Math.round(summary.top_confidence.confidence_score)} Puan`
              : "—"}
          </p>
        </button>
      </div>

      {/* Simplified Filter Area: Only Bugün (24s) / Tüm Bülten + Full-width Search */}
      <div className="space-y-2.5">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setTodayOnly(true)}
            className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-xs font-bold transition-all ${
              todayOnly
                ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.75)]"
                : "glass-card text-slate-300 hover:text-white"
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            Bugün (24s)
          </button>
          <button
            onClick={() => setTodayOnly(false)}
            className={`px-4 py-2 rounded-full text-xs font-bold transition-all ${
              !todayOnly
                ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.75)]"
                : "glass-card text-slate-300 hover:text-white"
            }`}
          >
            Tümü
          </button>
        </div>

        <div className="relative">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Takım veya lig ara..."
            className="w-full pl-10 pr-4 py-2.5 rounded-xl glass-card text-xs text-slate-100 placeholder:text-slate-500 focus:border-[#00FF85]/50 outline-none transition-colors"
          />
        </div>
      </div>

      {/* Simplified Match Cards */}
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

            return (
              <motion.div
                key={item.prediction_id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(idx * 0.03, 0.25) }}
                onClick={() => onSelectMatch(item.match_id)}
                className="relative cursor-pointer p-4 rounded-2xl glass-card glass-card-hover neon-bottom-glow transition-all overflow-hidden"
              >
                {/* Soft bottom neon emerald edge glow */}
                <div className="pointer-events-none absolute inset-x-6 bottom-0 h-[1.5px] bg-gradient-to-r from-transparent via-[#00FF85]/55 to-transparent" />

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

                {/* Middle: Home & Away Teams + Last 5 Form Boxes + Large Confidence Ring */}
                <div className="flex items-center justify-between gap-3">
                  <div className="space-y-2.5 min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TeamBadge name={item.home_team} logoUrl={item.home_logo_url} />
                        <span className="text-sm font-bold text-white truncate">
                          {item.home_team}
                        </span>
                      </div>
                      <FormStrip form={item.home_form} />
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <TeamBadge name={item.away_team} logoUrl={item.away_logo_url} />
                        <span className="text-sm font-bold text-white truncate">
                          {item.away_team}
                        </span>
                      </div>
                      <FormStrip form={item.away_form} />
                    </div>
                  </div>

                  <ConfidenceRing score={item.confidence_score} size={54} />
                </div>

                {/* Bottom: Neon Emerald Pick/Odd Pill + Circular + Button */}
                <div className="mt-3.5 pt-3 border-t border-white/[0.06] flex items-center justify-between gap-2">
                  <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#00FF85]/10 border border-[#00FF85]/45 shadow-[0_0_12px_-4px_rgba(0,255,133,0.4)]">
                    <span className="text-xs font-bold text-[#00FF85]">
                      {formatMarketLabel(item.market_key, item.selection_key)}
                    </span>
                    <span className="text-xs font-extrabold mono-nums text-white">
                      {item.iddaa_odd.toFixed(2)}
                    </span>
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
                    className={`w-8 h-8 rounded-full border flex items-center justify-center transition-all shrink-0 ${
                      inSlip
                        ? "bg-[#00FF85] text-[#05080D] border-[#00FF85] shadow-[0_0_12px_rgba(0,255,133,0.5)]"
                        : "bg-white/[0.04] text-slate-200 border-white/15 hover:border-[#00FF85]/60 hover:text-[#00FF85]"
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
