import { Calculator } from "lucide-react";

const PRESET_STAKES = [100, 250, 500];

interface Props {
  totalOdd: number;
  stake: number;
  onChangeStake: (next: number) => void;
  compact?: boolean;
}

export function StakeCalculator({ totalOdd, stake, onChangeStake, compact = false }: Props) {
  const safeStake = Number.isFinite(stake) && stake > 0 ? stake : 0;
  const payout = safeStake * totalOdd;
  const formattedPayout = payout.toLocaleString("tr-TR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  return (
    <div
      className={`rounded-xl bg-[#05080D]/75 border border-white/10 ${
        compact ? "px-3 py-2" : "px-3.5 py-2.5"
      } flex flex-wrap items-center justify-between gap-2 text-xs`}
    >
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-400 mr-0.5">
          <Calculator className="w-3.5 h-3.5 text-emerald-400" />
          Tutar:
        </span>
        {PRESET_STAKES.map((preset) => {
          const active = safeStake === preset;
          return (
            <button
              key={preset}
              type="button"
              onClick={() => onChangeStake(preset)}
              className={`px-2 py-1 rounded-lg text-[11px] font-bold mono-nums transition-colors ${
                active
                  ? "bg-[#00FF85] text-[#05080D]"
                  : "bg-white/[0.05] text-slate-300 hover:text-white border border-white/10"
              }`}
            >
              {preset} ₺
            </button>
          );
        })}
        <div className="relative flex items-center">
          <input
            type="number"
            min={1}
            step={10}
            value={safeStake || ""}
            onChange={(e) => {
              const val = Number(e.target.value);
              onChangeStake(Number.isFinite(val) && val > 0 ? Math.min(val, 1_000_000) : 0);
            }}
            placeholder="Özel"
            aria-label="Özel tutar (TL)"
            className="w-16 pl-2 pr-4 py-1 rounded-lg bg-[#0B111C] border border-white/15 focus:border-[#00FF85]/60 outline-none text-[11px] font-bold text-white mono-nums"
          />
          <span className="text-[10px] text-slate-400 font-bold -ml-3.5 pointer-events-none">₺</span>
        </div>
      </div>

      <div className="flex items-center gap-1.5 ml-auto">
        <span className="text-[11px] text-slate-400 font-medium">Tahmini Kazanç:</span>
        <span className="text-xs sm:text-sm font-extrabold text-[#00FF85] mono-nums">
          {formattedPayout} ₺
        </span>
      </div>
    </div>
  );
}
