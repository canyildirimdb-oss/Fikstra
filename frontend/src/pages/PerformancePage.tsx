import { useEffect, useMemo, useState } from "react";
import { Award, ChevronDown, ChevronUp, Clock, Info } from "lucide-react";
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

interface Props {
  refreshTrigger: number;
  onSelectMatch?: (matchId: number, predictionId?: number) => void;
}

const TIER_DISPLAY: Record<"BANKO" | "DENGELI" | "SURPRIZ", string> = {
  BANKO: "BANKO",
  DENGELI: "DENGELİ",
  SURPRIZ: "SÜRPRİZ",
};

function MetricInfo({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex items-center ml-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-slate-500 hover:text-cyan-400 transition-colors"
        aria-label="Bilgi"
      >
        <Info className="w-3.5 h-3.5" />
      </button>
      {open && (
        <span className="absolute left-0 top-5 z-20 w-56 p-2.5 rounded-xl bg-[#090D16] border border-cyan-500/40 text-[11px] leading-snug text-slate-200 shadow-xl font-normal">
          {text}
        </span>
      )}
    </span>
  );
}

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
  const [glossaryOpen, setGlossaryOpen] = useState(false);
  const [bankrollOpen, setBankrollOpen] = useState(false);
  const [breakdownTab, setBreakdownTab] = useState<"league" | "market">("league");

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

  if (loading || !data) {
    return <div className="h-48 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />;
  }

  return (
    <div className="space-y-4">
      {/* Human-Language Trust Hero */}
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
            {data.total_settled > 0
              ? `${data.won_count} Kazandı • ${data.lost_count} Kaybetti`
              : "Maçlar tamamlandığında resmi 90 dk skorlarıyla otomatik işlenir."}
          </p>
          <p className="text-[11px] text-emerald-300/75 mt-1.5">
            Burada yalnızca Analizler ekranında maç başlamadan önce yayınlanan resmi 60+ tahminler yer alır.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {data.archive.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap max-w-[200px]" aria-label="Son sonuçlar">
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
          <div className="px-3.5 py-2 rounded-xl bg-[#05080D]/80 border border-emerald-500/25 text-right shrink-0">
            <span className="text-[10px] text-slate-400 block">Başarı Oranı</span>
            <span className="text-xl font-extrabold mono-nums text-[#00FF85]">
              {data.overall_win_rate !== null ? `%${data.overall_win_rate}` : "—"}
            </span>
          </div>
        </div>
      </div>

      {/* Top Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <div className="p-4 rounded-2xl glass-card">
          <p className="text-[11px] text-slate-400 flex items-center">
            Oran Avantajı
            <MetricInfo text="Tahmini yakaladığınız oranın maç başlama saatindeki kapanış oranına göre yüzde kaç daha avantajlı olduğunu gösterir." />
          </p>
          <p
            className={`font-extrabold mono-nums text-cyan-400 mt-1 ${
              data.avg_clv_pct !== null ? "text-xl sm:text-2xl" : "text-base sm:text-lg"
            }`}
          >
            {data.avg_clv_pct !== null
              ? `${data.avg_clv_pct > 0 ? "+" : data.avg_clv_pct < 0 ? "-" : ""}%${Math.abs(data.avg_clv_pct)}`
              : "Bekleniyor"}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">Kapanış oranına göre fark</p>
        </div>

        <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
          <p className="text-[11px] text-slate-400 flex items-center">
            Tahmin Tutarlılığı
            <MetricInfo text="Model olasılıklarının gerçek maç sonuçlarına yakınlığını ölçer; 0'a yaklaştıkça daha isabetlidir." />
          </p>
          <p className="text-xl sm:text-2xl font-extrabold mono-nums text-amber-400 mt-1">
            {data.brier_score !== null ? data.brier_score.toFixed(3) : "—"}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">0&apos;a yaklaştıkça daha isabetli</p>
        </div>

        {(["BANKO", "DENGELI", "SURPRIZ"] as const).map((tier) => {
          const st = data.coupon_tiers[tier] || { total: 0, won: 0, win_rate: null };
          const lowSample = st.total < 10;
          return (
            <div key={tier} className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
              <div className="flex items-center justify-between gap-1">
                <p className="text-[11px] text-slate-400">{TIER_DISPLAY[tier]} Kupon</p>
                {lowSample && (
                  <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[9px] font-semibold">
                    Az veri
                  </span>
                )}
              </div>
              <p className="text-2xl font-extrabold mono-nums text-cyan-400 mt-1">
                {lowSample
                  ? `${st.won}/${st.total}`
                  : st.win_rate !== null
                  ? `%${st.win_rate}`
                  : "—"}
              </p>
              <p className="text-[10px] text-slate-500 mt-0.5">
                {lowSample ? "Örneklem < 10 kupon" : `${st.won}/${st.total} tuttu`}
              </p>
            </div>
          );
        })}
      </div>

      {/* Confidence Band Calibration Mini Table */}
      <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
        <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
          GÜVEN PUANINA GÖRE İSABET
        </h4>
        <div className="grid grid-cols-3 gap-2">
          {(data.calibration_bands || []).map((b) => {
            const lowSample = b.total < 10;
            return (
              <div
                key={b.band}
                className="p-3 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-center"
              >
                <span className="text-[11px] text-slate-400 block">{b.band} Puan</span>
                <span className="text-base font-bold mono-nums text-slate-100 mt-0.5 block">
                  {lowSample
                    ? `${b.won}/${b.total}`
                    : b.win_rate !== null
                    ? `%${b.win_rate}`
                    : "Bekleniyor"}
                </span>
                <span className="text-[10px] text-slate-500 mono-nums block mt-0.5">
                  {lowSample ? "Az veri" : `${b.won}/${b.total}`}
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
            <div className="space-y-2">
              {rows.slice(0, 12).map((row) => (
                <div
                  key={row.label}
                  className="p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] space-y-1.5"
                >
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-semibold text-slate-100 truncate">{row.label}</span>
                    <div className="flex items-center gap-2 shrink-0 mono-nums">
                      {row.low_sample ? (
                        <>
                          <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px] font-semibold">
                            Az veri
                          </span>
                          <span className="font-bold text-slate-200">
                            {row.won}/{row.decided}
                          </span>
                        </>
                      ) : (
                        <>
                          <span className="text-[11px] text-slate-400">
                            {row.won}/{row.decided}
                          </span>
                          <span className="font-extrabold text-[#00FF85]">
                            %{row.win_rate}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                  {!row.low_sample && row.win_rate !== null && (
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

      {/* Empty State vs Real Settled Chart + Archive */}
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
            <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
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

          <div className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1F2937] flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-bold text-slate-200">Sonuçlanan Tahmin Arşivi</span>
              <span className="text-[10px] text-slate-400">Detay için maça dokun</span>
            </div>
            <div className="divide-y divide-[#1F2937]">
              {data.archive.map((row) => {
                const isWon = row.status === "WON";
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
                        <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-[#00FF85] text-[10px] font-extrabold mono-nums shrink-0">
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
          </div>
        </>
      )}

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
