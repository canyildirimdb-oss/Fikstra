import { useState } from "react";
import { getTeamMonogram } from "../utils/format";
import { resolveLeagueVisual } from "../utils/highlightsFilter";

export function TeamBadge({
  name,
  logoUrl,
  size = "sm",
}: {
  name: string;
  logoUrl?: string | null;
  size?: "sm" | "md" | "lg";
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const { initials, palette } = getTeamMonogram(name);
  const dim =
    size === "lg"
      ? "w-10 h-10 text-sm"
      : size === "md"
      ? "w-8 h-8 text-xs"
      : "w-6 h-6 text-[10px]";

  if (logoUrl && !imgFailed) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-full bg-[#090D16] border border-[#1F2937] p-0.5 shrink-0 overflow-hidden ${dim}`}
      >
        <img
          src={logoUrl}
          alt={name}
          loading="lazy"
          onError={() => setImgFailed(true)}
          className="w-full h-full object-contain"
        />
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full bg-gradient-to-br border font-bold shrink-0 ${dim} ${palette}`}
    >
      {initials}
    </span>
  );
}

export function LeagueBadge({
  name,
  logoUrl,
  country,
}: {
  name: string;
  logoUrl?: string | null;
  country?: string | null;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const visual = resolveLeagueVisual(name, country, logoUrl);
  if (visual.logoUrl && !imgFailed) {
    return (
      <span className="inline-flex items-center justify-center w-5 h-5 rounded-md bg-white/[0.12] border border-white/15 p-0.5 shrink-0">
        <img
          src={visual.logoUrl}
          alt={name}
          loading="lazy"
          onError={() => setImgFailed(true)}
          className="w-full h-full object-contain drop-shadow-[0_1px_2px_rgba(255,255,255,0.25)]"
        />
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center justify-center w-5 h-5 rounded-md bg-gradient-to-br border text-[8px] font-extrabold shrink-0 ${visual.colorCls}`}
      title={name}
    >
      {visual.abbr.slice(0, 3)}
    </span>
  );
}

export function LeagueLogoColumn({
  name,
  logoUrl,
  country,
}: {
  name: string;
  logoUrl?: string | null;
  country?: string | null;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const visual = resolveLeagueVisual(name, country, logoUrl);

  return (
    <div
      className="w-12 shrink-0 flex flex-col items-center justify-center gap-1.5 bg-gradient-to-b from-white/[0.04] via-emerald-500/[0.04] to-white/[0.02] border-r border-white/[0.07] px-1.5 py-3"
      title={name}
    >
      {visual.logoUrl && !imgFailed ? (
        <div className="w-9 h-9 rounded-xl bg-white/[0.13] border border-white/20 shadow-[0_0_14px_-4px_rgba(255,255,255,0.28)] flex items-center justify-center p-1.5">
          <img
            src={visual.logoUrl}
            alt={name}
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="w-full h-full object-contain drop-shadow-[0_1px_4px_rgba(255,255,255,0.3)]"
          />
        </div>
      ) : (
        <div
          className={`w-9 h-9 rounded-xl bg-gradient-to-br border flex items-center justify-center text-[10px] font-extrabold tracking-tight shadow-inner ${visual.colorCls}`}
        >
          {visual.abbr}
        </div>
      )}
      <span className="text-[8.5px] font-bold tracking-wider text-slate-400 uppercase leading-none">
        {visual.abbr}
      </span>
    </div>
  );
}

export function FormStrip({
  form,
  variant = "dots",
}: {
  form: string[];
  variant?: "dots" | "boxes";
}) {
  if (!form || form.length === 0) {
    return <span className="text-[10px] text-slate-500">—</span>;
  }
  if (variant === "dots") {
    const dotMap: Record<string, string> = {
      W: "bg-[#00FF85]",
      D: "bg-amber-400",
      L: "bg-rose-500",
    };
    return (
      <div
        className="inline-flex items-center gap-1 shrink-0"
        aria-label="Son 5 maç formu (soldan sağa en yeni)"
        title="Soldan sağa: en yeni → en eski"
      >
        {form.slice(0, 5).map((item, idx) => (
          <span
            key={idx}
            className={`w-[7px] h-[7px] rounded-full ${dotMap[item] || "bg-slate-600"}`}
            title={item === "W" ? "Galibiyet" : item === "D" ? "Beraberlik" : "Mağlubiyet"}
          />
        ))}
      </div>
    );
  }
  const map: Record<string, { label: string; cls: string }> = {
    W: { label: "G", cls: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" },
    D: { label: "B", cls: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
    L: { label: "M", cls: "bg-rose-500/20 text-rose-400 border-rose-500/30" },
  };
  return (
    <div
      className="inline-flex items-center gap-0.5"
      aria-label="Son 5 maç formu (soldan sağa en yeni)"
      title="Soldan sağa: en yeni → en eski"
    >
      {form.slice(0, 5).map((item, idx) => {
        const cfg = map[item] || { label: item, cls: "bg-slate-800 text-slate-400 border-slate-700" };
        return (
          <span
            key={idx}
            className={`w-4 h-4 rounded-[4px] border text-[9px] font-bold flex items-center justify-center ${cfg.cls}`}
          >
            {cfg.label}
          </span>
        );
      })}
    </div>
  );
}
