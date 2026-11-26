export function formatMarketLabel(marketKey: string, selectionKey: string): string {
  const mMap: Record<string, string> = {
    MS: "MS",
    CS: "ÇŞ",
    AU_15: "1.5",
    AU_25: "2.5",
    AU_35: "3.5",
    KG: "KG",
  };
  const sMap: Record<string, string> = {
    UST: "Üst",
    ALT: "Alt",
    VAR: "Var",
    YOK: "Yok",
  };
  const m = mMap[marketKey] || marketKey;
  const s = sMap[selectionKey] || selectionKey;
  return `${m} ${s}`;
}

export function formatPickWithTeam(
  marketKey: string,
  selectionKey: string,
  homeTeam?: string,
  awayTeam?: string
): string {
  if (marketKey === "MS" && selectionKey === "1" && homeTeam) {
    return `${homeTeam} · MS 1`;
  }
  if (marketKey === "MS" && selectionKey === "2" && awayTeam) {
    return `${awayTeam} · MS 2`;
  }
  return formatMarketLabel(marketKey, selectionKey);
}

export function formatTurkishDate(isoDate: string): string {
  if (!isoDate) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate.trim());
  if (!m) return isoDate;
  const dt = new Date(`${m[1]}-${m[2]}-${m[3]}T12:00:00+03:00`);
  if (Number.isNaN(dt.getTime())) return isoDate;
  return new Intl.DateTimeFormat("tr-TR", {
    day: "numeric",
    month: "long",
    weekday: "long",
    timeZone: "Europe/Istanbul",
  }).format(dt);
}

export function getConfidenceTone(score: number) {
  if (score >= 85) {
    return {
      stroke: "#10B981",
      text: "text-emerald-400",
      bg: "bg-emerald-500/15",
      border: "border-emerald-500/30",
      glow: "shadow-[0_0_20px_-6px_rgba(16,185,129,0.45)]",
    };
  }
  if (score >= 75) {
    return {
      stroke: "#06B6D4",
      text: "text-cyan-400",
      bg: "bg-cyan-500/15",
      border: "border-cyan-500/30",
      glow: "shadow-[0_0_18px_-6px_rgba(6,182,212,0.4)]",
    };
  }
  if (score >= 65) {
    return {
      stroke: "#F59E0B",
      text: "text-amber-400",
      bg: "bg-amber-500/15",
      border: "border-amber-500/30",
      glow: "",
    };
  }
  return {
    stroke: "#64748B",
    text: "text-slate-300",
    bg: "bg-slate-700/30",
    border: "border-slate-700",
    glow: "",
  };
}

const BADGE_PALETTES = [
  "from-emerald-500/30 to-teal-600/20 text-emerald-300 border-emerald-500/30",
  "from-cyan-500/30 to-blue-600/20 text-cyan-300 border-cyan-500/30",
  "from-amber-500/30 to-orange-600/20 text-amber-300 border-amber-500/30",
  "from-rose-500/30 to-pink-600/20 text-rose-300 border-rose-500/30",
  "from-indigo-500/30 to-violet-600/20 text-indigo-300 border-indigo-500/30",
];

export function getTeamMonogram(name: string) {
  const clean = (name || "?").trim();
  const words = clean.split(/\s+/).filter(Boolean);
  const initials =
    words.length >= 2
      ? `${words[0][0]}${words[1][0]}`.toUpperCase()
      : clean.slice(0, 2).toUpperCase();
  let hash = 0;
  for (let i = 0; i < clean.length; i++) {
    hash = clean.charCodeAt(i) + ((hash << 5) - hash);
  }
  const palette = BADGE_PALETTES[Math.abs(hash) % BADGE_PALETTES.length];
  return { initials, palette };
}

export function formatTurkishPossessive(n: number): string {
  const abs = Math.abs(Math.trunc(n));
  const last = abs % 10;
  if (last === 1) return `${n}'i`;
  if (last === 2) return `${n}'si`;
  if (last === 3 || last === 4) return `${n}'ü`;
  if (last === 5 || last === 8) return `${n}'i`;
  if (last === 6) return `${n}'sı`;
  if (last === 7) return `${n}'si`;
  if (last === 9) return `${n}'u`;
  if (abs === 0) return `${n}'ı`;
  const lastTwo = abs % 100;
  if (lastTwo === 10 || lastTwo === 30) return `${n}'u`;
  if (lastTwo === 20 || lastTwo === 50) return `${n}'si`;
  if (lastTwo === 40 || lastTwo === 60 || lastTwo === 90) return `${n}'ı`;
  if (lastTwo === 70 || lastTwo === 80) return `${n}'i`;
  return `${n}'ü`;
}
