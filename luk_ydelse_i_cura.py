"""Playwright-koblingspunkt til senere lukning af ydelse i CURA."""
from __future__ import annotations

from typing import Any


async def luk_ydelse_i_cura(
    page,
    borger_id: str,
    ydelse_id: str,
    ydelsesnavn: str,
    slutdato: str | None = None,
) -> dict[str, Any]:
    """Klargør CURA-lukningen uden at ændre noget endnu.

    Output viser tydeligt, at ydelsen ikke er lukket. Playwright-logikken
    til URL, valg af ydelse og luk-funktion tilføjes senere i denne fil.
    """
    values = {
        "borger_id": str(borger_id or "").strip(),
        "ydelse_id": str(ydelse_id or "").strip(),
        "ydelsesnavn": str(ydelsesnavn or "").strip(),
        "slutdato": str(slutdato or "").strip(),
    }
    for key in ("borger_id", "ydelse_id", "ydelsesnavn"):
        if not values[key]:
            raise ValueError(f"{key} skal være udfyldt.")
    return {"klargjort": True, "lukket_i_cura": False, **values}
