import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  BarChart3,
  Bell,
  BellRing,
  BookmarkPlus,
  ChevronRight,
  Download,
  Flame,
  Grid3X3,
  HelpCircle,
  Layers,
  Link2,
  Send,
  Share2,
  Ticket,
  Trash2,
  X,
} from "lucide-react";
import { api } from "./api/client";
import { formatMarketLabel } from "./utils/format";
import { formatSyncTimeTr } from "./utils/highlightsFilter";
import { analyzeSlipRisk } from "./utils/slipRisk";
import { ControlPanel } from "./components/ControlPanel";
import { CouponShareModal } from "./components/CouponShareModal";
import { MatchDetailDrawer } from "./components/MatchDetailDrawer";
import { SettledMatchDrawer } from "./components/SettledMatchDrawer";
import { NotificationCenter } from "./components/NotificationCenter";
import { StakeCalculator } from "./components/StakeCalculator";
import type { ShareCouponPayload } from "./utils/couponShare";
import { CouponsPage } from "./pages/CouponsPage";
import { FikstraTotoPage } from "./pages/FikstraTotoPage";
import { HighlightsPage } from "./pages/HighlightsPage";
import { PerformancePage } from "./pages/PerformancePage";
import type { CustomSlipItem, DashboardSummary, MatchDetail, SavedSlip } from "./types";

type TabKey = "highlights" | "coupons" | "toto" | "performance";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const TELEGRAM_INVITE_URL =
  (import.meta.env.VITE_TELEGRAM_INVITE_URL as string | undefined) ||
  "https://t.me/+yVkUre9BnAo3ZmM0";

const STAKE_STORAGE_KEY = "fikstra_slip_stake";
const PWA_DISMISS_KEY = "fikstra_pwa_dismissed";
const SAVED_SLIPS_KEY = "fikstra_saved_slips_v1";

function isControlPath(): boolean {
  const path = window.location.pathname.toLowerCase();
  if (path === "/kontrol" || path === "/kontrol/") return true;
  const params = new URLSearchParams(window.location.search);
  return params.get("kontrol") === "1";
}

type CouponsSubTab = "active" | "history" | "sportoto" | "tracked";

function parseInitialUrl(): {
  tab: TabKey;
  matchId: number | null;
  predictionId: number | null;
  couponsView: CouponsSubTab;
  totoSlip: string | null;
} {
  const path = window.location.pathname.toLowerCase();
  if (path === "/bulten" || path === "/bulletin") {
    return { tab: "highlights", matchId: null, predictionId: null, couponsView: "active", totoSlip: null };
  }
  const params = new URLSearchParams(window.location.search);
  const rawTab = params.get("tab");
  const rawView = params.get("view");
  let tab: TabKey =
    rawTab === "coupons" || rawTab === "performance" || rawTab === "highlights" || rawTab === "toto"
      ? rawTab
      : "highlights";
  if (tab === "coupons" && rawView === "sportoto") {
    tab = "toto";
  }
  const couponsView: CouponsSubTab =
    rawView === "history" || rawView === "tracked" ? rawView : "active";
  const rawMatch = params.get("match");
  const parsedMatch = rawMatch ? Number(rawMatch) : NaN;
  const matchId = Number.isInteger(parsedMatch) && parsedMatch > 0 ? parsedMatch : null;
  const rawPred = params.get("pred");
  const parsedPred = rawPred ? Number(rawPred) : NaN;
  const predictionId = Number.isInteger(parsedPred) && parsedPred > 0 ? parsedPred : null;
  const totoSlip = tab === "toto" ? params.get("slip") : null;
  return { tab, matchId, predictionId, couponsView, totoSlip };
}

const TABS: { key: TabKey; label: string; mobileLabel?: string; badge?: string; icon: typeof Flame }[] = [
  { key: "highlights", label: "Analizler", icon: Flame },
  { key: "coupons", label: "Kuponlar", icon: Layers },
  { key: "toto", label: "Fikstra Toto", mobileLabel: "Toto", badge: "Yeni", icon: Grid3X3 },
  { key: "performance", label: "Performans", icon: BarChart3 },
];

function getTodayTrKey(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Istanbul" });
}

function getSlipStorageKey(): string {
  return `fikstra_slip_${getTodayTrKey()}`;
}

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
  const initialUrlRef = useRef(parseInitialUrl());
  const [tab, setTab] = useState<TabKey>(initialUrlRef.current.tab);
  const [couponsView, setCouponsView] = useState<CouponsSubTab>(initialUrlRef.current.couponsView);
  const [totoSlipParam, setTotoSlipParam] = useState<string | null>(initialUrlRef.current.totoSlip);
  const [notifOpen, setNotifOpen] = useState(false);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);
  const [selectedMatch, setSelectedMatch] = useState<{
    matchId: number;
    predictionId?: number;
    couponPick?: { market_key: string; selection_key: string; iddaa_odd: number };
    isSettled?: boolean;
    initialDetail?: MatchDetail | null;
  } | null>(() =>
    initialUrlRef.current.matchId
      ? {
          matchId: initialUrlRef.current.matchId,
          predictionId: initialUrlRef.current.predictionId ?? undefined,
        }
      : null
  );

  const [stake, setStake] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem(STAKE_STORAGE_KEY));
      return Number.isFinite(saved) && saved > 0 ? saved : 100;
    } catch {
      return 100;
    }
  });
  const [controlMode] = useState<boolean>(() => isControlPath());
  const [savedSlips, setSavedSlips] = useState<SavedSlip[]>(() => {
    try {
      const raw = localStorage.getItem(SAVED_SLIPS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.slice(0, 20) : [];
    } catch {
      return [];
    }
  });
  const [howItWorksOpen, setHowItWorksOpen] = useState(false);
  const [deferredInstallPrompt, setDeferredInstallPrompt] =
    useState<BeforeInstallPromptEvent | null>(null);
  const [pwaDismissed, setPwaDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(PWA_DISMISS_KEY) === "1";
    } catch {
      return false;
    }
  });

  const [slipItems, setSlipItems] = useState<CustomSlipItem[]>(() => {
    try {
      const raw = localStorage.getItem(getSlipStorageKey());
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  });

  const [slipSheetOpen, setSlipSheetOpen] = useState(false);
  const [toast, setToast] = useState<{
    message: string;
    undoItems?: CustomSlipItem[];
  } | null>(null);
  const toastTimerRef = useRef<number | null>(null);

  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [pushStatus, setPushStatus] = useState<"idle" | "enabled" | "unsupported">("idle");
  const [pushMsg, setPushMsg] = useState<string | null>(null);
  const [slipSharePayload, setSlipSharePayload] = useState<ShareCouponPayload | null>(null);
  const lastAutoRefreshRef = useRef<number>(Date.now());

  const showToast = useCallback((message: string, undoItems?: CustomSlipItem[]) => {
    if (toastTimerRef.current) {
      window.clearTimeout(toastTimerRef.current);
    }
    setToast({ message, undoItems });
    toastTimerRef.current = window.setTimeout(() => {
      setToast(null);
    }, 3600);
  }, []);

  // Clean started matches from persisted slip on startup
  useEffect(() => {
    const nowMs = Date.now();
    setSlipItems((prev) => {
      const valid = prev.filter((item) => {
        if (!item.start_time_utc) return true;
        const ms = Date.parse(item.start_time_utc);
        return Number.isNaN(ms) || ms > nowMs;
      });
      if (valid.length < prev.length) {
        showToast("Başlamış maçlar kuponumdan temizlendi");
      }
      return valid;
    });
  }, [showToast]);

  // Hydrate shared slip from ?slip=12,45,89 (skip when ?tab=toto)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("tab") === "toto") return;
    const rawSlip = params.get("slip");
    if (!rawSlip) return;
    const ids = rawSlip
      .split(",")
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isInteger(n) && n > 0)
      .slice(0, 30);

    // Clean ?slip= from URL immediately
    params.delete("slip");
    const nextSearch = params.toString();
    const nextUrl = nextSearch ? `${window.location.pathname}?${nextSearch}` : window.location.pathname;
    window.history.replaceState(window.history.state, "", nextUrl);

    if (ids.length === 0) return;

    api
      .getPredictionStatuses(ids)
      .then((res) => {
        const nowMs = Date.now();
        const validItems: CustomSlipItem[] = [];
        let skipped = 0;
        for (const it of res.items) {
          const kickoffMs = it.kickoff ? Date.parse(it.kickoff) : NaN;
          const started = !Number.isNaN(kickoffMs) && kickoffMs <= nowMs;
          if (it.status !== "PENDING" || started) {
            skipped += 1;
            continue;
          }
          validItems.push({
            match_id: it.match_id ?? it.id,
            prediction_id: it.prediction_id ?? it.id,
            home_team: it.home_team,
            away_team: it.away_team,
            market_key: it.market_key,
            selection_key: it.selection_key,
            iddaa_odd: it.odd,
            start_time_utc: it.start_time_utc ?? it.kickoff ?? undefined,
          });
        }
        if (validItems.length > 0) {
          setSlipItems(validItems);
          setTab("coupons");
          showToast(
            skipped > 0
              ? `${validItems.length} maç kuponuna yüklendi (${skipped} başlamış maç atlandı)`
              : `${validItems.length} maç kuponuna yüklendi`
          );
        } else {
          showToast("Paylaşılan kupondaki maçlar başlamış veya bulunamadı");
        }
      })
      .catch(() => {});
  }, [showToast]);

  // Persist slipItems per TR day
  useEffect(() => {
    try {
      localStorage.setItem(getSlipStorageKey(), JSON.stringify(slipItems));
    } catch {
      // ignore storage quota errors
    }
  }, [slipItems]);

  // Persist stake in localStorage
  useEffect(() => {
    try {
      localStorage.setItem(STAKE_STORAGE_KEY, String(stake));
    } catch {
      // ignore storage quota errors
    }
  }, [stake]);

  // Capture Android/Chrome PWA install prompt
  useEffect(() => {
    const onBeforeInstall = (e: Event) => {
      e.preventDefault();
      setDeferredInstallPrompt(e as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    return () => window.removeEventListener("beforeinstallprompt", onBeforeInstall);
  }, []);

  // Keep URL query params clean (?tab= & ?match=) without breaking drawer popstate
  useEffect(() => {
    if (controlMode) return;
    const path = window.location.pathname.toLowerCase();
    if (path === "/bulten" || path === "/bulletin") {
      window.history.replaceState(window.history.state, "", "/");
      return;
    }
    const qs = new URLSearchParams();
    if (tab !== "highlights") qs.set("tab", tab);
    if (tab === "coupons" && couponsView === "sportoto") qs.set("view", "sportoto");
    if (selectedMatch?.matchId) qs.set("match", String(selectedMatch.matchId));
    const nextUrl = qs.toString() ? `/?${qs.toString()}` : "/";
    const timer = window.setTimeout(() => {
      const current = window.location.pathname + window.location.search;
      if (current !== nextUrl) {
        window.history.replaceState(window.history.state, "", nextUrl);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [tab, couponsView, selectedMatch, controlMode]);

  // Silent auto-refresh every 60s when tab is visible, and immediately on visibility/focus return
  useEffect(() => {
    const triggerSilentRefresh = () => {
      const now = Date.now();
      if (now - lastAutoRefreshRef.current < 5000) return;
      lastAutoRefreshRef.current = now;
      setRefreshTrigger((n) => n + 1);
    };

    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        lastAutoRefreshRef.current = Date.now();
        setRefreshTrigger((n) => n + 1);
      }
    }, 60_000);

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        triggerSilentRefresh();
      }
    };
    const onFocus = () => {
      triggerSilentRefresh();
    };

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const loadSummary = useCallback(() => {
    api.getSummary().then(setSummary).catch(() => {});
    api
      .getHealth()
      .then((h) => {
        if (h?.last_sync_at) setLastSyncAt(h.last_sync_at);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary, refreshTrigger]);

  const handleCloseMatch = useCallback(() => {
    setSelectedMatch(null);
  }, []);

  const handleToggleSlip = useCallback(
    (item: CustomSlipItem) => {
      const kickoffMs = item.start_time_utc ? Date.parse(item.start_time_utc) : NaN;
      if (!Number.isNaN(kickoffMs) && kickoffMs <= Date.now()) {
        showToast("Başlamış veya sonuçlanmış maç kupona eklenemez");
        return;
      }
      setSlipItems((prev) => {
        const exists = prev.some((x) => x.match_id === item.match_id);
        if (exists) {
          showToast(`${item.home_team} kupondan çıkarıldı`, prev);
          return prev.filter((x) => x.match_id !== item.match_id);
        }
        if (item.prediction_id) {
          api.recordSlipAdd(item.prediction_id).catch(() => {});
        }
        showToast(`${item.home_team} kupona eklendi`, prev);
        return [...prev, item];
      });
    },
    [showToast]
  );

  const handleCopySlipLink = useCallback(async () => {
    const ids = slipItems
      .map((s) => s.prediction_id)
      .filter((id): id is number => typeof id === "number" && id > 0);
    if (ids.length === 0) {
      showToast("Link oluşturmak için en az 1 maç ekleyin");
      return;
    }
    const url = `${window.location.origin}/?slip=${ids.join(",")}`;
    try {
      await navigator.clipboard.writeText(url);
      showToast("Kupon linki panoya kopyalandı");
    } catch {
      showToast(url);
    }
  }, [slipItems, showToast]);

  const handleRemoveSlipItem = useCallback(
    (matchId: number) => {
      setSlipItems((prev) => {
        const target = prev.find((x) => x.match_id === matchId);
        if (target) {
          showToast(`${target.home_team} kupondan çıkarıldı`, prev);
        }
        return prev.filter((x) => x.match_id !== matchId);
      });
    },
    [showToast]
  );

  const handleClearSlip = useCallback(() => {
    setSlipItems((prev) => {
      if (prev.length > 0) {
        showToast("Kuponum temizlendi", prev);
      }
      return [];
    });
    setSlipSheetOpen(false);
  }, [showToast]);

  const handleImportSlip = useCallback(
    (items: CustomSlipItem[]) => {
      setSlipItems((prev) => {
        showToast(`${items.length} maç kuponuma aktarıldı`, prev);
        return items;
      });
    },
    [showToast]
  );

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

  useEffect(() => {
    try {
      localStorage.setItem(SAVED_SLIPS_KEY, JSON.stringify(savedSlips.slice(0, 20)));
    } catch {
      // ignore storage quota errors
    }
  }, [savedSlips]);

  const handleSaveSlip = useCallback(() => {
    if (slipItems.length === 0) return;
    const totalOdd = Number(slipItems.reduce((acc, x) => acc * x.iddaa_odd, 1).toFixed(2));
    const entry: SavedSlip = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      created_at: new Date().toISOString(),
      total_odd: totalOdd,
      stake,
      items: slipItems,
    };
    setSavedSlips((prev) => [entry, ...prev].slice(0, 20));
    showToast("Kupon takibe alındı (Kuponlar → Takip Ettiklerim)");
  }, [slipItems, stake, showToast]);

  const handleDeleteSavedSlip = useCallback((slipId: string) => {
    setSavedSlips((prev) => prev.filter((s) => s.id !== slipId));
  }, []);

  const customTotalOdd = Math.round(slipItems.reduce((acc, x) => acc * x.iddaa_odd, 1) * 100) / 100;
  const slipRisk = useMemo(() => analyzeSlipRisk(slipItems), [slipItems]);

  const openSlipShare = () => {
    const todayStr = getTodayTrKey();
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
  };

  if (controlMode) {
    return (
      <ControlPanel
        onExit={() => {
          window.location.href = "/";
        }}
      />
    );
  }

  const containerMaxWidth = tab === "highlights" ? "max-w-7xl" : "max-w-5xl";
  const syncTimeLabel = formatSyncTimeTr(lastSyncAt ?? summary?.last_sync_at);

  return (
    <div className="min-h-screen text-slate-100 pb-[calc(9.5rem+env(safe-area-inset-bottom))] md:pb-20">
      {/* Glassmorphic Neon Emerald Top Header */}
      <header className="sticky top-0 z-30 bg-[#05080D]/80 backdrop-blur-xl border-b border-emerald-500/15">
        <div className={`${containerMaxWidth} mx-auto px-4 h-16 flex items-center justify-between gap-3`}>
          <div className="flex items-center gap-3">
            <img
              src="/logo-profile.png"
              alt="Fikstra"
              className="w-9 h-9 rounded-xl border border-[#00FF85]/40 shadow-[0_0_18px_-3px_rgba(0,255,133,0.4)] object-cover shrink-0"
            />
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
                  {t.badge ? (
                    <span className="inline-flex items-center gap-1.5">
                      <span className="toto-shimmer-text font-extrabold">{t.label}</span>
                      <span className="px-1.5 py-[1px] rounded-full bg-[#00FF85] text-[#05080D] text-[9px] font-extrabold leading-none shadow-[0_0_8px_rgba(0,255,133,0.45)]">
                        {t.badge}
                      </span>
                    </span>
                  ) : (
                    t.label
                  )}
                  {t.key === "coupons" && slipItems.length > 0 && (
                    <span className="ml-0.5 px-1.5 py-0.5 rounded-full bg-[#00FF85] text-[#05080D] text-[10px] font-extrabold mono-nums leading-none">
                      {slipItems.length}
                    </span>
                  )}
                  {active && (
                    <span className="absolute inset-x-3 -bottom-1 h-[2px] rounded-full bg-[#00FF85] shadow-[0_0_8px_#00FF85]" />
                  )}
                </button>
              );
            })}
          </nav>

          {/* Live Freshness + Telegram Bridge + Circular Dark Glass Notification Action */}
          <div className="flex items-center gap-2 shrink-0">
            {syncTimeLabel && (
              <span
                className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/25 text-[11px] font-bold mono-nums text-emerald-300"
                title="Son canlı veri güncellemesi (TR saati)"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-[#00FF85] animate-pulse" />
                Canlı · {syncTimeLabel}
              </span>
            )}
            <a
              href={TELEGRAM_INVITE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="h-9 w-9 sm:w-auto sm:px-3 rounded-full glass-card border-emerald-500/30 hover:border-[#00FF85]/55 text-emerald-300 hover:text-[#00FF85] flex items-center justify-center gap-1.5 text-xs font-bold transition-all"
              title="Telegram Grubu"
              aria-label="Telegram Grubu"
            >
              <Send className="w-3.5 h-3.5 shrink-0" />
              <span className="hidden sm:inline">Telegram</span>
            </a>

            <button
              onClick={() => setNotifOpen(true)}
              className={`relative w-9 h-9 rounded-full glass-card flex items-center justify-center transition-all ${
                pushStatus === "enabled"
                  ? "border-[#00FF85]/55 text-[#00FF85] shadow-[0_0_12px_-3px_rgba(0,255,133,0.45)]"
                  : "text-slate-300 hover:border-[#00FF85]/40 hover:text-white"
              }`}
              title="Bildirim Merkezi"
              aria-label="Bildirim Merkezi"
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
          </div>
        </div>
      </header>

      {/* PWA Install / iOS Safari Hint Strip */}
      {(() => {
        if (pwaDismissed) return null;
        const isStandalone =
          typeof window !== "undefined" &&
          (window.matchMedia?.("(display-mode: standalone)")?.matches ||
            (window.navigator as { standalone?: boolean }).standalone === true);
        if (isStandalone) return null;
        const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
        const isIosSafari =
          /iphone|ipad|ipod/i.test(ua) &&
          /safari/i.test(ua) &&
          !/crios|fxios|edgios/i.test(ua);
        if (!deferredInstallPrompt && !isIosSafari) return null;

        const dismissPwa = () => {
          setPwaDismissed(true);
          try {
            localStorage.setItem(PWA_DISMISS_KEY, "1");
          } catch {
            // ignore
          }
        };

        return (
          <div className="max-w-5xl mx-auto px-4 pt-3">
            <div className="px-3.5 py-2 rounded-xl glass-card border-emerald-500/25 flex items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-2 min-w-0">
                <Download className="w-3.5 h-3.5 text-[#00FF85] shrink-0" />
                <span className="text-slate-200 truncate">
                  {deferredInstallPrompt
                    ? "Fikstra'yı ana ekranına uygulama olarak kur"
                    : "iPhone'da uygulama gibi kullan: Paylaş → Ana Ekrana Ekle"}
                </span>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {deferredInstallPrompt && (
                  <button
                    type="button"
                    onClick={async () => {
                      await deferredInstallPrompt.prompt();
                      const res = await deferredInstallPrompt.userChoice;
                      if (res.outcome === "accepted") dismissPwa();
                      setDeferredInstallPrompt(null);
                    }}
                    className="px-2.5 py-1 rounded-lg bg-[#00FF85] text-[#05080D] text-[11px] font-extrabold"
                  >
                    Uygulama olarak kur
                  </button>
                )}
                <button
                  type="button"
                  onClick={dismissPwa}
                  className="p-1 text-slate-400 hover:text-white"
                  aria-label="İpucunu kapat"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {pushMsg && (
        <div className="max-w-5xl mx-auto px-4 pt-3">
          <div className="px-3.5 py-2 rounded-xl bg-cyan-500/15 border border-cyan-500/30 text-xs text-cyan-300">
            {pushMsg}
          </div>
        </div>
      )}

      {/* Main Content */}
      <main className={`${containerMaxWidth} mx-auto px-4 pt-4`}>
        {tab === "highlights" && (
          <HighlightsPage
            summary={summary}
            onSelectMatch={(id, predictionId) => setSelectedMatch({ matchId: id, predictionId })}
            onGoPerformance={() => setTab("performance")}
            onGoCoupons={() => setTab("coupons")}
            onGoToto={() => setTab("toto")}
            telegramInviteUrl={TELEGRAM_INVITE_URL}
            slipItems={slipItems}
            onToggleSlip={handleToggleSlip}
            stake={stake}
            onChangeStake={setStake}
            onRemoveSlipItem={handleRemoveSlipItem}
            onClearSlip={handleClearSlip}
            onSaveSlip={handleSaveSlip}
            onCopySlipLink={handleCopySlipLink}
            onShareSlip={openSlipShare}
            refreshTrigger={refreshTrigger}
          />
        )}
        {tab === "coupons" && (
          <CouponsPage
            subTab={couponsView}
            onSubTabChange={setCouponsView}
            slipItems={slipItems}
            stake={stake}
            onChangeStake={setStake}
            onRemoveSlipItem={handleRemoveSlipItem}
            onClearSlip={handleClearSlip}
            onImportSlip={handleImportSlip}
            onSelectMatch={(matchId, couponPick, predictionId) =>
              setSelectedMatch({ matchId, couponPick, predictionId })
            }
            savedSlips={savedSlips}
            onSaveSlip={handleSaveSlip}
            onCopySlipLink={handleCopySlipLink}
            onDeleteSavedSlip={handleDeleteSavedSlip}
            refreshTrigger={refreshTrigger}
          />
        )}
        {tab === "toto" && (
          <FikstraTotoPage
            telegramInviteUrl={TELEGRAM_INVITE_URL}
            initialSlipParam={totoSlipParam}
            onConsumeSlipParam={() => setTotoSlipParam(null)}
            refreshTrigger={refreshTrigger}
            onSelectMatch={(matchId, isSettled) => setSelectedMatch({ matchId, isSettled })}
          />
        )}
        {tab === "performance" && (
          <PerformancePage
            refreshTrigger={refreshTrigger}
            onSelectMatch={(matchId, predictionId) =>
              setSelectedMatch({ matchId, predictionId, isSettled: true })
            }
          />
        )}

        {/* Subtle Transparency Footer */}
        <footer className="mt-8 pt-4 border-t border-white/[0.06] flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500">
          <span className="inline-flex items-center gap-2">
            <img src="/logo-mark.png" alt="Fikstra" className="w-4 h-4 object-contain" />
            Fikstra • Veri odaklı maç analizi (18+)
          </span>
          <button
            type="button"
            onClick={() => setHowItWorksOpen(true)}
            className="inline-flex items-center gap-1 text-slate-400 hover:text-[#00FF85] font-semibold transition-colors"
          >
            <HelpCircle className="w-3.5 h-3.5" />
            Nasıl Çalışır?
          </button>
        </footer>
      </main>

      {/* Undo Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            className="fixed left-4 right-4 md:left-auto md:right-6 bottom-[calc(8.2rem+env(safe-area-inset-bottom))] md:bottom-20 z-50 md:max-w-sm pointer-events-none"
          >
            <div className="pointer-events-auto px-4 py-2.5 rounded-xl bg-[#0B111C]/95 backdrop-blur-xl border border-emerald-500/35 shadow-2xl flex items-center justify-between gap-3 text-xs">
              <span className="text-slate-100 font-medium truncate">{toast.message}</span>
              {toast.undoItems && (
                <button
                  type="button"
                  onClick={() => {
                    setSlipItems(toast.undoItems!);
                    setToast(null);
                  }}
                  className="text-[#00FF85] font-extrabold shrink-0 hover:underline"
                >
                  Geri al
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Desktop Floating Kuponum Fişi Bar (hidden on Coupons tab, and hidden on lg+ Highlights where right panel is shown) */}
      {slipItems.length > 0 && tab !== "coupons" && (
        <div
          className={`hidden md:block ${
            tab === "highlights" ? "lg:hidden" : ""
          } fixed bottom-4 inset-x-0 z-30 px-4 pointer-events-none`}
        >
          <div className="max-w-3xl mx-auto pointer-events-auto p-3 rounded-2xl bg-[#080E18]/95 backdrop-blur-xl border border-[#00FF85]/45 shadow-2xl shadow-[#00FF85]/15 space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-xs font-bold text-[#00FF85] flex-wrap">
                  <Ticket className="w-3.5 h-3.5 shrink-0" />
                  <span>Kuponum Fişi ({slipItems.length} Maç)</span>
                  <span className="mono-nums px-2 py-0.5 rounded-md bg-[#00FF85]/20 text-[#00FF85]">
                    {customTotalOdd.toFixed(2)} Oran
                  </span>
                  <span
                    className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold border mono-nums ${
                      slipRisk.riskTone === "emerald"
                        ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/35"
                        : slipRisk.riskTone === "amber"
                        ? "bg-amber-500/15 text-amber-300 border-amber-500/35"
                        : "bg-rose-500/15 text-rose-300 border-rose-500/35"
                    }`}
                  >
                    {slipRisk.riskLabel} · %{slipRisk.combinedPct}
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
                  type="button"
                  onClick={handleCopySlipLink}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl glass-card text-slate-200 hover:border-[#00FF85]/45 text-xs font-bold"
                >
                  <Link2 className="w-3.5 h-3.5 text-[#00FF85]" />
                  Linkle Gönder
                </button>
                <button
                  type="button"
                  onClick={handleSaveSlip}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-emerald-500/15 border border-emerald-500/35 text-[#00FF85] text-xs font-bold hover:bg-emerald-500/25"
                >
                  <BookmarkPlus className="w-3.5 h-3.5" />
                  Kuponu Takibe Al
                </button>
                <button
                  onClick={openSlipShare}
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
            <StakeCalculator
              totalOdd={customTotalOdd}
              stake={stake}
              onChangeStake={setStake}
              compact
            />
          </div>
        </div>
      )}

      {/* Mobile Slim Floating Mini Kupon Bar (above bottom nav, hidden inside Kuponlar tab) */}
      {slipItems.length > 0 && tab !== "coupons" && (
        <div className="md:hidden fixed bottom-[calc(4.75rem+env(safe-area-inset-bottom))] inset-x-3 z-30 pointer-events-none">
          <button
            type="button"
            onClick={() => setSlipSheetOpen(true)}
            className="pointer-events-auto w-full px-4 py-2.5 rounded-xl bg-[#080E18]/95 backdrop-blur-xl border border-[#00FF85]/45 shadow-[0_8px_24px_-6px_rgba(0,255,133,0.3)] flex items-center justify-between gap-2 text-xs"
          >
            <div className="flex items-center gap-2 font-bold text-white mono-nums">
              <Ticket className="w-4 h-4 text-[#00FF85]" />
              <span>{slipItems.length} maç</span>
              <span className="text-slate-500">·</span>
              <span className="text-[#00FF85]">{customTotalOdd.toFixed(2)}</span>
            </div>
            <span className="inline-flex items-center gap-0.5 text-[#00FF85] font-bold">
              Aç <ChevronRight className="w-3.5 h-3.5" />
            </span>
          </button>
        </div>
      )}

      {/* Mobile Slip Bottom Sheet Drawer */}
      <AnimatePresence>
        {slipSheetOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setSlipSheetOpen(false)}
            className="md:hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-end"
          >
            <motion.div
              initial={{ y: 64, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: 64, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-h-[78vh] overflow-y-auto rounded-t-3xl bg-[#070B12]/98 border-t border-emerald-500/30 p-5 pb-[calc(1.5rem+env(safe-area-inset-bottom))] space-y-4"
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="text-sm font-extrabold text-[#00FF85]">Kuponum Fişi</h3>
                    <span
                      className={`px-2 py-0.5 rounded-full text-[10px] font-extrabold border mono-nums ${
                        slipRisk.riskTone === "emerald"
                          ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/35"
                          : slipRisk.riskTone === "amber"
                          ? "bg-amber-500/15 text-amber-300 border-amber-500/35"
                          : "bg-rose-500/15 text-rose-300 border-rose-500/35"
                      }`}
                    >
                      {slipRisk.riskLabel} · %{slipRisk.combinedPct}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-400 mono-nums mt-0.5">
                    {slipItems.length} maç • Toplam Oran: {customTotalOdd.toFixed(2)}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSlipSheetOpen(false)}
                  className="p-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400"
                  aria-label="Kapat"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-2">
                {slipItems.map((s) => (
                  <div
                    key={s.match_id}
                    className="flex items-center justify-between gap-2 px-3 py-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-xs"
                  >
                    <div className="min-w-0">
                      <p className="font-semibold text-slate-100 truncate">
                        {s.home_team} - {s.away_team}
                      </p>
                      <p className="text-[11px] text-emerald-400 font-bold">
                        {formatMarketLabel(s.market_key, s.selection_key)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2.5 shrink-0">
                      <span className="mono-nums font-extrabold text-white">
                        {s.iddaa_odd.toFixed(2)}
                      </span>
                      <button
                        type="button"
                        onClick={() => handleRemoveSlipItem(s.match_id)}
                        className="w-8 h-8 rounded-lg bg-white/5 text-slate-400 hover:text-rose-400 flex items-center justify-center"
                        aria-label="Çıkar"
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
                onChangeStake={setStake}
              />

              {slipRisk.weakestItem &&
                slipRisk.weakestPct !== null &&
                slipRisk.withoutWeakestPct !== null && (
                  <div className="flex items-center justify-between gap-2 px-3 py-2 rounded-xl bg-amber-500/10 border border-amber-500/25 text-[11px] text-amber-200">
                    <span className="truncate">
                      En riskli bacak:{" "}
                      <strong className="text-white">
                        {slipRisk.weakestItem.home_team} - {slipRisk.weakestItem.away_team}
                      </strong>{" "}
                      <span className="mono-nums">(%{slipRisk.weakestPct})</span> · Çıkarırsan{" "}
                      <span className="mono-nums font-bold text-emerald-300">
                        %{slipRisk.combinedPct} → %{slipRisk.withoutWeakestPct}
                      </span>
                    </span>
                    <button
                      type="button"
                      onClick={() => handleRemoveSlipItem(slipRisk.weakestItem!.match_id)}
                      className="px-2 py-1 rounded-lg bg-amber-500/20 hover:bg-rose-500/25 text-amber-100 hover:text-rose-200 font-bold shrink-0 text-[10px]"
                    >
                      Çıkar
                    </button>
                  </div>
                )}

              <div className="flex items-center gap-2 pt-2">
                <button
                  type="button"
                  onClick={handleCopySlipLink}
                  className="px-3 py-3 rounded-xl glass-card text-slate-200 text-xs font-bold inline-flex items-center gap-1"
                  aria-label="Linkle Gönder"
                >
                  <Link2 className="w-4 h-4 text-[#00FF85]" />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    handleSaveSlip();
                    setSlipSheetOpen(false);
                  }}
                  className="px-3 py-3 rounded-xl bg-emerald-500/15 border border-emerald-500/35 text-[#00FF85] text-xs font-bold inline-flex items-center gap-1"
                >
                  <BookmarkPlus className="w-4 h-4" />
                  Takibe Al
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSlipSheetOpen(false);
                    openSlipShare();
                  }}
                  className="flex-1 inline-flex items-center justify-center gap-1.5 py-3 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold"
                >
                  <Share2 className="w-4 h-4" />
                  Paylaş
                </button>
                <button
                  type="button"
                  onClick={handleClearSlip}
                  className="px-3 py-3 rounded-xl bg-[#0B0F17] border border-white/10 text-slate-300 hover:text-rose-400 text-xs font-bold inline-flex items-center gap-1"
                  aria-label="Temizle"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Mobile Fixed Opaque Symmetric 4-Tab Bottom Navigation Dock */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 bg-[#05080D] border-t border-emerald-500/20 shadow-[0_-10px_30px_-10px_rgba(0,0,0,0.9)] px-2 pt-1.5 pb-[calc(0.5rem+env(safe-area-inset-bottom))]">
        <div className="relative grid grid-cols-4 items-center">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`relative flex flex-col items-center justify-center py-1.5 px-0.5 rounded-xl transition-colors min-w-0 ${
                  active ? "text-[#00FF85]" : "text-slate-400"
                }`}
              >
                <div className="relative">
                  <Icon className="w-4 h-4" />
                  {t.key === "coupons" && slipItems.length > 0 && (
                    <span className="absolute -top-2 -right-3 min-w-[16px] h-[16px] px-1 rounded-full bg-[#00FF85] text-[#05080D] text-[9px] font-extrabold mono-nums flex items-center justify-center shadow">
                      {slipItems.length}
                    </span>
                  )}
                  {t.badge && (
                    <span className="absolute -top-2 -right-5 px-1 py-[1px] rounded-full bg-[#00FF85] text-[#05080D] text-[7.5px] font-extrabold leading-none shadow">
                      {t.badge}
                    </span>
                  )}
                </div>
                <span
                  className={`text-[10px] font-semibold mt-0.5 truncate max-w-full ${
                    t.badge ? "toto-shimmer-text font-extrabold" : ""
                  }`}
                >
                  {t.mobileLabel || t.label}
                </span>
                {active && (
                  <span className="absolute bottom-0.5 w-6 h-[2.5px] rounded-full bg-[#00FF85] shadow-[0_0_8px_#00FF85]" />
                )}
              </button>
            );
          })}
        </div>
      </nav>

      <NotificationCenter
        open={notifOpen}
        onClose={() => setNotifOpen(false)}
        pushStatus={pushStatus}
        onEnablePush={handleEnablePush}
        onNavigateUrl={(url) => {
          try {
            const parsed = new URL(url, window.location.origin);
            const t = parsed.searchParams.get("tab");
            const m = Number(parsed.searchParams.get("match"));
            const p = Number(parsed.searchParams.get("pred"));
            if (t === "coupons" || t === "toto" || t === "performance" || t === "highlights") {
              setTab(t);
            }
            if (Number.isInteger(m) && m > 0) {
              setSelectedMatch({
                matchId: m,
                predictionId: Number.isInteger(p) && p > 0 ? p : undefined,
              });
            }
          } catch {
            // ignore
          }
        }}
      />

      {/* Match Detail Bottom Sheet / Modal (Pre-match vs Settled) */}
      <MatchDetailDrawer
        matchId={!selectedMatch?.isSettled ? (selectedMatch?.matchId ?? null) : null}
        predictionId={selectedMatch?.predictionId}
        couponPick={selectedMatch?.couponPick}
        onClose={handleCloseMatch}
        onSettledDetected={(d) =>
          setSelectedMatch({
            matchId: d.match_id,
            predictionId: d.top_prediction?.prediction_id ?? selectedMatch?.predictionId,
            isSettled: true,
            initialDetail: d,
          })
        }
        slipItems={slipItems}
        onToggleSlip={handleToggleSlip}
      />
      <SettledMatchDrawer
        matchId={selectedMatch?.isSettled ? selectedMatch.matchId : null}
        predictionId={selectedMatch?.predictionId}
        initialDetail={selectedMatch?.initialDetail}
        onClose={handleCloseMatch}
      />
      <CouponShareModal payload={slipSharePayload} onClose={() => setSlipSharePayload(null)} />

      {/* Fikstra Nasıl Çalışır? Transparency Modal */}
      <AnimatePresence>
        {howItWorksOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setHowItWorksOpen(false)}
            className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4"
          >
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-md rounded-3xl bg-[#070B12] border border-emerald-500/30 p-5 space-y-4 shadow-2xl"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <img src="/logo-mark.png" alt="Fikstra" className="w-6 h-6 object-contain" />
                  <h3 className="text-sm font-extrabold text-white">Fikstra Nasıl Çalışır?</h3>
                </div>
                <button
                  type="button"
                  onClick={() => setHowItWorksOpen(false)}
                  className="p-1.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400 hover:text-white"
                  aria-label="Kapat"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-2.5 text-xs text-slate-300 leading-relaxed">
                <div className="p-3 rounded-xl bg-[#0B111C] border border-white/10">
                  <p className="font-bold text-emerald-300 mb-0.5">1. Form, Gol Gücü &amp; Kadro Analizi</p>
                  <p>Takımların formu, gol gücü, ev-deplasman performansı ve eksik oyuncu bilgileri birlikte analiz edilir.</p>
                </div>
                <div className="p-3 rounded-xl bg-[#0B111C] border border-white/10">
                  <p className="font-bold text-emerald-300 mb-0.5">2. Güncel Piyasa Oranları &amp; Oran Hareketleri</p>
                  <p>İddaa oranları güncel piyasa oranları ve sert oran düşüşleriyle kıyaslanarak değerli seçimler öne çıkarılır.</p>
                </div>
                <div className="p-3 rounded-xl bg-[#0B111C] border border-white/10">
                  <p className="font-bold text-emerald-300 mb-0.5">3. Kilitli Şeffaflık (Sıfır Sahte Veri)</p>
                  <p>Yayınlanan her tahmin o andaki oranıyla kilitlenir, sonradan değiştirilmez ve sahte geçmiş üretilmez.</p>
                </div>
              </div>

              <p className="text-[11px] text-slate-500 text-center">
                İstatistiksel tahmindir, kesin kazanç garantisi vermez • 18+
              </p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default App;
