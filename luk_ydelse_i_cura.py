"""Luk en ydelse i CURA via q-cura."""
from __future__ import annotations

from datetime import date
from typing import Any

from playwright.async_api import Page
from q_cura.functionality.afslut_ydelse import afslut_ydelse


async def luk_ydelse_i_cura(
    page: Page,
    session: Any,
    borger_id: str,
    ydelsesnavn: str,
    leverandoernavn: str,
) -> dict:
    """Luk én ydelse i CURA med dags dato og returnér q-cura-resultatet."""
    if page is None or session is None:
        raise ValueError("page og session skal være udfyldt.")
    borger_id = str(borger_id or "").strip()
    ydelsesnavn = str(ydelsesnavn or "").strip()
    leverandoernavn = str(leverandoernavn or "").strip()
    if not borger_id or not ydelsesnavn or not leverandoernavn:
        raise ValueError("borger_id, ydelsesnavn og leverandoernavn skal være udfyldt.")

    slutdato = date.today()
    print("Lukker ydelse i CURA:", ydelsesnavn, "|", leverandoernavn, "|", slutdato)
    result = await afslut_ydelse(
        page=page,
        session=session,
        citizen_id=borger_id,
        ydelse_navn=ydelsesnavn,
        leverandoer=leverandoernavn,
        slutdato=slutdato,
        stop_foer_gem=False,
    )
    if str(result.get("status") or "").casefold() != "afsluttet":
        raise RuntimeError(f"CURA bekræftede ikke afslutningen. Resultat: {result!r}")
    return result
