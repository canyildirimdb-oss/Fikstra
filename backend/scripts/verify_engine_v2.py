import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, ensure_schema
from app.models import Coupon, Match, MatchContext, Prediction
from services.sync import run_sync_pipeline


def main():
    ensure_schema()
    print("=== MOTOR v2 CANLI SYNC & DOGRULAMA ===")
    summary = run_sync_pipeline(max_external_enrich=40, max_tm_fresh=16, include_injuries=True)
    print(f"Sync Ozeti: {summary}\n")

    db = SessionLocal()
    try:
        # 1. Check 4.00+ odd cap & Braga vs Kuopion
        high_odd_55 = (
            db.query(Prediction)
            .filter(Prediction.iddaa_odd >= 4.00, Prediction.confidence_score >= 55.0)
            .count()
        )
        print(f"4.00+ oranli ve 55+ puanli oneri sayisi: {high_odd_55}")

        braga = db.query(Match).filter(Match.home_team.ilike("%Braga%")).first()
        if braga:
            braga_top = (
                db.query(Prediction)
                .filter(Prediction.match_id == braga.id, Prediction.iddaa_odd < 4.00)
                .order_by(Prediction.confidence_score.desc())
                .first()
            )
            if braga_top:
                print(
                    f"Braga maci en iyi tercih: {braga.home_team} vs {braga.away_team} -> "
                    f"{braga_top.market_key} {braga_top.selection_key} @ {braga_top.iddaa_odd:.2f} "
                    f"(Guven: {braga_top.confidence_score:.1f})"
                )

        # 2. Check U23 Next Gen major league exclusion
        u23_major = (
            db.query(Match)
            .filter(Match.league.ilike("%U23%"), Match.is_major_league.is_(True))
            .count()
        )
        print(f"U23 / Next Gen olup is_major_league=True kalan mac sayisi: {u23_major}")

        # 3. Check UEFA Europa & Conference League enrichment
        uefa_matches = (
            db.query(Match)
            .filter(
                (Match.league.ilike("%Avrupa Ligi%")) | (Match.league.ilike("%Konferans Ligi%"))
            )
            .all()
        )
        uefa_with_form = 0
        for um in uefa_matches:
            if um.context and (um.context.home_form or um.context.away_form):
                uefa_with_form += 1
        print(f"UEFA Avrupa/Konferans Ligi: {uefa_with_form}/{len(uefa_matches)} mac gercek form + Poisson verisine sahip")

        # 4. Top 5 highest-confidence picks (<4.00 odd)
        print("\n--- MOTOR v2 EN YUKSEK GUVEN SKORLU ILK 5 ONERI ---")
        all_preds = (
            db.query(Prediction)
            .join(Match, Prediction.match_id == Match.id)
            .filter(
                Match.status == "SCHEDULED",
                Prediction.status == "PENDING",
                Prediction.iddaa_odd >= 1.30,
                Prediction.iddaa_odd < 4.00,
            )
            .order_by(Prediction.confidence_score.desc())
            .all()
        )
        seen = set()
        shown = 0
        for p in all_preds:
            if p.match_id in seen:
                continue
            seen.add(p.match_id)
            shown += 1
            bd = json.loads(p.score_breakdown_json)
            g_str = f"{p.global_odd:.2f}" if p.global_odd else "N/A"
            print(
                f"[{shown}] {p.match.league} | {p.match.home_team} vs {p.match.away_team}\n"
                f"    Secim: {p.market_key} {p.selection_key} @ {p.iddaa_odd:.2f} (Global={g_str}) | Guven={p.confidence_score:.1f}/100\n"
                f"    4 Bilesen -> Deger:{bd['value']}/35 | OranHar:{bd['odds_move']}/25 | "
                f"FormPoisson:{bd['form_goals']}/25 | Sakatlik:{bd['injury']}/15 | "
                f"PoissonOlasilik:%{bd.get('poisson_prob_pct')} (Edge:%{bd.get('poisson_edge_pct')})"
            )
            if shown >= 5:
                break

        # 5. Updated Daily Coupons
        print("\n--- GUNCELLENMIS GUNLUK KUPONLAR ---")
        coupons = db.query(Coupon).order_by(Coupon.coupon_date.desc(), Coupon.id.asc()).limit(3).all()
        for c in coupons:
            print(f"[{c.tier}] Tarih: {c.coupon_date} | Toplam Oran: {c.total_odd:.2f} | Ort. Guven: {c.avg_confidence:.1f}")
            for it in c.items:
                pr = it.prediction
                print(
                    f"    - {pr.match.home_team} vs {pr.match.away_team} | "
                    f"{pr.market_key} {pr.selection_key} @ {pr.iddaa_odd:.2f} (Guven: {pr.confidence_score:.1f})"
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
