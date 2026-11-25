import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, SessionLocal, engine
from app.models import Match, MatchContext, OddsCurrent
from services.sync import run_sync_pipeline


def main():
    Base.metadata.create_all(bind=engine)
    print("=== FAZ 1 CANLI VERI DOGRULAMA BASLATILIYOR ===")
    summary = run_sync_pipeline(max_external_enrich=20, include_injuries=True)
    print(f"Sync Ozeti: {summary}\n")

    db = SessionLocal()
    try:
        matches = (
            db.query(Match)
            .filter(Match.external_source == "espn")
            .order_by(Match.is_major_league.desc(), Match.start_time_utc.asc())
            .all()
        )

        shown = 0
        for m in matches:
            odds = db.query(OddsCurrent).filter(OddsCurrent.match_id == m.id).all()
            ms_odds = {o.selection_key: o for o in odds if o.market_key == "MS"}
            if not ms_odds:
                continue
            has_global = any(o.global_avg_odd is not None for o in ms_odds.values())
            if not has_global:
                continue

            ctx = db.query(MatchContext).filter(MatchContext.match_id == m.id).first()
            shown += 1
            print(f"[{shown}] {m.league} | {m.home_team} vs {m.away_team} | UTC: {m.start_time_utc.strftime('%Y-%m-%d %H:%M')}")
            print(f"    Buyuk Lig: {m.is_major_league} | Veri Durumu: {m.data_status}")

            ms_parts = []
            for sel in ("1", "X", "2"):
                o = ms_odds.get(sel)
                if o:
                    g = f"{o.global_avg_odd:.2f}" if o.global_avg_odd else "N/A"
                    ms_parts.append(f"MS {sel}: iddaa={o.iddaa_odd:.2f} vs Global={g}")
            print("    " + " | ".join(ms_parts))

            if ctx:
                hf = json.loads(ctx.home_form) if ctx.home_form else []
                af = json.loads(ctx.away_form) if ctx.away_form else []
                inj = json.loads(ctx.injuries_json) if ctx.injuries_json else {}
                print(
                    f"    Form (Son 5): Ev={''.join(hf) or 'N/A'} (GolOrt: {ctx.home_goals_avg}) | "
                    f"Dep={''.join(af) or 'N/A'} (GolOrt: {ctx.away_goals_avg})"
                )
                h_inj = [f"{x['player']} ({x['reason']})" for x in (inj.get("home") or [])[:3]]
                a_inj = [f"{x['player']} ({x['reason']})" for x in (inj.get("away") or [])[:3]]
                print(f"    Sakat/Cezali: Ev={h_inj or 'Yok/Bulunamadi'} | Dep={a_inj or 'Yok/Bulunamadi'}")
            print("-" * 80)
            if shown >= 8:
                break

        print(f"\nToplam gosterilen eslesmis canli mac sayisi: {shown}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
