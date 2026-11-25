interface Props {
  score: number;
  size?: number;
}

export function ConfidenceRing({ score, size = 54 }: Props) {
  const strokeWidth = 3.5;
  const r = (size - strokeWidth * 2) / 2;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, score));
  const offset = circ - (clamped / 100) * circ;

  const isHigh = clamped >= 80;
  const isMid = clamped >= 70 && clamped < 80;
  const stroke = isHigh ? "#00FF85" : isMid ? "#10B981" : "#34D399";
  const strokeOpacity = isHigh || isMid ? 1 : 0.6;
  const textCls = isHigh
    ? "text-[#00FF85]"
    : isMid
    ? "text-emerald-400"
    : "text-emerald-300/75";

  return (
    <div
      className="relative inline-flex items-center justify-center shrink-0"
      style={{
        width: size,
        height: size,
        filter: isHigh ? "drop-shadow(0 0 8px rgba(0, 255, 133, 0.4))" : undefined,
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
          stroke={stroke}
          strokeOpacity={strokeOpacity}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.55s ease" }}
        />
      </svg>
      <span className={`absolute text-sm font-extrabold mono-nums ${textCls}`}>
        {Math.round(score)}
      </span>
    </div>
  );
}
