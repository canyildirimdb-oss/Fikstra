import { useEffect, useMemo, useState } from "react";
import { ArrowDownToLine, BookmarkPlus, Layers, Link2, Lock, Share2, ShieldCheck, Sparkles, Trash2 } from "lucide-react";
import { api } from "../api/client";
import { CouponShareModal } from "../components/CouponShareModal";
import { SportotoView } from "../components/SportotoView";
import { StakeCalculator } from "../components/StakeCalculator";
import { TeamBadge } from "../components/TeamBadge";
import type { CouponData, CustomSlipItem, PredictionStatusItem, SavedSlip } from "../types";
import type { ShareCouponPayload } from "../utils/couponShare";
import { formatMarketLabel } from "../utils/format";

interface Props {
  subTab?: "active" | "history" | "sportoto" | "tracked";
  onSubTabChange?: (next: "active" | "history" | "sportoto" | "tracked") => void;
  slipItems: CustomSlipItem[];
  stake: number;
  onChangeStake: (next: number) => void;
  onRemoveSlipItem: (matchId: number) => void;
  onClearSlip: () => void;
  onImportSlip: (items: CustomSlipItem[]) => void;
  onSelectMatch: (
    matchId: number,
    couponPick?: { market_key: string; selection_key: string; iddaa_odd: number }
  ) => void;
  savedSlips: SavedSlip[];
  onSaveSlip: () => void;
  onCopySlipLink: () => void;
  onDeleteSavedSlip: (slipId: string) => void;
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
  subTab: controlledSubTab,
  onSubTabChange,
  slipItems,
  stake,
  onChangeStake,
  onRemoveSlipItem,
  onClearSlip,
  onImportSlip,
  onSelectMatch,
  savedSlips,
  onSaveSlip,
  onCopySlipLink,
  onDeleteSavedSlip,
  refreshTrigger,
}: Props) {
  const [localSubTab, setLocalSubTab] = useState<"active" | "history" | "sportoto" | "tracked">("active");
  const subTab = controlledSubTab ?? localSubTab;
  const setSubTab = (next: "active" | "history" | "sportoto" | "tracked") => {
    setLocalSubTab(next);
    onSubTabChange?.(next);
  };
  const [trackedStatuses, setTrackedStatuses] = useState<Record<number, PredictionStatusItem>>({});
  const [trackedLoading, setTrackedLoading] = useState(false);
  const [mobileTier, setMobileTier] = useState<"BANKO" | "DENGELI" | "SURPRIZ">("BANKO");
  const [lockInfoId, setLockInfoId] = useState<number | null>(null);
  const [coupons, setCoupons] = useState<CouponData[]>([]);
  const [historyCoupons, setHistoryCoupons] = useState<CouponData[]>([]);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [sharePayload, setSharePayload] = useState<ShareCouponPayload | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (coupons.length === 0) setLoading(true);
    if (historyCoupons.length === 0) setHistoryLoading(true);
    Promise.all([
      api.getCoupons().then((res) => {
        if (!cancelled) setCoupons(res.coupons);
      }),
      api.getCouponHistory().then((res) => {
        if (!cancelled) setHistoryCoupons(res.coupons);
      }),
    ])
      .catch(() => {})
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
          setHistoryLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTrigger]);

  const trackedPredIds = useMemo(() => {
    const ids: number[] = [];
    for (const slip of savedSlips) {
      for (const item of slip.items) {
        if (item.prediction_id) ids.push(item.prediction_id);
      }
    }
    return Array.from(new Set(ids)).slice(0, 30);
  }, [savedSlips]);

  useEffect(() => {
    if (subTab !== "tracked" || trackedPredIds.length === 0) return;
    let cancelled = false;
    setTrackedLoading(true);
    api
      .getPredictionStatuses(trackedPredIds)
      .then((res) => {
        if (cancelled) return;
        const map: Record<number, PredictionStatusItem> = {};
        for (const item of res.items) {
          map[item.id] = item;
        }
        setTrackedStatuses(map);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setTrackedLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [subTab, trackedPredIds, refreshTrigger]);

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
      {/* Güncel Kuponlar | Geçmiş Kuponlar | Spor Toto 15'li | Takip Ettiklerim Switch */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setSubTab("active")}
          className={`px-3.5 py-2 rounded-full text-xs font-bold transition-all ${
            subTab === "active"
              ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.75)]"
              : "glass-card text-slate-300 hover:text-white"
          }`}
        >
          Güncel Kuponlar
        </button>
        <button
          onClick={() => setSubTab("history")}
          className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-xs font-bold transition-all ${
            subTab === "history"
              ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.75)]"
              : "glass-card text-slate-300 hover:text-white"
          }`}
        >
          Geçmiş Kuponlar
          {historyCoupons.length > 0 && (
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] mono-nums ${
                subTab === "history"
                  ? "bg-[#05080D]/20 text-[#05080D]"
                  : "bg-white/10 text-slate-300"
              }`}
            >
              {historyCoupons.length}
            </span>
          )}
        </button>
        <button
          onClick={() => setSubTab("sportoto")}
          className={`px-3.5 py-2 rounded-full text-xs font-bold transition-all ${
            subTab === "sportoto"
              ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.75)]"
              : "glass-card text-slate-300 hover:text-white"
          }`}
        >
          Spor Toto 15&apos;li
        </button>
        <button
          onClick={() => setSubTab("tracked")}
          className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-full text-xs font-bold transition-all ${
            subTab === "tracked"
              ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_18px_-4px_rgba(0,255,133,0.75)]"
              : "glass-card text-slate-300 hover:text-white"
          }`}
        >
          Takip Ettiklerim
          {savedSlips.length > 0 && (
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] mono-nums ${
                subTab === "tracked"
                  ? "bg-[#05080D]/20 text-[#05080D]"
                  : "bg-white/10 text-slate-300"
              }`}
            >
              {savedSlips.length}
            </span>
          )}
        </button>
      </div>

      {/* Spor Toto 15'li View */}
      {subTab === "sportoto" && <SportotoView refreshTrigger={refreshTrigger} />}

      {/* Tracked User Slips (localStorage + live DB status) */}
      {subTab === "tracked" && (
        <div className="space-y-3">
          {savedSlips.length === 0 ? (
            <div className="p-8 text-center rounded-2xl glass-card space-y-2">
              <BookmarkPlus className="w-7 h-7 text-[#00FF85] mx-auto" />
              <h3 className="text-sm font-bold text-slate-100">Henüz Takip Edilen Kupon Yok</h3>
              <p className="text-xs text-slate-400 max-w-md mx-auto">
                Kuponum fişine maç ekleyip &ldquo;Kuponu Takibe Al&rdquo; butonuna bastığında burada
                üyeliksiz olarak canlı sonuçlarını takip edebilirsin.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {savedSlips.map((slip) => {
                const legStatuses = slip.items.map((it) =>
                  it.prediction_id ? trackedStatuses[it.prediction_id]?.status || "PENDING" : "PENDING"
                );
                const wonLegs = legStatuses.filter((s) => s === "WON").length;
                const lostLegs = legStatuses.filter((s) => s === "LOST").length;
                const voidLegs = legStatuses.filter((s) => s === "VOID").length;
                const allSettled = legStatuses.every((s) => s !== "PENDING");

                let slipStatus = "BEKLEMEDE";
                let statusCls = "bg-slate-800 text-slate-300 border-slate-700";
                if (lostLegs > 0) {
                  slipStatus = "KAYBETTİ";
                  statusCls = "bg-rose-500/15 text-rose-300 border-rose-500/35";
                } else if (allSettled && voidLegs === legStatuses.length) {
                  slipStatus = "İADE/VOID";
                  statusCls = "bg-slate-700/60 text-slate-200 border-slate-600";
                } else if (allSettled && wonLegs > 0) {
                  slipStatus = "KAZANDI";
                  statusCls = "bg-emerald-500/15 text-[#00FF85] border-emerald-500/35";
                }

                const createdLabel = new Date(slip.created_at).toLocaleString("tr-TR", {
                  timeZone: "Europe/Istanbul",
                  day: "2-digit",
                  month: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                });

                return (
                  <div key={slip.id} className="p-4 rounded-2xl glass-card space-y-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold border ${statusCls}`}>
                            {slipStatus}
                          </span>
                          <span className="text-[11px] font-bold text-slate-300 mono-nums">
                            {wonLegs}/{slip.items.length} tuttu
                          </span>
                        </div>
                        <p className="text-[10px] text-slate-500 mt-1 mono-nums">
                          {createdLabel} · {slip.total_odd.toFixed(2)} Oran · {slip.stake} ₺
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => onDeleteSavedSlip(slip.id)}
                        className="p-2 rounded-xl bg-[#0B0F17] border border-white/10 text-slate-400 hover:text-rose-400"
                        aria-label="Kayıtlı kuponu sil"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    {trackedLoading && (
                      <p className="text-[10px] text-slate-500">Canlı skorlar kontrol ediliyor...</p>
                    )}

                    <div className="space-y-1.5">
                      {slip.items.map((leg, i) => {
                        const live = leg.prediction_id ? trackedStatuses[leg.prediction_id] : undefined;
                        const st = live?.status || "PENDING";
                        const score =
                          live && live.home_score !== null && live.away_score !== null
                            ? `${live.home_score}-${live.away_score}`
                            : null;
                        return (
                          <div
                            key={`${slip.id}-${leg.match_id}-${i}`}
                            className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-xl bg-[#05080D]/75 border border-white/10 text-xs"
                          >
                            <div className="min-w-0">
                              <p className="text-slate-200 font-semibold truncate">
                                {leg.home_team} - {leg.away_team}
                              </p>
                              <p className="text-[10px] text-emerald-400 font-bold">
                                {formatMarketLabel(leg.market_key, leg.selection_key)} @ {leg.iddaa_odd.toFixed(2)}
                              </p>
                            </div>
                            <div className="flex items-center gap-2 shrink-0 mono-nums">
                              {score && (
                                <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-200 text-[10px] font-bold">
                                  {score}
                                </span>
                              )}
                              <span
                                className={`text-[10px] font-extrabold ${
                                  st === "WON"
                                    ? "text-[#00FF85]"
                                    : st === "LOST"
                                    ? "text-rose-400"
                                    : st === "VOID"
                                    ? "text-slate-400"
                                    : "text-amber-300"
                                }`}
                              >
                                {st === "WON" ? "TUTTU" : st === "LOST" ? "YATTI" : st === "VOID" ? "İADE" : "BEKLİYOR"}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Custom User Slip */}
      {subTab === "active" && slipItems.length > 0 && (
        <div className="p-4 rounded-2xl glass-card border-[#00FF85]/40 neon-bottom-glow space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-[#00FF85]">Kendi Özel Kuponum</h3>
              <p className="text-[11px] text-slate-400">
                {slipItems.length} maç seçildi • Toplam Oran:{" "}
                <span className="font-extrabold text-white mono-nums">
                  {customTotalOdd.toFixed(2)}
                </span>
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={onCopySlipLink}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl glass-card text-slate-200 hover:border-[#00FF85]/45 text-xs font-bold"
              >
                <Link2 className="w-3.5 h-3.5 text-[#00FF85]" />
                Linkle Gönder
              </button>
              <button
                type="button"
                onClick={onSaveSlip}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-emerald-500/15 border border-emerald-500/35 text-[#00FF85] text-xs font-bold hover:bg-emerald-500/25"
              >
                <BookmarkPlus className="w-3.5 h-3.5" />
                Kuponu Takibe Al
              </button>
              <button
                onClick={openCustomShare}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-bold shadow-[0_0_16px_-4px_rgba(0,255,133,0.6)]"
              >
                <Share2 className="w-3.5 h-3.5" />
                Kuponu Paylaş
              </button>
              <button
                onClick={onClearSlip}
                className="p-2 rounded-xl bg-[#05080D]/80 border border-white/10 text-slate-400 hover:text-rose-400"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className="space-y-1.5">
            {slipItems.map((s) => (
              <div
                key={s.match_id}
                className="flex items-center justify-between px-3 py-2 rounded-xl bg-[#05080D]/75 border border-white/10 text-xs"
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

          <StakeCalculator
            totalOdd={customTotalOdd}
            stake={stake}
            onChangeStake={onChangeStake}
          />
        </div>
      )}

      {/* Daily Auto Coupons */}
      {subTab === "active" && (loading ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((n) => (
            <div key={n} className="h-64 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {/* Mobile Tier Segment Selector */}
          <div className="md:hidden grid grid-cols-3 gap-1.5 p-1 rounded-xl glass-card">
            {(
              [
                { key: "BANKO", label: "Banko" },
                { key: "DENGELI", label: "Dengeli" },
                { key: "SURPRIZ", label: "Sürpriz" },
              ] as const
            ).map((t) => {
              const found = coupons.find((x) => x.tier === t.key);
              const active = mobileTier === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setMobileTier(t.key)}
                  className={`py-2 px-2 rounded-lg text-xs font-bold transition-all flex items-center justify-center gap-1 mono-nums ${
                    active
                      ? "bg-[#00FF85] text-[#05080D] shadow-[0_0_12px_-3px_rgba(0,255,133,0.6)]"
                      : "text-slate-300"
                  }`}
                >
                  <span>{t.label}</span>
                  {found && (
                    <span className={active ? "text-[#05080D]/80" : "text-emerald-400"}>
                      {found.total_odd.toFixed(2)}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {(["BANKO", "DENGELI", "SURPRIZ"] as const).map((tierKey) => {
            const c = coupons.find((x) => x.tier === tierKey);
            const meta = TIER_META[tierKey] || TIER_META.BANKO;
            const Icon = meta.icon;
            const mobileVis = mobileTier === tierKey ? "flex" : "hidden md:flex";

            if (!c) {
              return (
                <div
                  key={tierKey}
                  className={`${mobileVis} flex-col justify-between p-4 rounded-2xl glass-card opacity-85`}
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
                className={`${mobileVis} flex-col justify-between p-4 rounded-2xl glass-card neon-bottom-glow`}
              >
                <div>
                  <div className="flex items-start justify-between gap-2 mb-3">
                    <div>
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-bold ${meta.badge}`}
                        >
                          <Icon className="w-3.5 h-3.5" />
                          {meta.title}
                        </span>
                        {/* Leg progress dots */}
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-white/[0.04]" title="Ayak durumu">
                          {c.items.map((it) => (
                            <span
                              key={it.prediction_id}
                              className={`w-2 h-2 rounded-full ${
                                it.leg_result === "WON"
                                  ? "bg-[#00FF85]"
                                  : it.leg_result === "LOST"
                                  ? "bg-rose-500"
                                  : it.leg_result === "LIVE"
                                  ? "bg-amber-400 animate-pulse"
                                  : "bg-slate-600"
                              }`}
                            />
                          ))}
                        </span>
                        {c.is_locked && (
                          <button
                            type="button"
                            onClick={() => setLockInfoId((cur) => (cur === c.id ? null : c.id))}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-800/90 border border-white/10 text-[10px] font-semibold text-slate-300 hover:border-amber-400/50"
                          >
                            <Lock className="w-2.5 h-2.5 text-amber-400" />
                            Kilitli
                          </button>
                        )}
                      </div>
                      {c.is_locked && lockInfoId === c.id && (
                        <p className="text-[10px] text-amber-300/90 bg-amber-500/10 border border-amber-500/25 rounded-lg px-2 py-1 mt-1.5">
                          İlk maç başlamasına ≤15 dk kaldığı için kupon kilitlendi; tüm ayaklar bitene kadar korunur.
                        </p>
                      )}
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
                          <span className="mono-nums shrink-0">
                            {it.start_date_tr ? `${it.start_date_tr} ${it.start_time_tr}` : it.start_time_tr}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5 min-w-0">
                          <TeamBadge name={it.home_team} logoUrl={it.home_logo_url} size="sm" />
                          <p className="text-xs font-semibold text-slate-100 truncate">
                            {it.home_team} vs {it.away_team}
                          </p>
                          <TeamBadge name={it.away_team} logoUrl={it.away_logo_url} size="sm" />
                        </div>
                        <div className="flex items-center justify-between mt-1.5 text-xs gap-2">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span className="text-emerald-400 font-bold truncate">
                              {formatMarketLabel(it.market_key, it.selection_key)}
                            </span>
                            {it.leg_result === "LIVE" && (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 text-[10px] font-bold shrink-0">
                                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                                Canlı
                                {it.home_score != null && it.away_score != null
                                  ? ` ${it.home_score}-${it.away_score}`
                                  : ""}
                              </span>
                            )}
                            {it.leg_result === "WON" && (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 text-[10px] font-bold shrink-0 mono-nums">
                                {it.home_score != null && it.away_score != null
                                  ? `${it.home_score}-${it.away_score} • `
                                  : ""}
                                Kazandı
                              </span>
                            )}
                            {it.leg_result === "LOST" && (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-rose-500/20 border border-rose-500/40 text-rose-300 text-[10px] font-bold shrink-0 mono-nums">
                                {it.home_score != null && it.away_score != null
                                  ? `${it.home_score}-${it.away_score} • `
                                  : ""}
                                Kaybetti
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mono-nums shrink-0">
                            <span className="text-slate-400 text-[10px]">
                              G:{Math.round(it.confidence_score)}
                            </span>
                            <span className="px-2 py-0.5 rounded-md bg-white/[0.08] font-extrabold text-white">
                              {it.iddaa_odd.toFixed(2)}
                            </span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="pt-3 border-t border-[#1F2937] space-y-2">
                  <div className="flex items-center justify-between text-[11px] text-slate-400 mono-nums">
                    <span>Ort. Güven: {c.avg_confidence.toFixed(1)} / 100</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        onImportSlip(
                          c.items.map((it) => ({
                            match_id: it.match_id,
                            home_team: it.home_team,
                            away_team: it.away_team,
                            market_key: it.market_key,
                            selection_key: it.selection_key,
                            iddaa_odd: it.iddaa_odd,
                          }))
                        )
                      }
                      className="flex-1 inline-flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-xl glass-card hover:border-[#00FF85]/45 text-xs font-bold text-slate-200"
                    >
                      <ArrowDownToLine className="w-3.5 h-3.5 text-[#00FF85]" />
                      Kuponuma Aktar
                    </button>
                    <button
                      onClick={() =>
                        setSharePayload({
                          tier: (c.tier as "BANKO" | "DENGELI" | "SURPRIZ") || "BANKO",
                          title: meta.title,
                          couponDate: c.coupon_date,
                          totalOdd: c.total_odd,
                          avgConfidence: c.avg_confidence,
                          couponStatus: c.status,
                          items: c.items,
                          textPayload,
                        })
                      }
                      className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-emerald-500/15 border border-emerald-500/35 hover:bg-emerald-500/25 text-xs font-bold text-emerald-300"
                    >
                      <Share2 className="w-3.5 h-3.5" /> Paylaş
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
          </div>
        </div>
      ))}

      {/* Settled Coupon History (Last 14 Days) */}
      {subTab === "history" && (
        historyLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[1, 2, 3].map((n) => (
              <div key={n} className="h-64 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />
            ))}
          </div>
        ) : historyCoupons.length === 0 ? (
          <div className="p-8 text-center rounded-2xl glass-card">
            <p className="text-sm font-semibold text-slate-200">
              Son 14 günde sonuçlanmış kupon henüz yok.
            </p>
            <p className="text-xs text-slate-400 mt-1">
              Oynanan kuponlar tüm ayakları tamamlandığında otomatik olarak buraya taşınır.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {historyCoupons.map((c) => {
              const meta = TIER_META[c.tier] || TIER_META.BANKO;
              const Icon = meta.icon;
              const isWon = c.status === "WON";
              const textPayload =
                `Fikstra • ${meta.title} (${c.coupon_date}) • ${isWon ? "KAZANDI" : "KAYBETTİ"}\n` +
                c.items
                  .map(
                    (it) =>
                      `• ${it.home_team} - ${it.away_team} (${it.home_score ?? "-"}-${it.away_score ?? "-"}) | ${formatMarketLabel(it.market_key, it.selection_key)} @ ${it.iddaa_odd.toFixed(2)}`
                  )
                  .join("\n") +
                `\nToplam Oran: ${c.total_odd.toFixed(2)}`;

              return (
                <div
                  key={c.id}
                  className={`flex flex-col justify-between p-4 rounded-2xl glass-card ${
                    isWon ? "border-emerald-500/40" : "border-rose-500/30"
                  }`}
                >
                  <div>
                    <div className="flex items-start justify-between gap-2 mb-3">
                      <div>
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-bold ${meta.badge}`}
                          >
                            <Icon className="w-3.5 h-3.5" />
                            {meta.title}
                          </span>
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[10px] font-extrabold ${
                              isWon
                                ? "bg-emerald-500/20 border-emerald-500/45 text-emerald-300"
                                : "bg-rose-500/20 border-rose-500/45 text-rose-300"
                            }`}
                          >
                            {isWon ? "KAZANDI" : "KAYBETTİ"}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-400 mt-1.5 mono-nums">
                          {c.coupon_date}
                        </p>
                      </div>
                      <div className="text-right">
                        <span className="text-[10px] text-slate-400 block">Toplam</span>
                        <span className="text-lg font-extrabold mono-nums text-white">
                          {c.total_odd.toFixed(2)}
                        </span>
                      </div>
                    </div>

                    <div className="space-y-2 my-3">
                      {c.items.map((it) => {
                        const legWon = it.leg_result === "WON";
                        const legLost = it.leg_result === "LOST";
                        return (
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
                              <span className="mono-nums shrink-0">
                                {it.start_date_tr
                                  ? `${it.start_date_tr} ${it.start_time_tr}`
                                  : it.start_time_tr}
                              </span>
                            </div>
                            <div className="flex items-center justify-between gap-2 mt-0.5 min-w-0">
                              <div className="flex items-center gap-1.5 min-w-0">
                                <TeamBadge name={it.home_team} logoUrl={it.home_logo_url} size="sm" />
                                <p className="text-xs font-semibold text-slate-100 truncate">
                                  {it.home_team} vs {it.away_team}
                                </p>
                                <TeamBadge name={it.away_team} logoUrl={it.away_logo_url} size="sm" />
                              </div>
                              {it.home_score != null && it.away_score != null && (
                                <span className="text-xs font-extrabold mono-nums text-white shrink-0">
                                  {it.home_score}-{it.away_score}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center justify-between mt-1.5 text-xs gap-2">
                              <div className="flex items-center gap-1.5 min-w-0">
                                <span className="text-emerald-400 font-bold truncate">
                                  {formatMarketLabel(it.market_key, it.selection_key)}
                                </span>
                                {(legWon || legLost) && (
                                  <span
                                    className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-extrabold shrink-0 ${
                                      legWon
                                        ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300"
                                        : "bg-rose-500/20 border-rose-500/40 text-rose-300"
                                    }`}
                                  >
                                    {legWon ? "KAZANDI" : "KAYBETTİ"}
                                  </span>
                                )}
                              </div>
                              <span className="font-bold text-white mono-nums shrink-0">
                                {it.iddaa_odd.toFixed(2)}
                              </span>
                            </div>
                          </div>
                        );
                      })}
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
                          couponStatus: c.status,
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
        )
      )}

      <CouponShareModal payload={sharePayload} onClose={() => setSharePayload(null)} />
    </div>
  );
}
