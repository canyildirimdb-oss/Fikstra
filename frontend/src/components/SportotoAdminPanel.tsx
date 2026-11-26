import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Grid3X3, RefreshCw, Save, Send, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { SportotoWeekData } from "../types";

interface RowDraft {
  odd_1: string;
  odd_x: string;
  odd_2: string;
  actual_result: string;
}

export function SportotoAdminPanel({ secret }: { secret: string }) {
  const [weeks, setWeeks] = useState<SportotoWeekData[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [title, setTitle] = useState("Spor Toto Haftası");
  const [rawText, setRawText] = useState("");
  const [rowEdits, setRowEdits] = useState<Record<number, RowDraft>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const loadWeeks = useCallback(
    async (preferId?: number) => {
      try {
        const res = await api.adminGetSportotoWeeks(secret);
        setWeeks(res.items || []);
        const target =
          (preferId && res.items.find((w) => w.id === preferId)) ||
          (selectedId && res.items.find((w) => w.id === selectedId)) ||
          res.items[0] ||
          null;
        if (target) {
          setSelectedId(target.id);
          const drafts: Record<number, RowDraft> = {};
          for (const m of target.matches) {
            drafts[m.slot_no] = {
              odd_1: m.odd_1 !== null ? String(m.odd_1) : "",
              odd_x: m.odd_x !== null ? String(m.odd_x) : "",
              odd_2: m.odd_2 !== null ? String(m.odd_2) : "",
              actual_result: m.actual_result || "",
            };
          }
          setRowEdits(drafts);
        } else {
          setSelectedId(null);
          setRowEdits({});
        }
      } catch {
        // ignore
      }
    },
    [secret, selectedId]
  );

  useEffect(() => {
    loadWeeks();
  }, [secret]); // eslint-disable-line react-hooks/exhaustive-deps

  const activeWeek = weeks.find((w) => w.id === selectedId) || null;

  const selectWeek = (w: SportotoWeekData) => {
    setSelectedId(w.id);
    const drafts: Record<number, RowDraft> = {};
    for (const m of w.matches) {
      drafts[m.slot_no] = {
        odd_1: m.odd_1 !== null ? String(m.odd_1) : "",
        odd_x: m.odd_x !== null ? String(m.odd_x) : "",
        odd_2: m.odd_2 !== null ? String(m.odd_2) : "",
        actual_result: m.actual_result || "",
      };
    }
    setRowEdits(drafts);
    setMsg(null);
    setErr(null);
  };

  const handleParse = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!rawText.trim()) return;
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const created = await api.adminParseSportoto(secret, {
        title: title.trim() || "Spor Toto Haftası",
        raw_text: rawText,
      });
      setRawText("");
      await loadWeeks(created.id);
      setMsg(`Taslak oluşturuldu (${15 - created.missing_count}/15 eşleşti).`);
    } catch (error) {
      setErr(error instanceof Error ? error.message : "Taslak oluşturulamadı");
    } finally {
      setBusy(false);
    }
  };

  const handleSaveRows = async (autoSettle = false) => {
    if (!activeWeek) return;
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const matchesPayload = activeWeek.matches.map((m) => {
        const d = rowEdits[m.slot_no];
        const o1 = d?.odd_1 ? Number(d.odd_1.replace(",", ".")) : null;
        const ox = d?.odd_x ? Number(d.odd_x.replace(",", ".")) : null;
        const o2 = d?.odd_2 ? Number(d.odd_2.replace(",", ".")) : null;
        const validOdds = o1 && ox && o2 && o1 > 1 && ox > 1 && o2 > 1;
        return {
          slot_no: m.slot_no,
          ...(validOdds ? { odd_1: o1, odd_x: ox, odd_2: o2 } : {}),
          actual_result: d?.actual_result ?? "",
        };
      });
      const updated = await api.adminPatchSportotoMatches(secret, activeWeek.id, {
        matches: matchesPayload,
        auto_settle_from_db: autoSettle,
      });
      await loadWeeks(updated.id);
      setMsg(autoSettle ? "DB skorları çekildi ve kaydedildi." : "Değişiklikler kaydedildi.");
    } catch (error) {
      setErr(error instanceof Error ? error.message : "Kaydetme hatası");
    } finally {
      setBusy(false);
    }
  };

  const handlePublish = async () => {
    if (!activeWeek) return;
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const updated = await api.adminPublishSportoto(secret, activeWeek.id);
      await loadWeeks(updated.id);
      setMsg("Hafta başarıyla yayınlandı!");
    } catch (error) {
      setErr(error instanceof Error ? error.message : "Yayınlama başarısız");
    } finally {
      setBusy(false);
    }
  };

  const handleTelegram = async () => {
    if (!activeWeek) return;
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const res = await api.adminTelegramSportoto(secret, activeWeek.id);
      setMsg(
        res.sent
          ? "Spor Toto 15'li Telegram'a gönderildi."
          : `Gönderilmedi (${res.reason || res.error || "zaten gönderilmiş"})`
      );
    } catch (error) {
      setErr(error instanceof Error ? error.message : "Telegram gönderim hatası");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!activeWeek) return;
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      await api.adminDeleteSportoto(secret, activeWeek.id);
      await loadWeeks();
      setMsg("Hafta silindi.");
    } catch (error) {
      setErr(error instanceof Error ? error.message : "Silme hatası");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-4 rounded-2xl glass-card space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Grid3X3 className="w-4 h-4 text-[#00FF85]" />
          <h2 className="text-xs font-bold uppercase tracking-wider text-slate-200">
            Spor Toto 15&apos;li Yönetimi
          </h2>
        </div>
        {weeks.length > 0 && (
          <select
            value={selectedId ?? ""}
            onChange={(e) => {
              const found = weeks.find((w) => w.id === Number(e.target.value));
              if (found) selectWeek(found);
            }}
            className="px-2.5 py-1.5 rounded-lg bg-[#05080D] border border-[#1F2937] text-xs text-slate-200"
          >
            {weeks.map((w) => (
              <option key={w.id} value={w.id}>
                #{w.id} {w.title} ({w.status})
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Parse Form */}
      <form onSubmit={handleParse} className="space-y-2.5">
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Hafta başlığı (ör. 15. Hafta)"
          className="w-full px-3 py-2 rounded-xl bg-[#05080D] border border-[#1F2937] text-xs text-slate-100 focus:outline-none focus:border-[#00FF85]/60"
        />
        <textarea
          rows={4}
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          placeholder={"15 satır yapıştır:\nGalatasaray - Fenerbahçe\nBeşiktaş - Trabzonspor | 2.10 3.40 3.20"}
          className="w-full px-3 py-2 rounded-xl bg-[#05080D] border border-[#1F2937] text-xs text-slate-100 font-mono focus:outline-none focus:border-[#00FF85]/60"
        />
        <button
          type="submit"
          disabled={busy || !rawText.trim()}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold disabled:opacity-50"
        >
          Eşleştir &amp; Taslak Oluştur
        </button>
      </form>

      {msg && (
        <div className="px-3 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-[#00FF85]">
          {msg}
        </div>
      )}
      {err && (
        <div className="px-3 py-2 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300">
          {err}
        </div>
      )}

      {/* Active Week Editor */}
      {activeWeek && (
        <div className="space-y-3 pt-2 border-t border-[#1F2937]">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-bold text-white">{activeWeek.title}</span>
              <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 text-[10px] font-bold">
                {activeWeek.status}
              </span>
            </div>
            {activeWeek.missing_count > 0 ? (
              <span className="inline-flex items-center gap-1 text-amber-300 text-[11px] font-semibold">
                <AlertTriangle className="w-3.5 h-3.5" />
                {activeWeek.missing_count} maçta oran eksik (Slot: {activeWeek.missing_slots.join(", ")})
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-[#00FF85] text-[11px] font-semibold">
                <CheckCircle2 className="w-3.5 h-3.5" />
                15/15 veri tam
              </span>
            )}
          </div>

          <div className="space-y-1.5 max-h-[380px] overflow-y-auto pr-1">
            {activeWeek.matches.map((m) => {
              const ed = rowEdits[m.slot_no] || { odd_1: "", odd_x: "", odd_2: "", actual_result: "" };
              return (
                <div
                  key={m.slot_no}
                  className="p-2 rounded-xl bg-[#05080D]/80 border border-[#1F2937] flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs"
                >
                  <div className="min-w-0 flex items-center gap-2 flex-1">
                    <span className="text-[11px] font-bold text-slate-400 mono-nums w-5 shrink-0">
                      {m.slot_no}.
                    </span>
                    <span className="text-slate-100 font-medium truncate">
                      {m.home_team} - {m.away_team}
                    </span>
                    <span
                      className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        m.source === "fikstra_model"
                          ? "bg-emerald-500/15 text-[#00FF85]"
                          : m.source === "market_devig"
                          ? "bg-cyan-500/15 text-cyan-300"
                          : "bg-rose-500/15 text-rose-300"
                      }`}
                    >
                      {m.source === "fikstra_model"
                        ? "Model ✓"
                        : m.source === "market_devig"
                        ? "Piyasa ✓"
                        : "Oran Eksik !"}
                    </span>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    {(["odd_1", "odd_x", "odd_2"] as const).map((field, idx) => (
                      <input
                        key={field}
                        type="text"
                        inputMode="decimal"
                        placeholder={["1", "X", "2"][idx]}
                        value={ed[field]}
                        onChange={(e) =>
                          setRowEdits((prev) => ({
                            ...prev,
                            [m.slot_no]: { ...ed, [field]: e.target.value },
                          }))
                        }
                        className="w-12 px-1.5 py-1 rounded bg-[#0B121D] border border-[#1F2937] text-[11px] text-center text-slate-100 mono-nums"
                      />
                    ))}
                    <select
                      value={ed.actual_result}
                      onChange={(e) =>
                        setRowEdits((prev) => ({
                          ...prev,
                          [m.slot_no]: { ...ed, actual_result: e.target.value },
                        }))
                      }
                      className="px-1.5 py-1 rounded bg-[#0B121D] border border-[#1F2937] text-[11px] text-slate-200"
                      aria-label={`Slot ${m.slot_no} sonuç`}
                    >
                      <option value="">-</option>
                      <option value="1">1</option>
                      <option value="X">X</option>
                      <option value="2">2</option>
                    </select>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => handleSaveRows(false)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-xs font-bold text-slate-200 hover:bg-white/10 disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              Kaydet
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleSaveRows(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-xs font-bold text-slate-200 hover:bg-white/10 disabled:opacity-50"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Skorları DB&apos;den Çek
            </button>
            <button
              type="button"
              disabled={busy || activeWeek.missing_count > 0}
              onClick={handlePublish}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#00FF85] text-[#05080D] text-xs font-extrabold disabled:opacity-40"
            >
              Yayınla
            </button>
            <button
              type="button"
              disabled={busy || activeWeek.status === "DRAFT"}
              onClick={handleTelegram}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-cyan-500/15 border border-cyan-500/35 text-cyan-300 text-xs font-bold disabled:opacity-40"
            >
              <Send className="w-3.5 h-3.5" />
              Telegram&apos;a Yayınla
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={handleDelete}
              className="inline-flex items-center gap-1 px-2.5 py-2 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs font-bold ml-auto disabled:opacity-50"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Sil
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
