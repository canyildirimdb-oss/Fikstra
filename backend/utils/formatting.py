"""Shared Turkish formatting helpers for Fikstra markets and picks."""


def _format_au_line(market_key: str) -> str:
    raw_line = market_key.split("_", 1)[1].strip() if "_" in market_key else "2.5"
    if "." not in raw_line and raw_line.isdigit() and len(raw_line) == 2:
        return f"{raw_line[0]}.{raw_line[1]}"
    return raw_line or "2.5"


def format_pick_tr(market_key: str, selection_key: str = "") -> str:
    """
    Format market/selection keys into clean Turkish labels.
    Normalizes AU_15 / AU_25 / AU_35 into '1.5 Üst', '2.5 Alt', '2.5 Üst / Alt', etc.
    """
    mk = (market_key or "").strip().upper()
    sk = (selection_key or "").strip().upper()

    if mk.startswith("AU_"):
        line = _format_au_line(mk)
        if sk == "UST":
            return f"{line} Üst"
        if sk == "ALT":
            return f"{line} Alt"
        return f"{line} Üst / Alt"
    if mk == "MS":
        return f"MS {sk}" if sk else "Maç Sonucu"
    if mk == "CS":
        return f"ÇŞ {sk}" if sk else "Çifte Şans"
    if mk == "KG":
        if sk == "VAR":
            return "KG Var"
        if sk == "YOK":
            return "KG Yok"
        return "Karşılıklı Gol"
    return f"{market_key} {selection_key}".strip()


_LEAGUE_DISPLAY_OVERRIDES = {
    "Meksika Primera Ligi, Açılış Playoffla": "Meksika Primera Ligi, Açılış Playoffları",
    "Meksika ExpansionMX Lig,Açılış,Playoff": "Meksika Expansion MX Ligi, Açılış Playoff",
}


def format_league_name_tr(league: str | None) -> str:
    """Fix İddaa's 38-char truncated league names for clean UI display."""
    raw = (league or "").strip()
    if not raw:
        return "Diğer"
    if raw in _LEAGUE_DISPLAY_OVERRIDES:
        return _LEAGUE_DISPLAY_OVERRIDES[raw]
    if raw.endswith(" Playoffla"):
        return raw[:-10] + " Playoffları"
    return raw
