import { useEffect, useState } from "react";
import { Award, Clock, Info } from "lucide-react";
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
import { formatMarketLabel } from "../utils/format";

interface Props {
  refreshTrigger: number;
}

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

export function PerformancePage({ refreshTrigger }: Props) {
  const [data, setData] = useState<PerformanceData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getPerformance()
      .then(setData)
      .finally(() => setLoading(false));
  }, [refreshTrigger]);

  if (loading || !data) {
    return <div className="h-48 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />;
  }

  return (
    <div className="space-y-4">
      {/* Top Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
          <p className="text-[11px] text-slate-400">Genel Tutturma</p>
          <p className="text-2xl font-extrabold mono-nums text-emerald-400 mt-1">
            {data.overall_win_rate !== null ? `%${data.overall_win_rate}` : "—"}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">
            {data.won_count} Kazandı / {data.lost_count} Kaybetti
          </p>
        </div>

        <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
          <p className="text-[11px] text-slate-400 flex items-center">
            Ortalama CLV %
            <MetricInfo text="Tahmini yakaladığınız oranın maç başlama saatindeki kapanış oranına göre yüzde kaç daha avantajlı olduğunu gösterir." />
          </p>
          <p
            className={`font-extrabold mono-nums text-cyan-400 mt-1 ${
              data.avg_clv_pct !== null ? "text-xl sm:text-2xl" : "text-base sm:text-lg"
            }`}
          >
            {data.avg_clv_pct !== null ? `${data.avg_clv_pct > 0 ? "+" : ""}%${data.avg_clv_pct}` : "Bekleniyor"}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">Kapanış oranı farkı</p>
        </div>

        <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
          <p className="text-[11px] text-slate-400 flex items-center">
            Brier Skoru
            <MetricInfo text="Model olasılıklarının gerçek maç sonuçlarına yakınlığını ölçer; 0.25 altı başarılı, sıfıra yaklaştıkça daha keskindir." />
          </p>
          <p className="text-xl sm:text-2xl font-extrabold mono-nums text-amber-400 mt-1">
            {data.brier_score !== null ? data.brier_score.toFixed(3) : "—"}
          </p>
          <p className="text-[10px] text-slate-500 mt-0.5">Düşük = daha iyi kalibre</p>
        </div>

        {(["BANKO", "DENGELI", "SURPRIZ"] as const).map((tier) => {
          const st = data.coupon_tiers[tier] || { total: 0, won: 0, win_rate: null };
          return (
            <div key={tier} className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
              <p className="text-[11px] text-slate-400">{tier} Kupon</p>
              <p className="text-2xl font-extrabold mono-nums text-cyan-400 mt-1">
                {st.win_rate !== null ? `%${st.win_rate}` : "—"}
              </p>
              <p className="text-[10px] text-slate-500 mt-0.5">
                {st.won}/{st.total} tuttu
              </p>
            </div>
          );
        })}
      </div>

      {/* Quant Glossary Strip (Kelly Birimi & ClubElo) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="p-3.5 rounded-2xl bg-[#131926] border border-[#1F2937] flex items-center justify-between gap-2">
          <div>
            <p className="text-xs font-bold text-slate-200 flex items-center">
              Kelly Birimi (1–5)
              <MetricInfo text="Oran avantajı ve güven skoruna göre tek maça ayrılması önerilen kasa ağırlığını (1 en düşük, 5 en yüksek) gösterir." />
            </p>
            <p className="text-[11px] text-slate-400 mt-0.5">Kasa yönetimi birim ölçeği</p>
          </div>
          <span className="px-2.5 py-1 rounded-lg bg-emerald-500/15 text-emerald-400 text-xs font-bold mono-nums">
            1.0–5.0
          </span>
        </div>
        <div className="p-3.5 rounded-2xl bg-[#131926] border border-[#1F2937] flex items-center justify-between gap-2">
          <div>
            <p className="text-xs font-bold text-slate-200 flex items-center">
              ClubElo Güç Puanı
              <MetricInfo text="Avrupa kulüplerinin lig zorluğu ve uzun vadeli maç sonuçlarına göre hesaplanan uluslararası güç reytingidir." />
            </p>
            <p className="text-[11px] text-slate-400 mt-0.5">Lig kalitesi ve takım gücü</p>
          </div>
          <span className="px-2.5 py-1 rounded-lg bg-cyan-500/15 text-cyan-400 text-xs font-bold mono-nums">
            Elo
          </span>
        </div>
      </div>

      {/* Confidence Band Calibration Mini Table */}
      <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
        <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
          Güven Bandı Kalibrasyonu
        </h4>
        <div className="grid grid-cols-3 gap-2">
          {(data.calibration_bands || []).map((b) => (
            <div
              key={b.band}
              className="p-3 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-center"
            >
              <span className="text-[11px] text-slate-400 block">{b.band} Puan</span>
              <span className="text-base font-bold mono-nums text-slate-100 mt-0.5 block">
                {b.win_rate !== null ? `%${b.win_rate}` : "Bekleniyor"}
              </span>
              <span className="text-[10px] text-slate-500 mono-nums">
                {b.won}/{b.total}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Empty State vs Real Settled Chart + Archive */}
      {data.total_settled === 0 ? (
        <div className="p-8 text-center rounded-2xl bg-[#131926] border border-[#1F2937] space-y-2">
          <Clock className="w-8 h-8 text-cyan-400 mx-auto" />
          <h3 className="text-sm font-bold text-slate-100">
            Henüz Sonuçlanmış Tahmin Bulunmuyor
          </h3>
          <p className="text-xs text-slate-400 max-w-md mx-auto">
            İlk sonuçlar maçlar tamamlandıktan 130 dk sonra otomatik işlenecektir. Sistem hiçbir
            sahte geçmiş performans verisi üretmez.
          </p>
        </div>
      ) : (
        <>
          {data.trend.length > 0 && (
            <div className="p-4 rounded-2xl bg-[#131926] border border-[#1F2937]">
              <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <Award className="w-4 h-4 text-emerald-400" />
                Günlük Tutturma Trendi (%)
              </h4>
              <div className="h-48 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data.trend}>
                    <XAxis dataKey="date" stroke="#64748B" fontSize={10} />
                    <YAxis stroke="#64748B" fontSize={10} domain={[0, 100]} width={30} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#0B0F17",
                        borderColor: "#1F2937",
                        borderRadius: "10px",
                        fontSize: "12px",
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
            </div>
          )}

          <div className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1F2937] text-xs font-bold text-slate-200">
              Sonuçlanan Tahmin Arşivi
            </div>
            <div className="divide-y divide-[#1F2937]">
              {data.archive.map((row) => (
                <div
                  key={row.prediction_id}
                  className="px-4 py-3 flex items-center justify-between gap-3 text-xs"
                >
                  <div className="min-w-0">
                    <p className="text-[10px] text-slate-400 truncate">{row.league}</p>
                    <p className="font-semibold text-slate-100 truncate">
                      {row.home_team} {row.home_score ?? "-"}-{row.away_score ?? "-"} {row.away_team}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-slate-300">
                      {formatMarketLabel(row.market_key, row.selection_key)} ({row.iddaa_odd.toFixed(2)})
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded-md text-[10px] font-bold ${
                        row.status === "WON"
                          ? "bg-emerald-500/20 text-emerald-400"
                          : "bg-rose-500/20 text-rose-400"
                      }`}
                    >
                      {row.status === "WON" ? "KAZANDI" : "KAYBETTİ"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
