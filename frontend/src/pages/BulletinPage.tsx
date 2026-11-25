import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { api } from "../api/client";
import { LeagueBadge, TeamBadge } from "../components/TeamBadge";
import type { BulletinLeague } from "../types";

const SELECTION_ORDER: Record<string, number> = {
  "1": 1,
  X: 2,
  "2": 3,
  "1X": 1,
  "12": 2,
  X2: 3,
  ALT: 1,
  UST: 2,
  VAR: 1,
  YOK: 2,
};

interface Props {
  onSelectMatch: (matchId: number) => void;
  refreshTrigger: number;
}

const MARKETS = [
  { key: "MS", label: "Maç Sonucu" },
  { key: "CS", label: "Çifte Şans" },
  { key: "AU_25", label: "2.5 Alt/Üst" },
  { key: "KG", label: "KG Var/Yok" },
];

export function BulletinPage({ onSelectMatch, refreshTrigger }: Props) {
  const [market, setMarket] = useState("MS");
  const [todayOnly, setTodayOnly] = useState(false);
  const [majorOnly, setMajorOnly] = useState(false);
  const [minOdd, setMinOdd] = useState(1.01);
  const [search, setSearch] = useState("");
  const [leagues, setLeagues] = useState<BulletinLeague[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .getBulletin({ todayOnly, majorOnly, minOdd, market, search })
      .then((res) => setLeagues(res.leagues))
      .finally(() => setLoading(false));
  }, [todayOnly, majorOnly, minOdd, market, search, refreshTrigger]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {MARKETS.map((m) => (
          <button
            key={m.key}
            onClick={() => setMarket(m.key)}
            className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
              market === m.key
                ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/40"
                : "bg-[#131926] text-slate-400 border-[#1F2937]"
            }`}
          >
            {m.label}
          </button>
        ))}

        <button
          onClick={() => setTodayOnly(!todayOnly)}
          className={`px-3 py-1.5 rounded-full text-xs font-semibold border ${
            todayOnly
              ? "bg-cyan-500/15 text-cyan-400 border-cyan-500/40"
              : "bg-[#131926] text-slate-400 border-[#1F2937]"
          }`}
        >
          Bugün (24s)
        </button>

        <button
          onClick={() => setMajorOnly(!majorOnly)}
          className={`px-3 py-1.5 rounded-full text-xs font-semibold border ${
            majorOnly
              ? "bg-cyan-500/15 text-cyan-400 border-cyan-500/40"
              : "bg-[#131926] text-slate-400 border-[#1F2937]"
          }`}
        >
          Büyük Ligler
        </button>

        <select
          value={minOdd}
          onChange={(e) => setMinOdd(Number(e.target.value))}
          className="px-3 py-1.5 rounded-full text-xs font-semibold bg-[#131926] text-slate-200 border border-[#1F2937] outline-none"
        >
          <option value={1.01}>Tüm Oranlar (1.01+)</option>
          <option value={1.3}>Min 1.30 Oran</option>
          <option value={1.6}>Min 1.60 Oran</option>
        </select>
      </div>

      <div className="relative">
        <Search className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Bültende takım veya lig ara..."
          className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-[#131926] border border-[#1F2937] text-xs text-slate-100 placeholder:text-slate-500 outline-none"
        />
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((n) => (
            <div key={n} className="h-28 rounded-2xl bg-[#131926] border border-[#1F2937] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {leagues.map((lg) => (
            <div
              key={lg.league}
              className="rounded-2xl bg-[#131926] border border-[#1F2937] overflow-hidden"
            >
              <div className="px-4 py-2.5 bg-[#090D16]/80 border-b border-[#1F2937] flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <LeagueBadge name={lg.league} logoUrl={lg.matches[0]?.league_logo_url} />
                  <span className="text-xs font-bold text-slate-200 truncate">{lg.league}</span>
                </div>
                <span className="text-[11px] text-slate-500 mono-nums shrink-0">{lg.matches.length} maç</span>
              </div>

              <div className="divide-y divide-[#1F2937]">
                {lg.matches.map((m) => {
                  const sortedOdds = [...m.odds].sort(
                    (a, b) =>
                      (SELECTION_ORDER[a.selection_key] ?? 99) -
                      (SELECTION_ORDER[b.selection_key] ?? 99)
                  );
                  return (
                  <div
                    key={m.match_id}
                    onClick={() => onSelectMatch(m.match_id)}
                    className="cursor-pointer px-4 py-3 hover:bg-slate-800/30 flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[11px] text-slate-400 mono-nums">
                        <span>
                          {m.start_date_tr} {m.start_time_tr}
                        </span>
                        {m.status === "LIVE" && (
                          <span className="px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-400 text-[10px] font-bold">
                            CANLI {m.home_score ?? 0}-{m.away_score ?? 0}
                          </span>
                        )}
                        {m.top_confidence && (
                          <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-[10px] font-semibold">
                            Güven {Math.round(m.top_confidence)}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 mt-1 min-w-0">
                        <TeamBadge name={m.home_team} logoUrl={m.home_logo_url} size="sm" />
                        <span className="text-xs font-semibold text-slate-100 truncate">
                          {m.home_team} - {m.away_team}
                        </span>
                        <TeamBadge name={m.away_team} logoUrl={m.away_logo_url} size="sm" />
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      {sortedOdds.map((o) => (
                        <div
                          key={o.selection_key}
                          className="px-2.5 py-1.5 rounded-lg bg-[#090D16] border border-[#1F2937] text-center min-w-[46px]"
                        >
                          <span className="block text-[9px] text-slate-500">{o.selection_key}</span>
                          <span className="text-xs font-bold mono-nums text-slate-100">
                            {o.iddaa_odd.toFixed(2)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
