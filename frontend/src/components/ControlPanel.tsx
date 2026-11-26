import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Download,
  KeyRound,
  LogOut,
  Megaphone,
  Plus,
  RefreshCw,
  Send,
  Shield,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import type { AdminOverviewData } from "../types";
import { SportotoAdminPanel } from "./SportotoAdminPanel";

const SESSION_SECRET_KEY = "fikstra_kontrol_secret";

function formatShortTr(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("tr-TR", {
      timeZone: "Europe/Istanbul",
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function ControlPanel({ onExit }: { onExit: () => void }) {
  const [secret, setSecret] = useState<string>(() => {
    try {
      return sessionStorage.getItem(SESSION_SECRET_KEY) || "";
    } catch {
      return "";
    }
  });
  const [secretInput, setSecretInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [overview, setOverview] = useState<AdminOverviewData | null>(null);
  const [loading, setLoading] = useState(false);

  const [annText, setAnnText] = useState("");
  const [annLink, setAnnLink] = useState("");
  const [annHours, setAnnHours] = useState<string>("24");
  const [annBusy, setAnnBusy] = useState(false);
  const [tgResult, setTgResult] = useState<string | null>(null);
  const [tgBusy, setTgBusy] = useState(false);
  const [csvBusy, setCsvBusy] = useState(false);

  const loadOverview = useCallback(async (sec: string) => {
    if (!sec) return;
    setLoading(true);
    setAuthError(null);
    try {
      const data = await api.adminOverview(sec);
      setOverview(data);
      try {
        sessionStorage.setItem(SESSION_SECRET_KEY, sec);
      } catch {
        // ignore
      }
    } catch {
      setAuthError("Yetkisiz erişim veya geçersiz X-Cron-Secret.");
      setOverview(null);
      setSecret("");
      try {
        sessionStorage.removeItem(SESSION_SECRET_KEY);
      } catch {
        // ignore
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (secret) {
      loadOverview(secret);
    }
  }, [secret, loadOverview]);

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = secretInput.trim();
    if (!cleaned) return;
    setSecret(cleaned);
    setSecretInput("");
  };

  const handleLogout = () => {
    try {
      sessionStorage.removeItem(SESSION_SECRET_KEY);
    } catch {
      // ignore
    }
    setSecret("");
    setOverview(null);
    setTgResult(null);
  };

  const handleCreateAnnouncement = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!secret || !annText.trim()) return;
    setAnnBusy(true);
    try {
      const hours = annHours ? Number(annHours) : null;
      await api.adminCreateAnnouncement(secret, {
        text: annText.trim(),
        link_url: annLink.trim() || null,
        expires_hours: hours && Number.isFinite(hours) && hours > 0 ? hours : null,
      });
      setAnnText("");
      setAnnLink("");
      await loadOverview(secret);
    } finally {
      setAnnBusy(false);
    }
  };

  const handleToggleAnnouncement = async (id: number, currentActive: boolean) => {
    if (!secret) return;
    await api.adminPatchAnnouncement(secret, id, !currentActive);
    await loadOverview(secret);
  };

  const handleDeleteAnnouncement = async (id: number) => {
    if (!secret) return;
    await api.adminDeleteAnnouncement(secret, id);
    await loadOverview(secret);
  };

  const handleTelegramTest = async () => {
    if (!secret) return;
    setTgBusy(true);
    setTgResult(null);
    try {
      const res = await api.adminTelegramTest(secret);
      setTgResult(
        res.ok
          ? "Telegram 4 konuya test mesajı başarıyla gönderildi."
          : `Test kısmen/tamamen başarısız: ${res.error || "Konu ayarlarını kontrol et"}`
      );
    } catch {
      setTgResult("Telegram test isteği sırasında hata oluştu.");
    } finally {
      setTgBusy(false);
    }
  };

  const handleExportCsv = async () => {
    if (!secret) return;
    setCsvBusy(true);
    try {
      const blob = await api.adminExportCalibrationCsv(secret);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const today = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Istanbul" });
      a.href = url;
      a.download = `fikstra_kalibrasyon_${today}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setCsvBusy(false);
    }
  };

  if (!secret || !overview) {
    return (
      <div className="min-h-screen bg-[#05080D] text-slate-100 flex items-center justify-center p-4">
        <div className="w-full max-w-sm p-6 rounded-2xl glass-card border border-emerald-500/25 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-[#00FF85]">
                <Shield className="w-4 h-4" />
              </div>
              <div>
                <h1 className="text-sm font-extrabold text-white">Fikstra Kaptan Köşkü</h1>
                <p className="text-[11px] text-slate-400">Yalnızca yetkili erişim</p>
              </div>
            </div>
            <button
              type="button"
              onClick={onExit}
              className="text-xs text-slate-400 hover:text-white"
            >
              Siteye Dön
            </button>
          </div>

          <form onSubmit={handleLogin} className="space-y-3">
            <div>
              <label className="text-[11px] font-semibold text-slate-400 block mb-1">
                X-Cron-Secret
              </label>
              <div className="relative">
                <KeyRound className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="password"
                  value={secretInput}
                  onChange={(e) => setSecretInput(e.target.value)}
                  placeholder="Gizli anahtarı girin"
                  autoFocus
                  className="w-full pl-9 pr-3 py-2.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] focus:border-[#00FF85]/60 outline-none text-xs text-white"
                />
              </div>
            </div>

            {authError && (
              <p className="text-[11px] text-rose-400 bg-rose-500/10 border border-rose-500/30 rounded-lg px-2.5 py-1.5">
                {authError}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-extrabold transition-colors disabled:opacity-60"
            >
              {loading ? "Doğrulanıyor..." : "Giriş Yap"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#05080D] text-slate-100 px-4 py-5">
      <div className="max-w-4xl mx-auto space-y-4">
        {/* Top bar */}
        <div className="flex flex-wrap items-center justify-between gap-2 p-4 rounded-2xl glass-card">
          <div className="flex items-center gap-2.5">
            <Shield className="w-5 h-5 text-[#00FF85]" />
            <div>
              <h1 className="text-base font-extrabold text-white">Fikstra Kaptan Köşkü</h1>
              <p className="text-[11px] text-slate-400">0 Odds API kredili canlı sistem özeti</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onExit}
              className="px-3 py-1.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-xs text-slate-300 hover:text-white"
            >
              Siteye Dön
            </button>
            <button
              type="button"
              onClick={handleLogout}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs font-semibold text-rose-300 hover:bg-rose-500/20"
            >
              <LogOut className="w-3.5 h-3.5" />
              Çıkış
            </button>
          </div>
        </div>

        {/* 1. System Health KPI Cards */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
          <div className="p-3.5 rounded-2xl glass-card">
            <span className="text-[10px] text-slate-400 block">Odds API Kalan</span>
            <span className="text-lg font-extrabold mono-nums text-[#00FF85]">
              {overview.odds_api_remaining ?? "—"}
            </span>
          </div>
          <div className="p-3.5 rounded-2xl glass-card">
            <span className="text-[10px] text-slate-400 block">Son Sync / Settle</span>
            <span className="text-xs font-bold mono-nums text-slate-100 block mt-0.5">
              {formatShortTr(overview.last_sync_at)}
            </span>
            <span className="text-[10px] text-slate-500 mono-nums block">
              S: {formatShortTr(overview.last_settle_at)}
            </span>
          </div>
          <div className="p-3.5 rounded-2xl glass-card">
            <span className="text-[10px] text-slate-400 block">24s VOID</span>
            <span
              className={`text-lg font-extrabold mono-nums ${
                overview.void_24h_count >= 5 ? "text-amber-400" : "text-slate-100"
              }`}
            >
              {overview.void_24h_count}
            </span>
          </div>
          <div className="p-3.5 rounded-2xl glass-card">
            <span className="text-[10px] text-slate-400 block">Gemini Devre Kesici</span>
            <span
              className={`inline-flex items-center gap-1 text-xs font-bold mt-1 ${
                overview.gemini_circuit_open ? "text-amber-400" : "text-[#00FF85]"
              }`}
            >
              {overview.gemini_circuit_open ? (
                <>
                  <AlertTriangle className="w-3.5 h-3.5" /> Açık
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5" /> Normal
                </>
              )}
            </span>
          </div>
          <div className="p-3.5 rounded-2xl glass-card col-span-2 sm:col-span-1">
            <span className="text-[10px] text-slate-400 block">Admin Telegram Alarm</span>
            <span
              className={`text-xs font-bold block mt-1 ${
                overview.telegram_admin_configured ? "text-[#00FF85]" : "text-slate-400"
              }`}
            >
              {overview.telegram_admin_configured ? "Aktif" : "Ayarlı Değil"}
            </span>
          </div>
        </div>

        {/* 2. Quick Actions */}
        <div className="p-4 rounded-2xl glass-card flex flex-wrap items-center justify-between gap-2.5">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-[#00FF85]" />
            <span className="text-xs font-bold text-white">Hızlı Aksiyonlar</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleTelegramTest}
              disabled={tgBusy}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#00FF85] hover:bg-emerald-400 text-[#05080D] text-xs font-extrabold disabled:opacity-60"
            >
              <Send className="w-3.5 h-3.5" />
              {tgBusy ? "Gönderiliyor..." : "Telegram Bağlantı Testi"}
            </button>
            <button
              type="button"
              onClick={handleExportCsv}
              disabled={csvBusy}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#0B0F17] border border-emerald-500/35 text-xs font-bold text-[#00FF85] hover:bg-emerald-500/10 disabled:opacity-60"
            >
              <Download className="w-3.5 h-3.5" />
              {csvBusy ? "İndiriliyor..." : "Kalibrasyon Verisi İndir (CSV)"}
            </button>
            <button
              type="button"
              onClick={() => loadOverview(secret)}
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-xs font-semibold text-slate-200 hover:border-[#00FF85]/40"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              Yenile
            </button>
          </div>
          {tgResult && (
            <p className="w-full text-[11px] text-emerald-300 bg-emerald-500/10 border border-emerald-500/25 rounded-lg px-3 py-1.5">
              {tgResult}
            </p>
          )}
        </div>

        {/* 3. Live Announcement Management */}
        <div className="p-4 rounded-2xl glass-card space-y-3">
          <div className="flex items-center gap-2">
            <Megaphone className="w-4 h-4 text-[#00FF85]" />
            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
              Canlı Duyuru Yönetimi
            </h2>
          </div>

          <form onSubmit={handleCreateAnnouncement} className="grid grid-cols-1 sm:grid-cols-12 gap-2">
            <input
              type="text"
              maxLength={220}
              value={annText}
              onChange={(e) => setAnnText(e.target.value)}
              placeholder="Tek satırlık duyuru metni..."
              className="sm:col-span-6 px-3 py-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] focus:border-[#00FF85]/50 outline-none text-xs text-white"
            />
            <input
              type="text"
              maxLength={300}
              value={annLink}
              onChange={(e) => setAnnLink(e.target.value)}
              placeholder="Opsiyonel link (https://... veya /?tab=coupons)"
              className="sm:col-span-3 px-3 py-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] focus:border-[#00FF85]/50 outline-none text-xs text-white"
            />
            <select
              value={annHours}
              onChange={(e) => setAnnHours(e.target.value)}
              className="sm:col-span-2 px-2.5 py-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-xs text-slate-200 outline-none"
            >
              <option value="24">24 saat</option>
              <option value="6">6 saat</option>
              <option value="72">72 saat</option>
              <option value="">Süresiz</option>
            </select>
            <button
              type="submit"
              disabled={annBusy || !annText.trim()}
              className="sm:col-span-1 inline-flex items-center justify-center gap-1 px-3 py-2 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold disabled:opacity-50"
              aria-label="Duyuru Ekle"
            >
              <Plus className="w-4 h-4" />
            </button>
          </form>

          {overview.announcements.length === 0 ? (
            <p className="text-xs text-slate-500">Henüz kayıtlı duyuru yok.</p>
          ) : (
            <div className="space-y-1.5">
              {overview.announcements.map((a) => (
                <div
                  key={a.id}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-xs"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-slate-100 font-medium truncate">{a.text}</p>
                    <p className="text-[10px] text-slate-500">
                      {a.link_url ? `${a.link_url} · ` : ""}
                      {a.expires_at ? `Bitiş: ${formatShortTr(a.expires_at)}` : "Süresiz"}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => handleToggleAnnouncement(a.id, a.is_active)}
                      className={`px-2 py-1 rounded-lg text-[10px] font-bold border ${
                        a.is_active
                          ? "bg-emerald-500/15 text-[#00FF85] border-emerald-500/30"
                          : "bg-slate-800 text-slate-400 border-slate-700"
                      }`}
                    >
                      {a.is_active ? "Aktif" : "Pasif"}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDeleteAnnouncement(a.id)}
                      className="p-1.5 rounded-lg bg-white/5 text-slate-400 hover:text-rose-400"
                      aria-label="Duyuruyu sil"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 4. Last 15 Telegram Dispatches */}
        <div className="p-4 rounded-2xl glass-card space-y-2.5">
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
            Son 15 Telegram Yayını
          </h2>
          {overview.recent_dispatches.length === 0 ? (
            <p className="text-xs text-slate-500">Henüz Telegram yayın kaydı yok.</p>
          ) : (
            <div className="divide-y divide-[#1F2937] text-xs">
              {overview.recent_dispatches.map((d) => (
                <div key={d.id} className="py-2 flex items-center justify-between gap-2">
                  <div className="min-w-0 flex items-center gap-2">
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-bold">
                      {d.kind}
                    </span>
                    <span className="text-slate-200 font-mono text-[11px] truncate">
                      {d.dispatch_key}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 text-[11px]">
                    <span className={d.settled_replied ? "text-[#00FF85]" : "text-slate-500"}>
                      {d.settled_replied ? "Yanıtlandı" : "Açık"}
                    </span>
                    <span className="text-slate-500 mono-nums">{formatShortTr(d.updated_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 5. Spor Toto 15'li Management */}
        <SportotoAdminPanel secret={secret} />
      </div>
    </div>
  );
}
