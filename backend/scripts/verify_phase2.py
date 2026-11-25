import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, ensure_schema
from app.models import Coupon, Match, NotificationLog, Prediction
from services.ai_summary import generate_match_summary
from services.push import send_push_notification
from services.sync import run_sync_pipeline


def main():
    ensure_schema()
    print("=== FAZ 2 CANLI DOGRULAMA BASLATILIYOR ===")
    summary = run_sync_pipeline(max_external_enrich=20, include_injuries=True)
    print(f"Sync + Analiz Ozeti: {summary}\n")

    db = SessionLocal()
    try:
        # 1. En yuksek guven skorlu ilk 5 oneri (market cesitliligi + >= 1.30 kontrolu)
        all_preds = (
            db.query(Prediction)
            .join(Match, Prediction.match_id == Match.id)
            .filter(Match.status == "SCHEDULED", Prediction.status == "PENDING")
            .order_by(Prediction.confidence_score.desc())
            .all()
        )
        below_130 = [p for p in all_preds if p.iddaa_odd < 1.30]
        print(f"Toplam aktif oneri: {len(all_preds)} | 1.30 alti ihlal sayisi: {len(below_130)}")

        print("\n--- EN YUKSEK GUVEN SKORLU ILK 5 ONERI (DISTINCT MACLAR) ---")
        shown_matches: set[int] = set()
        top_preds: list[Prediction] = []
        for p in all_preds:
            if p.match_id in shown_matches:
                continue
            shown_matches.add(p.match_id)
            top_preds.append(p)
            bd = json.loads(p.score_breakdown_json)
            g_str = f"{p.global_odd:.2f}" if p.global_odd else "N/A"
            print(
                f"[{len(top_preds)}] {p.match.league} | {p.match.home_team} vs {p.match.away_team}\n"
                f"    Secim: {p.market_key} {p.selection_key} | iddaa={p.iddaa_odd:.2f} | Global={g_str} | "
                f"Guven={p.confidence_score:.1f}/100\n"
                f"    4 Bilesen -> Deger:{bd['value']}/35 | OranHar:{bd['odds_move']}/25 | "
                f"FormGol:{bd['form_goals']}/25 | Sakatlik:{bd['injury']}/15 (PiyasaOlasilik:%{bd['true_prob_pct']})"
            )
            if len(top_preds) >= 5:
                break

        # 2. Gunluk otomatik kuponlar (BANKO, DENGELI, SURPRIZ)
        print("\n--- GUNLUK OTOMATIK KUPONLAR (UTC+3) ---")
        coupons = db.query(Coupon).order_by(Coupon.id.desc()).limit(3).all()
        for c in reversed(coupons):
            print(f"[{c.tier}] Tarih: {c.coupon_date} | Toplam Oran: {c.total_odd:.2f} | Ort. Guven: {c.avg_confidence:.1f}")
            for item in c.items:
                pr = item.prediction
                print(
                    f"    - {pr.match.home_team} vs {pr.match.away_team} | "
                    f"{pr.market_key} {pr.selection_key} @ {pr.iddaa_odd:.2f} (Guven: {pr.confidence_score:.1f})"
                )

        # 3. AI / Fallback Ozeti
        if top_preds:
            print("\n--- ORNEK MAC OZETI (GEMINI / FALLBACK) ---")
            m0 = top_preds[0].match
            text, src = generate_match_summary(db, m0)
            print(f"Mac: {m0.home_team} vs {m0.away_team} | Kaynak: {src}")
            print(f"Ozet: {text}")

        # 4. VAPID Push Test Akisi
        print("\n--- VAPID PUSH TEST AKISI ---")
        push_res = send_push_notification(
            db,
            dedupe_key="phase2_verify_push_test",
            title="Iddaa Radar Faz 2 Push Testi",
            body="VAPID bildirim ve dedupe log akisi dogrulandi.",
        )
        print("Push sonucu:", push_res)
        logs_cnt = db.query(NotificationLog).count()
        print("Toplam NotificationLog kaydi:", logs_cnt)
    finally:
        db.close()


if __name__ == "__main__":
    main()
