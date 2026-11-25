import { getConfidenceTone } from "../utils/format";

interface Props {
  score: number;
  size?: number;
}

export function ConfidenceRing({ score, size = 50 }: Props) {
  const tone = getConfidenceTone(score);
  const strokeWidth = 3.5;
  const r = (size - strokeWidth * 2) / 2;
  const circ = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, score));
  const offset = circ - (clamped / 100) * circ;

  return (
    <div
      className="relative inline-flex items-center justify-center shrink-0"
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="#1F2937"
          strokeWidth={strokeWidth}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone.stroke}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.55s ease" }}
        />
      </svg>
      <span className={`absolute text-xs font-bold mono-nums ${tone.text}`}>
        {Math.round(score)}
      </span>
    </div>
  );
}
