import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, BellRing, ChevronRight, X } from "lucide-react";
import { api } from "../api/client";
import type { NotificationItem } from "../types";

interface Props {
  open: boolean;
  onClose: () => void;
  pushStatus: "idle" | "enabled" | "denied" | "unsupported";
  onEnablePush: () => void;
  onNavigateUrl: (url: string) => void;
}

const CATEGORY_META: Record<string, { label: string; cls: string }> = {
  high_conf: {
    label: "Yüksek Güven",
    cls: "bg-emerald-500/15 text-[#00FF85] border-emerald-500/35",
  },
  odds_drop: {
    label: "Oran Düştü",
    cls: "bg-amber-500/15 text-amber-300 border-amber-500/35",
  },
  coupon: {
    label: "Kupon",
    cls: "bg-cyan-500/15 text-cyan-300 border-cyan-500/35",
  },
  settled: {
    label: "Sonuç",
    cls: "bg-purple-500/15 text-purple-300 border-purple-500/35",
  },
  toto: {
    label: "Fikstra Toto",
    cls: "bg-[#00FF85]/15 text-[#00FF85] border-[#00FF85]/40",
  },
  info: {
    label: "Bilgi",
    cls: "bg-slate-800 text-slate-300 border-slate-700",
  },
};

function formatRelativeTr(iso: string | null): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const diffMin = Math.max(0, Math.floor((Date.now() - ms) / 60000));
  if (diffMin < 1) return "Az önce";
  if (diffMin < 60) return `${diffMin} dk önce`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr} sa önce`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay} gün önce`;
}

export function NotificationCenter({
  open,
  onClose,
  pushStatus,
  onEnablePush,
  onNavigateUrl,
}: Props) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api
      .getRecentNotifications()
      .then((res) => {
        if (!cancelled) setItems(res.items || []);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-start sm:items-center justify-center p-3 sm:p-4 pt-16 sm:pt-4"
        >
          <motion.div
            initial={{ y: -12, opacity: 0, scale: 0.98 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: -12, opacity: 0, scale: 0.98 }}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md max-h-[80vh] flex flex-col rounded-3xl bg-[#070B12] border border-emerald-500/30 shadow-2xl overflow-hidden"
          >
            {/* Header */}
            <div className="px-4 py-3.5 border-b border-white/10 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                {pushStatus === "enabled" ? (
                  <BellRing className="w-4 h-4 text-[#00FF85]" />
                ) : (
                  <Bell className="w-4 h-4 text-slate-300" />
                )}
                <h3 className="text-sm font-extrabold text-white">Bildirim Merkezi</h3>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="p-1.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400 hover:text-white"
                aria-label="Kapat"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Push Toggle Box */}
            <div className="p-4 border-b border-white/10 bg-[#0B111C]/70 space-y-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs font-bold text-slate-100">
                    {pushStatus === "enabled"
                      ? "Anlık Bildirimler Açık"
                      : "Anlık Bildirimleri Aç"}
                  </p>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Günün kuponları, oran düşüşleri ve maç sonuçları hazır olunca haber ver.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onEnablePush}
                  disabled={pushStatus === "enabled"}
                  className={`shrink-0 px-3 py-1.5 rounded-xl text-xs font-extrabold transition-colors ${
                    pushStatus === "enabled"
                      ? "bg-emerald-500/15 text-[#00FF85] border border-emerald-500/35"
                      : "bg-[#00FF85] text-[#05080D] hover:bg-emerald-400"
                  }`}
                >
                  {pushStatus === "enabled" ? "Aktif ✓" : "Aç"}
                </button>
              </div>
              {(pushStatus === "denied" ||
                (typeof Notification !== "undefined" && Notification.permission === "denied")) && (
                <p className="text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/25 rounded-lg px-2.5 py-1.5">
                  Tarayıcı ayarlarından bildirim izni verdiğinde anlık uyarılar açılır.
                </p>
              )}
            </div>

            {/* Recent Notifications List */}
            <div className="flex-1 overflow-y-auto p-3 space-y-2">
              {loading ? (
                <div className="space-y-2">
                  <div className="h-16 rounded-2xl bg-white/[0.03] animate-pulse" />
                  <div className="h-16 rounded-2xl bg-white/[0.03] animate-pulse" />
                </div>
              ) : items.length === 0 ? (
                <div className="py-8 text-center space-y-1">
                  <p className="text-xs font-semibold text-slate-300">Henüz bildirim yok</p>
                  <p className="text-[11px] text-slate-500">
                    Yeni kuponlar, oran düşüşleri ve sonuçlar burada listelenir.
                  </p>
                </div>
              ) : (
                items.map((n) => {
                  const meta = CATEGORY_META[n.category] || CATEGORY_META.info;
                  const rel = formatRelativeTr(n.sent_at);
                  return (
                    <button
                      key={n.id}
                      type="button"
                      onClick={() => {
                        onNavigateUrl(n.url || "/");
                        onClose();
                      }}
                      className="w-full text-left p-3 rounded-2xl bg-[#0B111C]/80 border border-white/[0.07] hover:border-[#00FF85]/35 transition-colors space-y-1"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${meta.cls}`}
                        >
                          {meta.label}
                        </span>
                        <span className="text-[10px] text-slate-500 mono-nums">{rel}</span>
                      </div>
                      <p className="text-xs font-bold text-slate-100 leading-snug">{n.title}</p>
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[11px] text-slate-400 leading-snug">{n.body}</p>
                        <ChevronRight className="w-3.5 h-3.5 text-slate-500 shrink-0" />
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
