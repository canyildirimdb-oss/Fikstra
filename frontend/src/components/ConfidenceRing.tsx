import { getConfidenceTier } from "../utils/highlightsFilter";

interface Props {
  score: number;
  size?: number;
  showTier?: boolean;
}

export function ConfidenceRing({ score, size = 56, showTier = true }: Props) {
  const strokeWidth = 3.5;
  const r = (size - strokeWidth * 2) / 2;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, score));
  const offset = circ - (clamped / 100) * circ;
  const tier = getConfidenceTier(clamped);

  return (
    <div className="inline-flex flex-col items-center gap-1 shrink-0">
      <div
        className="relative inline-flex items-center justify-center shrink-0"
        style={{
          width: size,
          height: size,
          filter: tier.glow,
        }}
      >
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke="rgba(255, 255, 255, 0.08)"
            strokeWidth={strokeWidth}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={tier.stroke}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circ}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 0.55s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
          <span className={`text-sm font-extrabold mono-nums ${tier.textCls}`}>
            {Math.round(score)}
          </span>
          <span className="text-[7px] font-bold tracking-wider text-slate-400 uppercase mt-0.5">
            GÜVEN
          </span>
        </div>
      </div>
      {showTier && (
        <span
          className={`px-1.5 py-[1px] rounded-full border text-[8.5px] font-extrabold tracking-wider leading-tight ${tier.badgeCls}`}
        >
          {tier.label}
        </span>
      )}
    </div>
  );
}
