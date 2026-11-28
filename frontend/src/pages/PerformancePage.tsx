import { useEffect, useMemo, useState } from "react";
import { Award, ChevronDown, ChevronUp, Clock, Flame, TrendingUp, Trophy } from "lucide-react";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { PerformanceData } from "../types";
import { formatMarketLabel, formatTurkishPossessive } from "../utils/format";
import { getConfidenceTier } from "../utils/highlightsFilter";
import {
  computeAverageWinningOdd,
  computeConfidenceTierBands,
  computeStrongestMarket,
  computeWinStreaks,
  filterAndPaginateArchive,
  getArchiveStatusCounts,
  getDynamicArchiveMarkets,
  type ArchiveStatusFilter,
} from "../utils/performanceMetrics";

interface Props {
  refreshTrigger: number;
  onSelectMatch?: (matchId: number, predictionId?: number) => void;
}

const TIER_DISPLAY: Record<"BANKO" | "DENGELI" | "SURPRIZ", string> = {
  BANKO: "Banko",
  DENGELI: "Dengeli",
  SURPRIZ: "Sürpriz",
};

function parseDayMonthScore(dateStr: string): number {
  const parts = (dateStr || "").split(".");
  if (parts.length !== 2) return 0;
  const d = Number(parts[0]) || 0;
  const m = Number(parts[1]) || 0;
  return m * 100 + d;
}

export function PerformancePage({ refreshTrigger, onSelectMatch }: Props) {
  const [data, setData] = useState<PerformanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [techOpen, setTechOpen] = useState(false);
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const [bankrollOpen, setBankrollOpen] = useState(false);
  const [breakdownTab, setBreakdownTab] = useState<"league" | "market">("league");
  const [statusFilter, setStatusFilter] = useState<ArchiveStatusFilter>("ALL");
  const [marketFilter, setMarketFilter] = useState<string>("ALL");
  const [visibleLimit, setVisibleLimit] = useState<number>(20);

  useEffect(() => {
    if (data === null) setLoading(true);
    api
      .getPerformance()
      .then(setData)
      .finally(() => setLoading(false));
  }, [refreshTrigger]);

  const sortedTrend = useMemo(() => {
    if (!data?.trend?.length) return [];
    return [...data.trend]
      .sort((a, b) => parseDayMonthScore(a.date) - parseDayMonthScore(b.date))
      .map((t) => ({
        ...t,
        won: Math.round((t.win_rate / 100) * t.total),
      }));
  }, [data?.trend]);

  const avgWinningOdd = useMemo(
    () => (data ? computeAverageWinningOdd(data.archive) : null),
    [data]
  );
  const strongestMarket = useMemo(
    () => (data ? computeStrongestMarket(data.breakdown?.by_market, data.archive) : null),
    [data]
  );
  const streaks = useMemo(
    () => (data ? computeWinStreaks(data.archive) : { longestStreak: 0, currentStreak: 0 }),
    [data]
  );
  const tierBands = useMemo(
    () => (data ? computeConfidenceTierBands(data.archive, data.calibration_bands) : []),
    [data]
  );
  const statusCounts = useMemo(
    () => (data ? getArchiveStatusCounts(data.archive) : { all: 0, won: 0, lost: 0 }),
    [data]
  );
  const archiveMarkets = useMemo(
    () => (data ? getDynamicArchiveMarkets(data.archive) : []),
    [data]
  );
  const paginatedArchive = useMemo(
    () =>
      data
        ? filterAndPaginateArchive(data.archive, statusFilter, marketFilter, visibleLimit)
        : { filtered: [], visible: [], hasMore: false, remainingCount: 0 },
    [data, statusFilter, marketFilter, visibleLimit]
  );

  if (loading || !data) {
    return <div className="h-48 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />;
  }

  const totalSettledCoupons = (["BANKO", "DENGELI", "SURPRIZ"] as const).reduce(
    (sum, t) => sum + (data.coupon_tiers?.[t]?.total ?? 0),
    0
  );

  return (
    <div className="space-y-4">
      {/* Human-Language Trust Header */}
      <div className="p-4 sm:p-5 rounded-2xl glass-card neon-bottom-glow flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-wider font-semibold text-emerald-300/80">
            Resmi 60+ Güven Performansı
          </p>
          <h2 className="text-lg sm:text-xl font-extrabold text-white mt-0.5">
            {data.total_settled > 0
              ? `Son ${data.total_settled} tahminin ${formatTurkishPossessive(data.won_count)} tuttu`
              : "İlk sonuçlar bekleniyor"}
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Burada yalnızca Analizler ekranında maç başlamadan önce yayınlanan resmi 60+ tahminler yer alır.
          </p>
        </div>

        {data.archive.length > 0 && (
          <div className="flex items-center gap-1.5 flex-wrap max-w-[240px]" aria-label="Son sonuçlar">
            {data.archive.slice(0, 16).map((row) => (
              <span
                key={row.prediction_id}
                title={`${row.home_team} - ${row.away_team}: ${row.status === "WON" ? "Kazandı" : "Kaybetti"}`}
                className={`w-2.5 h-2.5 rounded-full ${
                  row.status === "WON"
                    ? "bg-[#00FF85] shadow-[0_0_6px_rgba(0,255,133,0.5)]"
                    : "bg-rose-500/80"
                }`}
              />
            ))}
          </div>
        )}
      </div>

      {/* 1. ÜST VİTRİN: 4 User-Friendly KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {/* Card 1: Genel Başarı Oranı */}
        <div className="p-4 rounded-2xl glass-card border-[#00FF85]/25 flex flex-col justify-between">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-slate-400">Genel Başarı Oranı</span>
            <Trophy className="w-4 h-4 text-[#00FF85] shrink-0" />
          </div>
          <div className="mt-2">
            <p className="text-2xl sm:text-3xl font-extrabold mono-nums text-[#00FF85]">
              {data.overall_win_rate !== null ? `%${data.overall_win_rate}` : "—"}
            </p>
            <p className="text-[11px] text-slate-400 mono-nums mt-0.5">
              {data.won_count}/{data.total_settled} tuttu
            </p>
          </div>
        </div>

        {/* Card 2: Ortalama Kazanan Oran */}
        <div className="p-4 rounded-2xl glass-card flex flex-col justify-between">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-slate-400">Ort. Kazanan Oran</span>
            <TrendingUp className="w-4 h-4 text-cyan-400 shrink-0" />
          </div>
          <div className="mt-2">
            <p className="text-2xl sm:text-3xl font-extrabold mono-nums text-cyan-300">
              {avgWinningOdd !== null ? avgWinningOdd.toFixed(2) : "—"}
            </p>
            <p className="text-[11px] text-slate-400 mono-nums mt-0.5">
              {data.total_settled} sonuçlanan tahmin
            </p>
          </div>
        </div>

        {/* Card 3: En Güçlü Pazar */}
        <div className="p-4 rounded-2xl glass-card flex flex-col justify-between">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-slate-400">En Güçlü Pazar</span>
            <Award className="w-4 h-4 text-emerald-400 shrink-0" />
          </div>
          <div className="mt-2">
            {strongestMarket ? (
              <>
                <p className="text-base sm:text-lg font-extrabold text-white truncate">
                  {strongestMarket.label}
                </p>
                <p className="text-xs font-bold mono-nums text-[#00FF85] mt-0.5">
                  %{strongestMarket.winRate} · {strongestMarket.won}/{strongestMarket.total}
                </p>
              </>
            ) : (
              <>
                <p className="text-2xl font-extrabold text-slate-400">—</p>
                <p className="text-[11px] text-slate-500 mt-0.5">Veri bekleniyor</p>
              </>
            )}
          </div>
        </div>

        {/* Card 4: En Uzun Seri */}
        <div className="p-4 rounded-2xl glass-card flex flex-col justify-between">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-slate-400">En Uzun Seri</span>
            <Flame className="w-4 h-4 text-amber-400 shrink-0" />
          </div>
          <div className="mt-2">
            <p className="text-2xl sm:text-3xl font-extrabold mono-nums text-amber-300">
              {streaks.longestStreak > 0 ? `${streaks.longestStreak} Maç` : "—"}
            </p>
            <p className="text-[11px] text-slate-400 mono-nums mt-0.5">
              Mevcut seri: {streaks.currentStreak > 0 ? `${streaks.currentStreak} Maç` : "—"}
            </p>
          </div>
        </div>
      </div>

      {/* 2. Akıllı Kupon Takibi (Compact single-row strip when < 5 settled coupons; 3 cards when >= 5) */}
      {totalSettledCoupons < 5 ? (
        <div className="px-4 py-3 rounded-2xl glass-card flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="font-bold text-slate-200">Kupon Takibi</span>
          <div className="flex flex-wrap items-center gap-3 mono-nums text-[11px]">
            {(["BANKO", "DENGELI", "SURPRIZ"] as const).map((tier) => {
              const st = data.coupon_tiers?.[tier] || { total: 0, won: 0, win_rate: null };
              return (
                <span key={tier} className="inline-flex items-center gap-1.5 text-slate-300">
                  <span className="text-slate-400">{TIER_DISPLAY[tier]}:</span>
                  <strong className="text-white">{st.won}/{st.total}</strong>
                </span>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-2 sm:gap-3">
          {(["BANKO", "DENGELI", "SURPRIZ"] as const).map((tier) => {
            const st = data.coupon_tiers[tier] || { total: 0, won: 0, win_rate: null };
            return (
              <div key={tier} className="p-3 sm:p-4 rounded-2xl glass-card min-w-0">
                <p className="text-[10px] sm:text-[11px] text-slate-400 truncate">
                  {TIER_DISPLAY[tier]} Kupon
                </p>
                <p className="text-lg sm:text-2xl font-extrabold mono-nums text-cyan-400 mt-1">
                  {st.win_rate !== null ? `%${st.win_rate}` : `${st.won}/${st.total}`}
                </p>
                <p className="text-[10px] text-slate-500 mt-0.5 mono-nums">
                  {st.won}/{st.total} tuttu
                </p>
              </div>
            );
          })}
        </div>
      )}

      {/* Güven Kademesine Göre İsabet (60-67 DENGELİ, 68-74 GÜÇLÜ, 75+ ELİT) */}
      <div className="p-4 rounded-2xl glass-card">
        <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
          GÜVEN KADEMESİNE GÖRE İSABET
        </h4>
        <div className="grid grid-cols-3 gap-2.5">
          {tierBands.map((b) => {
            const empty = b.total === 0;
            return (
              <div
                key={b.key}
                className={`p-3 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-center space-y-1 transition-opacity ${
                  empty ? "opacity-55" : ""
                }`}
              >
                <div className="flex items-center justify-center gap-1.5 flex-wrap">
                  <span className={`px-1.5 py-0.5 rounded-full border text-[9px] font-extrabold ${b.badgeCls}`}>
                    {b.label}
                  </span>
                  <span className="text-[10px] text-slate-400 mono-nums">{b.rangeLabel}</span>
                </div>
                <span className="text-base sm:text-lg font-extrabold mono-nums text-slate-100 block pt-0.5">
                  {b.winRate !== null ? `%${b.winRate}` : "—"}
                </span>
                <span className="text-[10px] text-slate-500 mono-nums block">
                  {b.won}/{b.total}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Fikstra Karne: Lig & Piyasa Kırılımı */}
      <div className="p-4 rounded-2xl glass-card space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">
              Fikstra Karne
            </h4>
            <p className="text-[11px] text-slate-400">
              Sonuçlanmış resmi 60+ tahminlere göre gerçek isabet dağılımı
            </p>
          </div>
          <div className="flex items-center gap-1 p-1 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
            <button
              type="button"
              onClick={() => setBreakdownTab("league")}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors ${
                breakdownTab === "league"
                  ? "bg-[#00FF85] text-[#05080D]"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Liglere Göre
            </button>
            <button
              type="button"
              onClick={() => setBreakdownTab("market")}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-colors ${
                breakdownTab === "market"
                  ? "bg-[#00FF85] text-[#05080D]"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              Piyasalara Göre
            </button>
          </div>
        </div>

        {(() => {
          const rows =
            breakdownTab === "league"
              ? data.breakdown?.by_league || []
              : data.breakdown?.by_market || [];
          if (rows.length === 0) {
            return (
              <p className="text-xs text-slate-500 py-3 text-center">
                Henüz sonuçlanmış 60+ tahmin verisi bulunmuyor.
              </p>
            );
          }
          return (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {rows.slice(0, 12).map((row) => (
                <div
                  key={row.label}
                  className="p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-semibold text-slate-100 truncate">{row.label}</span>
                    <div className="flex items-center gap-2 shrink-0 mono-nums">
                      <span className="text-[11px] text-slate-400">
                        {row.won}/{row.decided}
                      </span>
                      {row.win_rate !== null && (
                        <span className="font-extrabold text-[#00FF85]">
                          %{row.win_rate}
                        </span>
                      )}
                    </div>
                  </div>
                  {row.win_rate !== null && (
                    <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-[#00FF85]"
                        style={{ width: `${Math.min(100, Math.max(4, row.win_rate))}%` }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          );
        })()}
      </div>

      {/* Empty State vs Real Settled Chart + Filterable/Paginated Archive */}
      {data.total_settled === 0 ? (
        <div className="p-8 text-center rounded-2xl bg-[#131926] border border-[#1F2937] space-y-2">
          <Clock className="w-8 h-8 text-cyan-400 mx-auto" />
          <h3 className="text-sm font-bold text-slate-100">
            Henüz Sonuçlanmış Tahmin Bulunmuyor
          </h3>
          <p className="text-xs text-slate-400 max-w-md mx-auto">
            İlk sonuçlar maçlar tamamlandıktan sonra maç bitiminde otomatik işlenecektir. Sistem hiçbir
            sahte geçmiş performans verisi üretmez.
          </p>
        </div>
      ) : (
        <>
          {sortedTrend.length > 0 && (
            <div className="p-4 rounded-2xl glass-card">
              <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <Award className="w-4 h-4 text-emerald-400" />
                Günlük Tutturma Trendi
              </h4>
              {sortedTrend.length < 3 ? (
                <div className="flex flex-wrap gap-2">
                  {sortedTrend.map((t) => (
                    <div
                      key={t.date}
                      className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl bg-[#0B0F17] border border-emerald-500/25 text-xs mono-nums"
                    >
                      <span className="text-slate-300 font-semibold">{t.date}</span>
                      <span className="text-slate-500">·</span>
                      <span className="text-[#00FF85] font-extrabold">
                        {t.won}/{t.total}
                      </span>
                      <span className="text-slate-400 text-[11px]">(%{t.win_rate})</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="h-48 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={sortedTrend}>
                      <XAxis dataKey="date" stroke="#64748B" fontSize={10} />
                      <YAxis stroke="#64748B" fontSize={10} domain={[0, 100]} width={30} />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: "#0B0F17",
                          borderColor: "#1F2937",
                          borderRadius: "10px",
                          fontSize: "12px",
                        }}
                        formatter={(value: unknown, _name: unknown, props: { payload?: { won?: number; total?: number } }) => {
                          const won = props?.payload?.won ?? 0;
                          const total = props?.payload?.total ?? 0;
                          return [`%${value} (${won}/${total})`, "Başarı"];
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="win_rate"
                        stroke="#10B981"
                        fill="#10B981"
                        fillOpacity={0.18}
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          )}

          {/* 4. Filterable & Paginated Sonuçlanan Tahmin Arşivi */}
          <div className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1F2937] space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-bold text-slate-200">Sonuçlanan Tahmin Arşivi</span>
                <span className="text-[10px] text-slate-400">Detay için maça dokun</span>
              </div>

              {/* Status + Market Filter Chips */}
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  {(
                    [
                      { key: "ALL" as const, label: "Tümü", count: statusCounts.all },
                      { key: "WON" as const, label: "Kazandı", count: statusCounts.won },
                      { key: "LOST" as const, label: "Kaybetti", count: statusCounts.lost },
                    ]
                  ).map((chip) => {
                    const active = statusFilter === chip.key;
                    return (
                      <button
                        key={chip.key}
                        type="button"
                        onClick={() => {
                          setStatusFilter(chip.key);
                          setVisibleLimit(20);
                        }}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-bold transition-all ${
                          active
                            ? "bg-[#00FF85] text-[#05080D]"
                            : "bg-[#0B0F17] border border-white/10 text-slate-300 hover:text-white"
                        }`}
                      >
                        <span>{chip.label}</span>
                        <span className="mono-nums opacity-80">({chip.count})</span>
                      </button>
                    );
                  })}
                </div>

                {archiveMarkets.length > 2 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    {archiveMarkets.map((m) => {
                      const active = marketFilter === m.key;
                      return (
                        <button
                          key={m.key}
                          type="button"
                          onClick={() => {
                            setMarketFilter(m.key);
                            setVisibleLimit(20);
                          }}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold transition-all ${
                            active
                              ? "bg-emerald-500/20 border border-[#00FF85]/50 text-[#00FF85]"
                              : "bg-[#0B0F17] border border-white/10 text-slate-400 hover:text-slate-200"
                          }`}
                        >
                          <span>{m.label}</span>
                          <span className="mono-nums text-[10px] opacity-80">{m.count}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>

            {paginatedArchive.visible.length === 0 ? (
              <p className="p-6 text-center text-xs text-slate-400">
                Bu filtreye uyan sonuçlanmış tahmin bulunamadı.
              </p>
            ) : (
              <div className="divide-y divide-[#1F2937]">
                {paginatedArchive.visible.map((row) => {
                  const isWon = row.status === "WON";
                  const tier = getConfidenceTier(row.confidence_score);
                  return (
                    <button
                      key={row.prediction_id}
                      type="button"
                      onClick={() => onSelectMatch?.(row.match_id, row.prediction_id)}
                      className={`w-full text-left px-4 py-3 flex items-center justify-between gap-3 text-xs border-l-[3px] hover:bg-white/[0.03] transition-colors ${
                        isWon ? "border-l-[#00FF85]" : "border-l-rose-500"
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          {row.start_date_tr && (
                            <span className="text-[10px] text-slate-500 mono-nums shrink-0">
                              {row.start_date_tr} ·
                            </span>
                          )}
                          <p className="text-[10px] text-slate-400 truncate">{row.league}</p>
                          <span
                            className={`px-1.5 py-0.5 rounded border text-[10px] font-extrabold mono-nums shrink-0 ${tier.badgeCls}`}
                          >
                            {Math.round(row.confidence_score)}
                          </span>
                        </div>
                        <p className="font-semibold text-slate-100 leading-snug break-words mt-0.5">
                          {row.home_team}{" "}
                          <span className="mono-nums text-white">
                            {row.home_score ?? "-"}-{row.away_score ?? "-"}
                          </span>{" "}
                          {row.away_team}
                        </p>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <span className="text-slate-300 text-[11px] mono-nums">
                          {formatMarketLabel(row.market_key, row.selection_key)} · {row.iddaa_odd.toFixed(2)}
                        </span>
                        <span
                          className={`px-2 py-0.5 rounded-md text-[10px] font-bold ${
                            isWon
                              ? "bg-emerald-500/20 text-emerald-400"
                              : "bg-rose-500/20 text-rose-400"
                          }`}
                        >
                          {isWon ? "KAZANDI" : "KAYBETTİ"}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            {paginatedArchive.hasMore && (
              <div className="p-3 border-t border-[#1F2937] text-center">
                <button
                  type="button"
                  onClick={() => setVisibleLimit((prev) => prev + 20)}
                  className="px-4 py-2 rounded-xl bg-white/[0.04] border border-white/10 hover:border-[#00FF85]/45 text-xs font-bold text-[#00FF85] transition-colors"
                >
                  Daha Fazla Göster (+{Math.min(20, paginatedArchive.remainingCount)})
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {/* 3. Gelişmiş İstatistikler (Teknik) — Closed by Default */}
      <div className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden">
        <button
          type="button"
          onClick={() => setTechOpen((v) => !v)}
          className="w-full px-4 py-3 flex items-center justify-between text-xs font-bold text-slate-200 hover:bg-white/[0.02]"
        >
          <span>Gelişmiş İstatistikler (Teknik)</span>
          {techOpen ? (
            <ChevronUp className="w-4 h-4 text-slate-400" />
          ) : (
            <ChevronDown className="w-4 h-4 text-slate-400" />
          )}
        </button>
        {techOpen && (
          <div className="px-4 pb-4 pt-2 grid grid-cols-1 sm:grid-cols-2 gap-3 border-t border-[#1F2937]">
            <div className="p-3.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] space-y-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-bold text-slate-200">Oran Avantajı (CLV)</p>
                <span className="text-base font-extrabold mono-nums text-cyan-400">
                  {data.avg_clv_pct !== null
                    ? `${data.avg_clv_pct > 0 ? "+" : data.avg_clv_pct < 0 ? "-" : ""}%${Math.abs(data.avg_clv_pct)}`
                    : "Bekleniyor"}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 leading-relaxed">
                Tahmini yakaladığınız oranın maç başlama saatindeki kapanış oranına göre yüzde kaç daha avantajlı olduğunu gösterir.
              </p>
            </div>
            <div className="p-3.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] space-y-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-bold text-slate-200">Tahmin Tutarlılığı (Brier)</p>
                <span className="text-base font-extrabold mono-nums text-amber-400">
                  {data.brier_score !== null ? data.brier_score.toFixed(3) : "—"}
                </span>
              </div>
              <p className="text-[11px] text-slate-400 leading-relaxed">
                Model olasılıklarının gerçekleşen maç sonuçlarına ne kadar yakın olduğunu ölçer; 0&apos;a yaklaştıkça daha isabetlidir.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Collapsible Metric Glossary */}
      <div className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden">
        <button
          type="button"
          onClick={() => setGlossaryOpen((v) => !v)}
          className="w-full px-4 py-3 flex items-center justify-between text-xs font-bold text-slate-200 hover:bg-white/[0.02]"
        >
          <span>Metrik Sözlüğü (Kasa Ölçeği &amp; Takım Güç Puanı)</span>
          {glossaryOpen ? (
            <ChevronUp className="w-4 h-4 text-slate-400" />
          ) : (
            <ChevronDown className="w-4 h-4 text-slate-400" />
          )}
        </button>
        {glossaryOpen && (
          <div className="px-4 pb-4 pt-1 grid grid-cols-1 sm:grid-cols-2 gap-3 border-t border-[#1F2937]">
            <div className="p-3 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
              <div className="flex items-center justify-between">
                <p className="text-xs font-bold text-slate-200">Önerilen Kasa Ağırlığı (1.0–5.0)</p>
                <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px] font-bold mono-nums">
                  Kasa Ölçeği
                </span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Oran avantajı ve güven skoruna göre tek maça ayrılması önerilen kasa ağırlığını gösterir.
              </p>
            </div>
            <div className="p-3 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
              <div className="flex items-center justify-between">
                <p className="text-xs font-bold text-slate-200">Takım Güç Puanı</p>
                <span className="px-2 py-0.5 rounded bg-cyan-500/15 text-cyan-400 text-[10px] font-bold mono-nums">
                  Güç
                </span>
              </div>
              <p className="text-[11px] text-slate-400 mt-1">
                Avrupa kulüplerinin lig zorluğu ve uzun vadeli maç sonuçlarına göre hesaplanan güç reytingidir.
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Collapsible Virtual Bankroll (Bottom, Closed by Default) */}
      <div className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden">
        <button
          type="button"
          onClick={() => setBankrollOpen((v) => !v)}
          className="w-full px-4 py-3 flex items-center justify-between text-xs font-bold text-slate-300 hover:bg-white/[0.02]"
        >
          <span>Sabit 100 ₺ Kasa Takibi</span>
          {bankrollOpen ? (
            <ChevronUp className="w-4 h-4 text-slate-400" />
          ) : (
            <ChevronDown className="w-4 h-4 text-slate-400" />
          )}
        </button>
        {bankrollOpen && (
          <div className="px-4 pb-4 pt-2 border-t border-[#1F2937] space-y-3">
            {!data.virtual_bankroll || !data.virtual_bankroll.ready ? (
              <p className="text-xs text-slate-400">
                Dürüst simülasyon için en az 15 sonuçlanmış 60+ maç gerekli (şu an{" "}
                {data.virtual_bankroll?.decided_count ?? 0}/15).
              </p>
            ) : (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                  <div className="p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
                    <span className="text-[10px] text-slate-400 block">Net Kâr/Zarar</span>
                    <span
                      className={`text-sm font-extrabold mono-nums ${
                        (data.virtual_bankroll.net_profit ?? 0) >= 0
                          ? "text-[#00FF85]"
                          : "text-rose-400"
                      }`}
                    >
                      {(data.virtual_bankroll.net_profit ?? 0) >= 0 ? "+" : ""}
                      {data.virtual_bankroll.net_profit} ₺
                    </span>
                  </div>
                  <div className="p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
                    <span className="text-[10px] text-slate-400 block">Net Getiri</span>
                    <span className="text-sm font-extrabold mono-nums text-slate-100">
                      %{data.virtual_bankroll.roi_pct}
                    </span>
                  </div>
                  <div className="p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
                    <span className="text-[10px] text-slate-400 block">En Yüksek Tepe</span>
                    <span className="text-sm font-extrabold mono-nums text-emerald-400">
                      +{data.virtual_bankroll.peak_profit} ₺
                    </span>
                  </div>
                  <div className="p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937]">
                    <span className="text-[10px] text-slate-400 block">Maks. Düşüş</span>
                    <span className="text-sm font-extrabold mono-nums text-amber-400">
                      -{data.virtual_bankroll.max_drawdown} ₺
                    </span>
                  </div>
                </div>
                {data.virtual_bankroll.series.length > 1 && (
                  <div className="h-36 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={data.virtual_bankroll.series}>
                        <XAxis dataKey="date" tick={{ fill: "#94a3b8", fontSize: 10 }} />
                        <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} width={40} />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: "#0B0F17",
                            borderColor: "#1F2937",
                            fontSize: "11px",
                          }}
                        />
                        <Area
                          type="monotone"
                          dataKey="cumulative_profit"
                          stroke="#00FF85"
                          fill="#00FF85"
                          fillOpacity={0.15}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
