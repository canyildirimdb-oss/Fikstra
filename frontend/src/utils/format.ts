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
