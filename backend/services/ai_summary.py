import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Match, MatchContext, Prediction
from utils.cache import get_cached_json

logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash",
]
_GEMINI_LOCK = threading.Lock()
_LAST_GEMINI_CALL_TS = 0.0
_GEMINI_COOLDOWN_UNTIL = 0.0
_GEMINI_COOLDOWN_SEC = 600.0  # 10-minute circuit breaker after 429/503
_MIN_INTERVAL_SEC = 4.2  # Free tier friendly (~14 RPM max)


def is_gemini_circuit_open() -> bool:
    """Return True when the Gemini 429/503 circuit breaker cooldown is currently active."""
    return time.monotonic() < _GEMINI_COOLDOWN_UNTIL

MARKET_LABELS = {
    "MS": "Maç Sonucu",
    "CS": "Çifte Şans",
    "AU_15": "1.5 Gol",
    "AU_25": "2.5 Gol",
    "AU_35": "3.5 Gol",
    "KG": "Karşılıklı Gol",
}


_FORM_TR_MAP = {"W": "G", "D": "B", "L": "M"}


def form_to_turkish(form_items: list[str] | tuple[str, ...] | None) -> str:
    if not form_items:
        return ""
    return "".join(_FORM_TR_MAP.get(str(ch).strip().upper(), str(ch).strip().upper()) for ch in form_items if str(ch).strip())


def sanitize_turkish_summary(text: str, injuries_json: Optional[str] = None) -> str:
    """Normalize English form letters (W/D/L -> G/B/M), remove duplicate 'rağmen', and fix injury contradictions."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    # Replace standalone 3-5 char W/D/L form codes like LDLLW, WLDWW, W/D/L
    def _repl_form(match: re.Match[str]) -> str:
        tok = match.group(0)
        return "".join(_FORM_TR_MAP.get(c, c) for c in tok)

    cleaned = re.sub(r"\b[WDL]{3,5}\b", _repl_form, cleaned)
    cleaned = re.sub(r"\b[WDL]/[WDL]/[WDL]\b", _repl_form, cleaned)

    # Fix duplicate "rağmen ... rağmen" in the same sentence
    cleaned = re.sub(r"\brağmen\b([^.!?]*?)\brağmen\b", r"rağmen\1karşın", cleaned, flags=re.IGNORECASE)

    # Fix contradiction when both teams actually have 0 recorded injuries
    if injuries_json:
        try:
            inj = json.loads(injuries_json) if isinstance(injuries_json, str) else injuries_json
            h_cnt = len(inj.get("home") or [])
            a_cnt = len(inj.get("away") or [])
            if h_cnt == 0 and a_cnt == 0:
                cleaned = re.sub(
                    r"(kadrosundaki\s+)?önemli\s+eksiklere\s+rağmen",
                    "tam kadro yapısıyla",
                    cleaned,
                    flags=re.IGNORECASE,
                )
                cleaned = re.sub(
                    r"sakat\s+ve\s+cezalı\s+eksiklere\s+rağmen",
                    "sağlam kadro yapısıyla",
                    cleaned,
                    flags=re.IGNORECASE,
                )
        except Exception:
            pass

    return cleaned


def _compute_exact_5m_goal_avg(
    db: Optional[Session],
    match_id: int,
    side: str,
) -> tuple[Optional[float], int]:
    """Return (avg_goals, sample_count) from recent_v4:<match_id> if available."""
    if db is None:
        return None, 0
    try:
        rm = get_cached_json(db, f"recent_v4:{match_id}", ttl_hours=24.0)
        if not isinstance(rm, dict):
            return None, 0
        rows = (rm.get(side) or [])[:5]
        if not rows:
            return None, 0
        total = 0
        cnt = 0
        for r in rows:
            gf = r.get("home_score") if r.get("is_home") else r.get("away_score")
            if gf is not None:
                total += int(gf)
                cnt += 1
        return (round(total / cnt, 2) if cnt > 0 else None), cnt
    except Exception:
        return None, 0


def _finalize_ai_summary(text: str) -> str:
    """Ensure Gemini summary never ends mid-sentence if output was truncated."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    if cleaned[-1] in ".!?…\"”":
        return cleaned
    last_end = max(cleaned.rfind("."), cleaned.rfind("!"), cleaned.rfind("?"))
    if last_end >= max(30, int(len(cleaned) * 0.4)):
        return cleaned[: last_end + 1].strip()
    return f"{cleaned}."


def _build_rule_based_summary(
    match: Match,
    top_pred: Optional[Prediction],
    ctx: Optional[MatchContext],
    db: Optional[Session] = None,
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
                base += f" İddaa oranı global ortalamanın ({top_pred.global_odd:.2f}) %{diff:.1f} üzerinde değer sunuyor."
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
        hf = form_to_turkish(json.loads(ctx.home_form)) if ctx.home_form else "Veri yok"
        af = form_to_turkish(json.loads(ctx.away_form)) if ctx.away_form else "Veri yok"
        h5_avg, h5_cnt = _compute_exact_5m_goal_avg(db, match.id, "home")
        a5_avg, a5_cnt = _compute_exact_5m_goal_avg(db, match.id, "away")
        if h5_avg is not None and a5_avg is not None and h5_cnt > 0 and a5_cnt > 0:
            parts.append(
                f"Son {h5_cnt} maç formunda ev sahibi {hf} ({h5_avg:.1f} gol ort.), "
                f"deplasman ekibi {af} ({a5_avg:.1f} gol ort.) grafiğine sahip."
            )
        else:
            h_g = f"{ctx.home_goals_avg:.2f}" if ctx.home_goals_avg is not None else "-"
            a_g = f"{ctx.away_goals_avg:.2f}" if ctx.away_goals_avg is not None else "-"
            parts.append(
                f"Son 5 maç formunda ev sahibi {hf} (sezon genelinde {h_g} gol ort.), "
                f"deplasman ekibi {af} (sezon genelinde {a_g} gol ort.) grafiğine sahip."
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
        else:
            parts.append("Her iki ekipte de kayıtlı önemli sakat/cezalı eksiği bulunmuyor.")

    if not parts:
        parts.append(
            f"{match.league} kapsamındaki {match.home_team} - {match.away_team} maçı için güncel oran hareketleri takip ediliyor."
        )

    return sanitize_turkish_summary(" ".join(parts), ctx.injuries_json if ctx else None)


def generate_match_summary(
    db: Session,
    match: Match,
    force_refresh: bool = False,
    prediction_id: Optional[int] = None,
) -> tuple[str, str]:
    """
    Return (summary_text, source) using DB cache first, then lightweight Gemini REST API,
    falling back seamlessly to rule-based Turkish summary if key is absent or quota hits.
    """
    global _LAST_GEMINI_CALL_TS, _GEMINI_COOLDOWN_UNTIL

    ctx = db.query(MatchContext).filter(MatchContext.match_id == match.id).first()
    if not ctx:
        ctx = MatchContext(match_id=match.id)
        db.add(ctx)
        db.flush()

    top_pred = None
    if prediction_id is not None:
        top_pred = (
            db.query(Prediction)
            .filter(Prediction.match_id == match.id, Prediction.id == prediction_id)
            .first()
        )
    if top_pred is None:
        top_pred = (
            db.query(Prediction)
            .filter(Prediction.match_id == match.id, Prediction.status == "PENDING")
            .order_by(Prediction.is_top_pick.desc(), Prediction.confidence_score.desc())
            .first()
        )
    current_pick = f"{top_pred.market_key}:{top_pred.selection_key}" if top_pred else None

    if ctx.ai_summary and not force_refresh and ctx.ai_summary_pick == current_pick:
        return sanitize_turkish_summary(ctx.ai_summary, ctx.injuries_json), ctx.ai_source or "cached"

    api_key = (settings.gemini_api_key or "").strip()
    gemini_failed = False
    if api_key:
        with _GEMINI_LOCK:
            now_mono = time.monotonic()
            if now_mono < _GEMINI_COOLDOWN_UNTIL:
                gemini_failed = True
            else:
                elapsed = now_mono - _LAST_GEMINI_CALL_TS
                if elapsed < _MIN_INTERVAL_SEC:
                    time.sleep(_MIN_INTERVAL_SEC - elapsed)

        if not gemini_failed:
            hf = form_to_turkish(json.loads(ctx.home_form)) if ctx.home_form else ""
            af = form_to_turkish(json.loads(ctx.away_form)) if ctx.away_form else ""
            inj = json.loads(ctx.injuries_json) if ctx.injuries_json else {}
            h_inj_list = [x["player"] for x in (inj.get("home") or [])[:4]]
            a_inj_list = [x["player"] for x in (inj.get("away") or [])[:4]]
            h5_avg, h5_cnt = _compute_exact_5m_goal_avg(db, match.id, "home")
            a5_avg, a5_cnt = _compute_exact_5m_goal_avg(db, match.id, "away")
            h_avg_lbl = f"Son {h5_cnt} maç ort: {h5_avg:.2f}" if h5_avg is not None else f"Sezon geneli ort: {ctx.home_goals_avg}"
            a_avg_lbl = f"Son {a5_cnt} maç ort: {a5_avg:.2f}" if a5_avg is not None else f"Sezon geneli ort: {ctx.away_goals_avg}"

            prompt = (
                "Sen uzman bir futbol veri analistisin. Yalnızca aşağıdaki GERÇEK verilere dayanarak "
                "3-4 cümlelik net, klişesiz, akıcı Türkçe maç analizi yaz. Veri uydurma.\n"
                "KURALLAR: Form harflerini SADECE Türkçe yaz (G=Galibiyet, B=Beraberlik, M=Mağlubiyet), asla W/D/L yazma. "
                "Eğer sezon geneli ortalama verildiyse 'son 5 maçta' deme, 'sezon genelinde' de. "
                "Eksikler boşsa 'kadro sağlam' de ve asla 'eksiklere rağmen' yazma. Aynı cümlede iki kez 'rağmen' kullanma.\n"
                f"Maç: {match.home_team} vs {match.away_team} ({match.league})\n"
                f"Öne Çıkan Seçim: {top_pred.market_key if top_pred else '-'} {top_pred.selection_key if top_pred else '-'} "
                f"| İddaa Oranı: {top_pred.iddaa_odd if top_pred else '-'} | Global Oran: {top_pred.global_odd if top_pred else '-'} "
                f"| Güven Skoru: {top_pred.confidence_score if top_pred else '-'}/100\n"
                f"Ev Son 5 Form (G/B/M): {hf or 'Yok'} ({h_avg_lbl})\n"
                f"Dep Son 5 Form (G/B/M): {af or 'Yok'} ({a_avg_lbl})\n"
                f"Ev Eksikler: {h_inj_list or 'Yok (Kadro sağlam)'}\n"
                f"Dep Eksikler: {a_inj_list or 'Yok (Kadro sağlam)'}\n"
            )

            for model_name in GEMINI_MODELS:
                try:
                    with _GEMINI_LOCK:
                        _LAST_GEMINI_CALL_TS = time.monotonic()
                    endpoint = (
                        f"https://generativelanguage.googleapis.com/v1beta/models/"
                        f"{model_name}:generateContent"
                    )
                    resp = httpx.post(
                        endpoint,
                        headers={"x-goog-api-key": api_key},
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 650},
                        },
                        timeout=11.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_text = (
                            data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [{}])[0]
                            .get("text", "")
                            .strip()
                        )
                        text = sanitize_turkish_summary(_finalize_ai_summary(raw_text), ctx.injuries_json)
                        if text:
                            ctx.ai_summary = text
                            ctx.ai_summary_pick = current_pick
                            ctx.ai_source = "gemini"
                            ctx.ai_generated_at = datetime.now(timezone.utc)
                            db.commit()
                            return text, "gemini"
                    elif resp.status_code in (429, 503):
                        with _GEMINI_LOCK:
                            _GEMINI_COOLDOWN_UNTIL = time.monotonic() + _GEMINI_COOLDOWN_SEC
                        logger.warning(
                            "[Fikstra AI] Gemini HTTP %s on %s; activating 10m circuit breaker",
                            resp.status_code,
                            model_name,
                        )
                        gemini_failed = True
                        break
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
        fallback_text = _build_rule_based_summary(match, top_pred, ctx, db=db)
        return fallback_text, "transient_error"

    fallback_text = _build_rule_based_summary(match, top_pred, ctx, db=db)
    ctx.ai_summary = fallback_text
    ctx.ai_summary_pick = current_pick
    ctx.ai_source = "rule_based"
    ctx.ai_generated_at = datetime.now(timezone.utc)
    db.commit()
    return fallback_text, "rule_based"
