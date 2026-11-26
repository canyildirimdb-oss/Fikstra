import { useState } from "react";
import { getTeamMonogram } from "../utils/format";

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
}: {
  name: string;
  logoUrl?: string | null;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  if (!logoUrl || imgFailed) return null;
  return (
    <img
      src={logoUrl}
      alt={name}
      loading="lazy"
      onError={() => setImgFailed(true)}
      className="w-4 h-4 object-contain shrink-0 rounded-sm bg-white/5 p-0.5"
    />
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
