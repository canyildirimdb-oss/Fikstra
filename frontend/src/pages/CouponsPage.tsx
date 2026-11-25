import { useEffect, useState } from "react";
import { Layers, Share2, ShieldCheck, Sparkles, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { CouponShareModal } from "../components/CouponShareModal";
import { TeamBadge } from "../components/TeamBadge";
import type { CouponData, CustomSlipItem } from "../types";
import type { ShareCouponPayload } from "../utils/couponShare";
import { formatMarketLabel } from "../utils/format";

interface Props {
  slipItems: CustomSlipItem[];
  onRemoveSlipItem: (matchId: number) => void;
  onClearSlip: () => void;
  onSelectMatch: (
    matchId: number,
    couponPick?: { market_key: string; selection_key: string; iddaa_odd: number }
  ) => void;
  refreshTrigger: number;
}

const TIER_META: Record<
  string,
  { title: string; subtitle: string; badge: string; icon: typeof ShieldCheck }
> = {
  BANKO: {
    title: "Banko Kupon",
    subtitle: "En yüksek güven • 1.30–1.55 oran bandı",
    badge: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
    icon: ShieldCheck,
  },
  DENGELI: {
    title: "Dengeli Kupon",
    subtitle: "Güven & oran dengesi • 1.45–2.00 bandı",
    badge: "bg-cyan-500/15 text-cyan-400 border-cyan-500/30",
    icon: Layers,
  },
  SURPRIZ: {
    title: "Sürpriz Değer Kuponu",
    subtitle: "Değerli global fark & düşüş • 1.90–3.60",
    badge: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    icon: Sparkles,
  },
};

export function CouponsPage({
  slipItems,
  onRemoveSlipItem,
  onClearSlip,
  onSelectMatch,
  refreshTrigger,
}: Props) {
  const [coupons, setCoupons] = useState<CouponData[]>([]);
  const [loading, setLoading] = useState(true);
  const [sharePayload, setSharePayload] = useState<ShareCouponPayload | null>(null);

  useEffect(() => {
    setLoading(true);
    api
      .getCoupons()
      .then((res) => setCoupons(res.coupons))
      .finally(() => setLoading(false));
  }, [refreshTrigger]);

  const customTotalOdd = slipItems.reduce((acc, x) => acc * x.iddaa_odd, 1);

  const openCustomShare = () => {
    const todayStr = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Istanbul" });
    const textPayload =
      `Fikstra • Kendi Özel Kuponum (${todayStr})\n` +
      slipItems
        .map(
          (s) =>
            `• ${s.home_team} - ${s.away_team} | ${formatMarketLabel(s.market_key, s.selection_key)} @ ${s.iddaa_odd.toFixed(2)}`
        )
        .join("\n") +
      `\nToplam Oran: ${customTotalOdd.toFixed(2)}`;
    setSharePayload({
      tier: "CUSTOM",
      title: "Kendi Özel Kuponum",
      couponDate: todayStr,
      totalOdd: customTotalOdd,
      avgConfidence: null,
      items: slipItems,
      textPayload,
    });
  };

  return (
    <div className="space-y-5">
      {/* Custom User Slip */}
      {slipItems.length > 0 && (
        <div className="p-4 rounded-2xl bg-gradient-to-br from-emerald-500/10 to-[#131926] border border-emerald-500/35 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-emerald-300">Kendi Özel Kuponum</h3>
              <p className="text-[11px] text-slate-400">
                {slipItems.length} maç seçildi • Toplam Oran:{" "}
                <span className="font-extrabold text-white mono-nums">
                  {customTotalOdd.toFixed(2)}
                </span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={openCustomShare}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold"
              >
                <Share2 className="w-3.5 h-3.5" />
                Kuponu Paylaş
              </button>
              <button
                onClick={onClearSlip}
                className="p-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400 hover:text-rose-400"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="space-y-1.5">
            {slipItems.map((s) => (
              <div
                key={s.match_id}
                className="flex items-center justify-between px-3 py-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-xs"
              >
                <span className="text-slate-200 truncate">
                  {s.home_team} - {s.away_team}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-emerald-400 font-semibold">
                    {formatMarketLabel(s.market_key, s.selection_key)}
                  </span>
                  <span className="mono-nums font-bold text-white">{s.iddaa_odd.toFixed(2)}</span>
                  <button
                    onClick={() => onRemoveSlipItem(s.match_id)}
                    className="text-slate-500 hover:text-rose-400 ml-1"
                  >
                    ×
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Daily Auto Coupons */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((n) => (
            <div key={n} className="h-64 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {(["BANKO", "DENGELI", "SURPRIZ"] as const).map((tierKey) => {
            const c = coupons.find((x) => x.tier === tierKey);
            const meta = TIER_META[tierKey] || TIER_META.BANKO;
            const Icon = meta.icon;

            if (!c) {
              return (
                <div
                  key={tierKey}
                  className="flex flex-col justify-between p-4 rounded-2xl bg-[#131926] border border-[#1F2937] opacity-85"
                >
                  <div>
                    <span
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-bold ${meta.badge}`}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      {meta.title}
                    </span>
                    <p className="text-[11px] text-slate-400 mt-1.5">{meta.subtitle}</p>
                  </div>
                  <div className="my-8 p-4 rounded-xl bg-[#0B0F17] border border-dashed border-[#1F2937] text-center">
                    <p className="text-xs text-slate-400 leading-relaxed">
                      Bugün bu profil için kalite barajını geçen yeterli maç yok.
                    </p>
                  </div>
                  <div className="pt-3 border-t border-[#1F2937] text-[11px] text-slate-500">
                    Kalite koruması aktif
                  </div>
                </div>
              );
            }

            const textPayload =
              `Fikstra • ${meta.title} (${c.coupon_date})\n` +
              c.items
                .map(
                  (it) =>
                    `• ${it.home_team} - ${it.away_team} | ${formatMarketLabel(it.market_key, it.selection_key)} @ ${it.iddaa_odd.toFixed(2)}`
                )
                .join("\n") +
              `\nToplam Oran: ${c.total_odd.toFixed(2)}`;

            return (
              <div
                key={c.id}
                className="flex flex-col justify-between p-4 rounded-2xl bg-[#131926] border border-[#1F2937]"
              >
                <div>
                  <div className="flex items-start justify-between gap-2 mb-3">
                    <div>
                      <span
                        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-bold ${meta.badge}`}
                      >
                        <Icon className="w-3.5 h-3.5" />
                        {meta.title}
                      </span>
                      <p className="text-[11px] text-slate-400 mt-1.5">{meta.subtitle}</p>
                    </div>
                    <div className="text-right">
                      <span className="text-[10px] text-slate-400 block">Toplam</span>
                      <span className="text-lg font-extrabold mono-nums text-white">
                        {c.total_odd.toFixed(2)}
                      </span>
                    </div>
                  </div>

                  <div className="space-y-2 my-3">
                    {c.items.map((it) => (
                      <div
                        key={it.prediction_id}
                        onClick={() =>
                          onSelectMatch(it.match_id, {
                            market_key: it.market_key,
                            selection_key: it.selection_key,
                            iddaa_odd: it.iddaa_odd,
                          })
                        }
                        className="cursor-pointer p-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] hover:border-slate-600"
                      >
                        <div className="flex justify-between text-[10px] text-slate-400 mb-0.5">
                          <span className="truncate">{it.league}</span>
                          <span className="mono-nums shrink-0">{it.start_time_tr}</span>
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                          <TeamBadge name={it.home_team} logoUrl={it.home_logo_url} size="sm" />
                          <p className="text-xs font-semibold text-slate-100 truncate">
                            {it.home_team} vs {it.away_team}
                          </p>
                          <TeamBadge name={it.away_team} logoUrl={it.away_logo_url} size="sm" />
                        </div>
                        <div className="flex items-center justify-between mt-1.5 text-xs">
                          <span className="text-emerald-400 font-bold">
                            {formatMarketLabel(it.market_key, it.selection_key)}
                          </span>
                          <div className="flex items-center gap-2 mono-nums">
                            <span className="text-slate-400 text-[11px]">
                              Güven {Math.round(it.confidence_score)}
                            </span>
                            <span className="font-bold text-white">{it.iddaa_odd.toFixed(2)}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="pt-3 border-t border-[#1F2937] flex items-center justify-between">
                  <span className="text-[11px] text-slate-400 mono-nums">
                    Ort. Güven: {c.avg_confidence.toFixed(1)} / 100
                  </span>
                  <button
                    onClick={() =>
                      setSharePayload({
                        tier: (c.tier as "BANKO" | "DENGELI" | "SURPRIZ") || "BANKO",
                        title: meta.title,
                        couponDate: c.coupon_date,
                        totalOdd: c.total_odd,
                        avgConfidence: c.avg_confidence,
                        items: c.items,
                        textPayload,
                      })
                    }
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-500/15 border border-emerald-500/35 hover:bg-emerald-500/25 text-xs font-bold text-emerald-300"
                  >
                    <Share2 className="w-3.5 h-3.5" /> Kuponu Paylaş
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <CouponShareModal payload={sharePayload} onClose={() => setSharePayload(null)} />
    </div>
  );
}
