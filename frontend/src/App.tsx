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
  Ticket,
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
    <div className="min-h-screen text-slate-100 pb-28 md:pb-14">
      {/* Glassmorphic Neon Emerald Top Header */}
      <header className="sticky top-0 z-30 bg-[#05080D]/80 backdrop-blur-xl border-b border-emerald-500/15">
        <div className="max-w-5xl mx-auto px-4 h-16 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {/* Geometric F. Logo in Neon Emerald Glowing Dark Square */}
            <div className="w-9 h-9 rounded-xl bg-[#0B111C] border border-[#00FF85]/50 shadow-[0_0_18px_-3px_rgba(0,255,133,0.45)] flex items-center justify-center shrink-0">
              <svg viewBox="0 0 32 32" className="w-5 h-5" fill="none">
                <path
                  d="M8 7H22M8 7V25M8 15.5H19"
                  stroke="#00FF85"
                  strokeWidth="3.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <circle cx="22.5" cy="23.5" r="2.3" fill="#00FF85" />
              </svg>
            </div>
            <div>
              <h1 className="text-base font-extrabold tracking-tight text-white leading-none">
                Fikstra
              </h1>
              <p className="text-[11px] text-emerald-300/80 font-medium mt-1 leading-none">
                Veriyle Okunan Maçlar
              </p>
            </div>
          </div>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center gap-1 glass-card p-1 rounded-xl">
            {TABS.map((t) => {
              const Icon = t.icon;
              const active = tab === t.key;
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                    active
                      ? "bg-[#00FF85]/15 text-[#00FF85]"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {t.label}
                  {active && (
                    <span className="absolute inset-x-3 -bottom-1 h-[2px] rounded-full bg-[#00FF85] shadow-[0_0_8px_#00FF85]" />
                  )}
                </button>
              );
            })}
          </nav>

          {/* Circular Dark Glass Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleEnablePush}
              className={`relative w-9 h-9 rounded-full glass-card flex items-center justify-center transition-all ${
                pushStatus === "enabled"
                  ? "border-[#00FF85]/55 text-[#00FF85] shadow-[0_0_12px_-3px_rgba(0,255,133,0.45)]"
                  : "text-slate-300 hover:border-[#00FF85]/40 hover:text-white"
              }`}
              title="Bildirimleri Aç"
            >
              {pushStatus === "enabled" ? (
                <BellRing className="w-4 h-4" />
              ) : (
                <Bell className="w-4 h-4" />
              )}
              {pushStatus === "enabled" && (
                <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-[#00FF85] shadow-[0_0_6px_#00FF85]" />
              )}
            </button>
            <button
              onClick={handleManualRefresh}
              disabled={refreshing}
              className="w-9 h-9 rounded-full glass-card flex items-center justify-center text-slate-300 hover:text-[#00FF85] hover:border-[#00FF85]/40 disabled:opacity-60 transition-all"
              title="Yenile"
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin text-[#00FF85]" : ""}`} />
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

      {/* Desktop Floating Kuponum Fişi Bar (when not on Coupons tab) */}
      {slipItems.length > 0 && tab !== "coupons" && (
        <div className="hidden md:block fixed bottom-4 inset-x-0 z-30 px-4 pointer-events-none">
          <div className="max-w-3xl mx-auto pointer-events-auto p-3 rounded-2xl bg-[#080E18]/95 backdrop-blur-xl border border-[#00FF85]/45 shadow-2xl shadow-[#00FF85]/15 flex items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-xs font-bold text-[#00FF85]">
                <Ticket className="w-3.5 h-3.5 shrink-0" />
                <span>Kuponum Fişi ({slipItems.length} Maç)</span>
                <span className="mono-nums px-2 py-0.5 rounded-md bg-[#00FF85]/20 text-[#00FF85]">
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
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-bold"
              >
                <Share2 className="w-3.5 h-3.5" />
                Kuponu Paylaş
              </button>
              <button
                onClick={() => setTab("coupons")}
                className="px-2.5 py-1.5 rounded-xl glass-card text-xs font-semibold text-slate-200 hover:border-[#00FF85]/45"
              >
                Fişi Aç
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mobile Floating Bottom Navigation Dock with Elevated Center Kuponum Fişi Button */}
      <nav className="md:hidden fixed bottom-3 inset-x-3 z-40">
        <div className="relative rounded-2xl bg-[#080E18]/90 backdrop-blur-2xl border border-emerald-500/20 shadow-[0_12px_35px_-10px_rgba(0,0,0,0.85)] px-2 py-1.5 grid grid-cols-5 items-center">
          {(["highlights", "coupons"] as TabKey[]).map((key) => {
            const t = TABS.find((x) => x.key === key)!;
            const Icon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`relative flex flex-col items-center justify-center py-1.5 rounded-xl transition-colors ${
                  active ? "text-[#00FF85]" : "text-slate-400"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="text-[10px] font-semibold mt-0.5 truncate">{t.label}</span>
                {active && (
                  <span className="absolute bottom-0.5 w-6 h-[2.5px] rounded-full bg-[#00FF85] shadow-[0_0_8px_#00FF85]" />
                )}
              </button>
            );
          })}

          {/* Elevated Center Neon Green Kuponum Fişi Button */}
          <div className="flex justify-center">
            <button
              onClick={() => {
                setTab("coupons");
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
              className="relative -mt-6 w-13 h-13 rounded-full bg-[#00FF85] text-[#05080D] flex items-center justify-center shadow-[0_0_24px_rgba(0,255,133,0.6)] border-4 border-[#05080D] active:scale-95 transition-transform"
              title="Kuponum Fişi"
            >
              <Ticket className="w-5 h-5 stroke-[2.4]" />
              {slipItems.length > 0 && (
                <span className="absolute -top-1 -right-1 min-w-[20px] h-5 px-1 rounded-full bg-[#05080D] border border-[#00FF85] text-[#00FF85] text-[10px] font-extrabold mono-nums flex items-center justify-center shadow">
                  {slipItems.length}
                </span>
              )}
            </button>
          </div>

          {(["bulletin", "performance"] as TabKey[]).map((key) => {
            const t = TABS.find((x) => x.key === key)!;
            const Icon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`relative flex flex-col items-center justify-center py-1.5 rounded-xl transition-colors ${
                  active ? "text-[#00FF85]" : "text-slate-400"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="text-[10px] font-semibold mt-0.5 truncate">{t.label}</span>
                {active && (
                  <span className="absolute bottom-0.5 w-6 h-[2.5px] rounded-full bg-[#00FF85] shadow-[0_0_8px_#00FF85]" />
                )}
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
