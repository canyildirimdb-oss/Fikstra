import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Match, MatchContext, Prediction

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
]
_LAST_GEMINI_CALL_TS = 0.0
_MIN_INTERVAL_SEC = 4.2  # Free tier friendly (~14 RPM max)

MARKET_LABELS = {
    "MS": "Maç Sonucu",
    "CS": "Çifte Şans",
    "AU_15": "1.5 Gol",
    "AU_25": "2.5 Gol",
    "AU_35": "3.5 Gol",
    "KG": "Karşılıklı Gol",
}


def _build_rule_based_summary(
    match: Match,
    top_pred: Optional[Prediction],
    ctx: Optional[MatchContext],
) -> str:
    parts: list[str] = []

    if top_pred:
        m_lbl = MARKET_LABELS.get(top_pred.market_key, top_pred.market_key)
        base = (
            f"{match.home_team} - {match.away_team} mücadelesinde sistemin öne çıkardığı tercih "
            f"{m_lbl} {top_pred.selection_key} ({top_pred.iddaa_odd:.2f} oran, "
            f"%{top_pred.confidence_score:.0f} güven)."
        )
        if top_pred.global_odd:
            diff = ((top_pred.iddaa_odd / top_pred.global_odd) - 1.0) * 100.0
            if diff >= 0:
                base += f" iddaa oranı global ortalamanın ({top_pred.global_odd:.2f}) %{diff:.1f} üzerinde değer sunuyor."
            else:
                base += f" Global piyasa ortalaması {top_pred.global_odd:.2f} seviyesinde."
        if top_pred.score_breakdown_json:
            try:
                bd = json.loads(top_pred.score_breakdown_json)
                prob_pct = bd.get("blended_prob_pct") or bd.get("true_prob_pct")
                if prob_pct is not None:
                    base += f" Model olasılığı %{float(prob_pct):.0f} olarak hesaplandı."
            except Exception:
                pass
        if top_pred.odds_drop_pct is not None and top_pred.odds_drop_pct >= 2.0:
            base += f" Açılış oranına göre %{top_pred.odds_drop_pct:.1f} oran düşüşü dikkat çekiyor."
        parts.append(base)

    if ctx and (ctx.home_form or ctx.away_form):
        hf = "".join(json.loads(ctx.home_form)) if ctx.home_form else "Veri yok"
        af = "".join(json.loads(ctx.away_form)) if ctx.away_form else "Veri yok"
        h_g = f"{ctx.home_goals_avg:.1f}" if ctx.home_goals_avg is not None else "-"
        a_g = f"{ctx.away_goals_avg:.1f}" if ctx.away_goals_avg is not None else "-"
        parts.append(
            f"Son 5 maç formunda ev sahibi {hf} ({h_g} gol ort.), deplasman ekibi {af} ({a_g} gol ort.) grafiğine sahip."
        )

    if ctx and ctx.injuries_json:
        inj = json.loads(ctx.injuries_json)
        h_inj = inj.get("home") or []
        a_inj = inj.get("away") or []
        if h_inj or a_inj:
            h_names = ", ".join(x["player"] for x in h_inj[:2]) or "yok"
            a_names = ", ".join(x["player"] for x in a_inj[:2]) or "yok"
            parts.append(
                f"Kadroda eksik listesi: {match.home_team} ({len(h_inj)} eksik: {h_names}), "
                f"{match.away_team} ({len(a_inj)} eksik: {a_names})."
            )

    if not parts:
        parts.append(
            f"{match.league} kapsamındaki {match.home_team} - {match.away_team} maçı için güncel oran hareketleri takip ediliyor."
        )

    return " ".join(parts)


def generate_match_summary(
    db: Session,
    match: Match,
    force_refresh: bool = False,
) -> tuple[str, str]:
    """
    Return (summary_text, source) using DB cache first, then lightweight Gemini REST API,
    falling back seamlessly to rule-based Turkish summary if key is absent or quota hits.
    """
    global _LAST_GEMINI_CALL_TS

    ctx = db.query(MatchContext).filter(MatchContext.match_id == match.id).first()
    if not ctx:
        ctx = MatchContext(match_id=match.id)
        db.add(ctx)
        db.flush()

    top_pred = (
        db.query(Prediction)
        .filter(Prediction.match_id == match.id, Prediction.status == "PENDING")
        .order_by(Prediction.confidence_score.desc())
        .first()
    )
    current_pick = f"{top_pred.market_key}:{top_pred.selection_key}" if top_pred else None

    if ctx.ai_summary and not force_refresh and ctx.ai_summary_pick == current_pick:
        return ctx.ai_summary, ctx.ai_source or "cached"

    api_key = (settings.gemini_api_key or "").strip()
    gemini_failed = False
    if api_key:
        elapsed = time.monotonic() - _LAST_GEMINI_CALL_TS
        if elapsed < _MIN_INTERVAL_SEC:
            time.sleep(_MIN_INTERVAL_SEC - elapsed)

        hf = json.loads(ctx.home_form) if ctx.home_form else []
        af = json.loads(ctx.away_form) if ctx.away_form else []
        inj = json.loads(ctx.injuries_json) if ctx.injuries_json else {}

        prompt = (
            "Sen uzman bir futbol veri analistisin. Yalnızca aşağıdaki GERÇEK verilere dayanarak "
            "3-4 cümlelik net, klişesiz, akıcı Türkçe maç analizi yaz. Veri uydurma.\n"
            f"Maç: {match.home_team} vs {match.away_team} ({match.league})\n"
            f"Öne Çıkan Seçim: {top_pred.market_key if top_pred else '-'} {top_pred.selection_key if top_pred else '-'} "
            f"| iddaa Oranı: {top_pred.iddaa_odd if top_pred else '-'} | Global Oran: {top_pred.global_odd if top_pred else '-'} "
            f"| Güven Skoru: {top_pred.confidence_score if top_pred else '-'}/100\n"
            f"Ev Son 5 Form: {''.join(hf) or 'Yok'} (Gol Ort: {ctx.home_goals_avg})\n"
            f"Dep Son 5 Form: {''.join(af) or 'Yok'} (Gol Ort: {ctx.away_goals_avg})\n"
            f"Ev Eksikler: {[x['player'] for x in (inj.get('home') or [])[:4]]}\n"
            f"Dep Eksikler: {[x['player'] for x in (inj.get('away') or [])[:4]]}\n"
        )

        for model_name in GEMINI_MODELS:
            try:
                _LAST_GEMINI_CALL_TS = time.monotonic()
                endpoint = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model_name}:generateContent?key={api_key}"
                )
                resp = httpx.post(
                    endpoint,
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 450},
                    },
                    timeout=18.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                        .strip()
                    )
                    if text:
                        ctx.ai_summary = text
                        ctx.ai_summary_pick = current_pick
                        ctx.ai_source = "gemini"
                        ctx.ai_generated_at = datetime.now(timezone.utc)
                        db.commit()
                        return text, "gemini"
                else:
                    logger.warning(
                        "[Fikstra AI] Gemini HTTP %s on model %s: %s",
                        resp.status_code,
                        model_name,
                        resp.text[:200],
                    )
                gemini_failed = True
            except Exception as exc:
                logger.warning("[Fikstra AI] Gemini exception on model %s: %s", model_name, exc)
                gemini_failed = True
                continue

    if gemini_failed:
        # Never cache transient 429 / 503 / timeout failures in DB; retry on next open
        fallback_text = _build_rule_based_summary(match, top_pred, ctx)
        return fallback_text, "transient_error"

    fallback_text = _build_rule_based_summary(match, top_pred, ctx)
    ctx.ai_summary = fallback_text
    ctx.ai_summary_pick = current_pick
    ctx.ai_source = "rule_based"
    ctx.ai_generated_at = datetime.now(timezone.utc)
    db.commit()
    return fallback_text, "rule_based"
