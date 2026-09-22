"""Match én CURA-ydelse til fakturaen."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def find_matchende_ydelser(
    ydelser: list[dict[str, Any]],
    leverandoernavn: str,
    periode_start: date,
    periode_slut: date,
) -> list[dict[str, Any]]:
    """Returnér aktive ydelser, som matcher navn og overlapper perioden."""
    needle = _normaliser_tekst(leverandoernavn)
    if not needle:
        raise ValueError("leverandoernavn skal være udfyldt.")
    matches = []
    for ydelse in ydelser:
        if str(ydelse.get("status") or "").casefold() != "active":
            continue
        name = _normaliser_tekst(ydelse.get("ydelsesnavn"))
        if needle not in name:
            continue
        start = _parse_optional_date(ydelse.get("startdato"))
        end = _parse_optional_date(ydelse.get("slutdato"))
        if start is not None and start > periode_slut:
            continue
        if end is not None and end < periode_start:
            continue
        matches.append(ydelse)
    return matches


def validate_entydig_ydelse(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Returnér den ene ydelse eller rejs en kontrolleret fejl."""
    if len(matches) != 1:
        raise ValueError(f"Der forventes præcis én matchende CURA-ydelse. Fundet: {len(matches)}.")
    return matches[0]


def beloeb_matcher(fakturabeloeb: Decimal, cura_takst: Any, tolerance: Decimal) -> bool:
    """Returnér True når forskellen er højst den konfigurerede tolerance."""
    try:
        takst = Decimal(str(cura_takst).replace(",", "."))
    except InvalidOperation as error:
        raise ValueError(f"CURA-taksten er ugyldig: {cura_takst!r}.") from error
    return abs(fakturabeloeb - takst) <= tolerance


def _normaliser_tekst(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _parse_optional_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None
