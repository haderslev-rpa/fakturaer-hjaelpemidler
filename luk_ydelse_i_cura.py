"""Luk en ydelse i CURA via q-cura."""

from __future__ import annotations

from datetime import date
from typing import Any

from playwright.async_api import Page

from q_cura.functionality.afslut_ydelse import (
    afslut_ydelse,
)


async def luk_ydelse_i_cura(
    page: Page,
    session: Any,
    borger_id: str,
    ydelsesnavn: str,
    leverandoernavn: str,
    bemaerkninger: str,
) -> dict:
    """
    Luk én ydelse i CURA med dags dato.

    Bemærkninger bruges til at vælge den korrekte ydelse,
    hvis flere ydelser har samme navn og leverandør.

    Output:
        Returnerer resultatet fra q-cura-funktionen
        afslut_ydelse().
    """
    if page is None or session is None:
        raise ValueError(
            "page og session skal være angivet ved CURA-lukning."
        )

    borger_id = str(borger_id or "").strip()
    ydelsesnavn = str(ydelsesnavn or "").strip()
    leverandoernavn = str(
        leverandoernavn or ""
    ).strip()

    bemaerkninger = str(
        bemaerkninger
        if bemaerkninger is not None
        else ""
    )

    if not borger_id:
        raise ValueError(
            "borger_id mangler ved CURA-lukning."
        )

    if not ydelsesnavn:
        raise ValueError(
            "ydelsesnavn mangler ved CURA-lukning."
        )

    if not leverandoernavn:
        raise ValueError(
            "leverandoernavn mangler ved CURA-lukning."
        )

    return await afslut_ydelse(
        page=page,
        session=session,
        citizen_id=borger_id,
        ydelse_navn=ydelsesnavn,
        leverandoer=leverandoernavn,
        bemaerkninger=bemaerkninger,
        slutdato=date.today(),
        stop_foer_gem=False,
    )
