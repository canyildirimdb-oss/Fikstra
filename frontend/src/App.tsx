import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  Bell,
  BellRing,
  Flame,
  Layers,
  ListFilter,
  RefreshCw,
  Share2,
} from "lucide-react";
import { api } from "./api/client";
import { formatMarketLabel } from "./utils/format";
import { CouponShareModal } from "./components/CouponShareModal";
import { MatchDetailDrawer } from "./components/MatchDetailDrawer";
import type { ShareCouponPayload } from "./utils/couponShare";
import { BulletinPage } from "./pages/BulletinPage";
import { CouponsPage } from "./pages/CouponsPage";
import { HighlightsPage } from "./pages/HighlightsPage";
import { PerformancePage } from "./pages/PerformancePage";
import type { CustomSlipItem, DashboardSummary } from "./types";

type TabKey = "highlights" | "coupons" | "bulletin" | "performance";

const TABS: { key: TabKey; label: string; icon: typeof Flame }[] = [
  { key: "highlights", label: "Öne Çıkanlar", icon: Flame },
  { key: "coupons", label: "Kuponlar", icon: Layers },
  { key: "bulletin", label: "Bülten", icon: ListFilter },
  { key: "performance", label: "Performans", icon: BarChart3 },
];

function urlBase64ToUint8Array(base64String: string) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export function App() {
  const [tab, setTab] = useState<TabKey>("highlights");
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [selectedMatch, setSelectedMatch] = useState<{
    matchId: number;
    couponPick?: { market_key: string; selection_key: string; iddaa_odd: number };
  } | null>(null);
  const [slipItems, setSlipItems] = useState<CustomSlipItem[]>([]);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [pushStatus, setPushStatus] = useState<"idle" | "enabled" | "unsupported">("idle");
  const [pushMsg, setPushMsg] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [slipSharePayload, setSlipSharePayload] = useState<ShareCouponPayload | null>(null);

  const loadSummary = useCallback(() => {
    api.getSummary().then(setSummary).catch(() => {});
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary, refreshTrigger]);

  const handleManualRefresh = async () => {
    setRefreshTrigger((n) => n + 1);
    if (refreshing) return;
    setRefreshing(true);
    try {
      const res = await api.triggerRefresh();
      if (res.status === "cooldown") {
        setPushMsg(`Veriler güncel • ${res.retry_after_sec ?? 180} sn sonra yeniden taranabilir.`);
      } else if (res.status === "already_running") {
        setPushMsg("Arka planda tarama zaten sürüyor, ekran güncellendi.");
      } else {
        setPushMsg("Canlı bülten taraması arka planda başlatıldı.");
        setTimeout(() => setRefreshTrigger((n) => n + 1), 6000);
      }
    } catch {
      setPushMsg("Veriler yenilendi.");
    } finally {
      setRefreshing(false);
      setTimeout(() => setPushMsg(null), 3500);
    }
  };

  const handleToggleSlip = (item: CustomSlipItem) => {
    setSlipItems((prev) => {
      const exists = prev.some((x) => x.match_id === item.match_id);
      if (exists) return prev.filter((x) => x.match_id !== item.match_id);
      return [...prev, item];
    });
  };

  const handleEnablePush = async () => {
    if (!("Notification" in window) || !("serviceWorker" in navigator)) {
      setPushStatus("unsupported");
      setPushMsg("Tarayıcı bildirimleri desteklemiyor.");
      return;
    }
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        setPushMsg("Bildirim izni verilmedi.");
        return;
      }
      const { public_key } = await api.getVapidKey();
      if (!public_key) {
        setPushStatus("enabled");
        setPushMsg("Bildirim izni açıldı (sunucu VAPID anahtarı bekleniyor).");
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key),
      });
      const jsonSub = sub.toJSON();
      if (jsonSub.endpoint && jsonSub.keys?.p256dh && jsonSub.keys?.auth) {
        await api.subscribePush({
          endpoint: jsonSub.endpoint,
          p256dh: jsonSub.keys.p256dh,
          auth: jsonSub.keys.auth,
        });
      }
      setPushStatus("enabled");
      setPushMsg("Telefon bildirimleri aktif!");
    } catch {
      setPushMsg("Bildirim kaydı sırasında hata oluştu.");
    } finally {
      setTimeout(() => setPushMsg(null), 3200);
    }
  };

  const customTotalOdd = slipItems.reduce((acc, x) => acc * x.iddaa_odd, 1);

  return (
    <div className="min-h-screen bg-[#090D16] text-slate-100 pb-28 md:pb-14">
      {/* Glassmorphic Top Header */}
      <header className="sticky top-0 z-30 bg-[#090D16]/85 backdrop-blur-xl border-b border-[#1F2937]">
        <div className="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <img
              src="/icon.svg"
              alt="Fikstra"
              className="w-8 h-8 rounded-xl shadow-lg shadow-emerald-500/20"
            />
            <div>
              <h1 className="text-sm font-extrabold tracking-tight text-white leading-none">
                Fikstra
              </h1>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-[10px] text-slate-400 mono-nums">
                  7/24 Aktif •{" "}
                  {summary?.minutes_ago !== null && summary?.minutes_ago !== undefined
                    ? `${summary.minutes_ago} dk önce`
                    : "Canlı"}
                </span>
              </div>
            </div>
          </div>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center gap-1 bg-[#131926] p-1 rounded-xl border border-[#1F2937]">
            {TABS.map((t) => {
              const Icon = t.icon;
              const active = tab === t.key;
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    active
                      ? "bg-emerald-500/15 text-emerald-400"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {t.label}
                </button>
              );
            })}
          </nav>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleEnablePush}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border text-xs font-semibold transition-colors ${
                pushStatus === "enabled"
                  ? "bg-emerald-500/15 border-emerald-500/40 text-emerald-400"
                  : "bg-[#131926] border-[#1F2937] text-slate-300 hover:border-cyan-500/40"
              }`}
              title="Bildirimleri Aç"
            >
              {pushStatus === "enabled" ? (
                <BellRing className="w-3.5 h-3.5" />
              ) : (
                <Bell className="w-3.5 h-3.5" />
              )}
              <span className="hidden sm:inline">Bildirim</span>
            </button>
            <button
              onClick={handleManualRefresh}
              disabled={refreshing}
              className="p-2 rounded-xl bg-[#131926] border border-[#1F2937] text-slate-300 hover:text-white disabled:opacity-60"
              title="Yenile"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>
      </header>

      {pushMsg && (
        <div className="max-w-5xl mx-auto px-4 pt-3">
          <div className="px-3.5 py-2 rounded-xl bg-cyan-500/15 border border-cyan-500/30 text-xs text-cyan-300">
            {pushMsg}
          </div>
        </div>
      )}

      {/* Main Content */}
      <main className="max-w-5xl mx-auto px-4 pt-4">
        {tab === "highlights" && (
          <HighlightsPage
            summary={summary}
            onSelectMatch={(id) => setSelectedMatch({ matchId: id })}
            slipItems={slipItems}
            onToggleSlip={handleToggleSlip}
            refreshTrigger={refreshTrigger}
          />
        )}
        {tab === "coupons" && (
          <CouponsPage
            slipItems={slipItems}
            onRemoveSlipItem={(id) =>
              setSlipItems((prev) => prev.filter((x) => x.match_id !== id))
            }
            onClearSlip={() => setSlipItems([])}
            onSelectMatch={(matchId, couponPick) => setSelectedMatch({ matchId, couponPick })}
            refreshTrigger={refreshTrigger}
          />
        )}
        {tab === "bulletin" && (
          <BulletinPage
            onSelectMatch={(id) => setSelectedMatch({ matchId: id })}
            refreshTrigger={refreshTrigger}
          />
        )}
        {tab === "performance" && <PerformancePage refreshTrigger={refreshTrigger} />}
      </main>

      {/* Bottom Kuponum Fişi Bar (when not on Coupons tab) */}
      {slipItems.length > 0 && tab !== "coupons" && (
        <div className="fixed bottom-16 md:bottom-4 inset-x-0 z-30 px-4 pointer-events-none">
          <div className="max-w-3xl mx-auto pointer-events-auto p-3 rounded-2xl bg-[#0D1424]/95 backdrop-blur-xl border border-emerald-500/40 shadow-2xl shadow-emerald-500/15 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-xs font-bold text-emerald-400">
                <Layers className="w-3.5 h-3.5 shrink-0" />
                <span>Kuponum Fişi ({slipItems.length} Maç)</span>
                <span className="mono-nums px-2 py-0.5 rounded-md bg-emerald-500/20 text-emerald-300">
                  {customTotalOdd.toFixed(2)} Oran
                </span>
              </div>
              <p className="text-[11px] text-slate-300 truncate mt-0.5">
                {slipItems
                  .map(
                    (s) =>
                      `${s.home_team}-${s.away_team} (${formatMarketLabel(s.market_key, s.selection_key)} @${s.iddaa_odd.toFixed(2)})`
                  )
                  .join(" • ")}
              </p>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                onClick={() => {
                  const todayStr = new Date().toLocaleDateString("en-CA", {
                    timeZone: "Europe/Istanbul",
                  });
                  const textPayload =
                    `Fikstra • Kendi Özel Kuponum (${todayStr})\n` +
                    slipItems
                      .map(
                        (s) =>
                          `• ${s.home_team} - ${s.away_team} | ${formatMarketLabel(s.market_key, s.selection_key)} @ ${s.iddaa_odd.toFixed(2)}`
                      )
                      .join("\n") +
                    `\nToplam Oran: ${customTotalOdd.toFixed(2)}`;
                  setSlipSharePayload({
                    tier: "CUSTOM",
                    title: "Kendi Özel Kuponum",
                    couponDate: todayStr,
                    totalOdd: customTotalOdd,
                    avgConfidence: null,
                    items: slipItems,
                    textPayload,
                  });
                }}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold"
              >
                <Share2 className="w-3.5 h-3.5" />
                Kuponu Paylaş
              </button>
              <button
                onClick={() => setTab("coupons")}
                className="px-2.5 py-1.5 rounded-xl bg-[#131926] border border-[#1F2937] text-xs font-semibold text-slate-200 hover:border-emerald-500/40"
              >
                Fişi Aç
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mobile Fixed Bottom Navigation */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-[#090D16]/95 backdrop-blur-xl border-t border-[#1F2937] px-2 py-1.5">
        <div className="grid grid-cols-4 gap-1">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`flex flex-col items-center justify-center py-1.5 rounded-xl transition-colors ${
                  active ? "text-emerald-400 bg-emerald-500/10" : "text-slate-400"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="text-[10px] font-semibold mt-0.5">{t.label}</span>
              </button>
            );
          })}
        </div>
      </nav>

      {/* Match Detail Bottom Sheet / Modal */}
      <MatchDetailDrawer
        matchId={selectedMatch?.matchId ?? null}
        couponPick={selectedMatch?.couponPick}
        onClose={() => setSelectedMatch(null)}
      />
      <CouponShareModal payload={slipSharePayload} onClose={() => setSlipSharePayload(null)} />
    </div>
  );
}

export default App;
