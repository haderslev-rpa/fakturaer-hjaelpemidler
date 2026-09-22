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
) -> dict:
    """
    Luk én ydelse i CURA med dags dato.

    Output:
        Returnerer resultatet direkte fra
        q_cura.functionality.afslut_ydelse.

        Eksempel:

        {
            "citizen_id": "...",
            "ydelse_navn": "...",
            "leverandoer": "...",
            "slutdato": "22.09.2026",
            "status": "afsluttet",
        }
    """
    if page is None:
        raise ValueError(
            "page skal være udfyldt."
        )

    if session is None:
        raise ValueError(
            "session skal være udfyldt."
        )

    borger_id = str(
        borger_id or ""
    ).strip()

    ydelsesnavn = str(
        ydelsesnavn or ""
    ).strip()

    leverandoernavn = str(
        leverandoernavn or ""
    ).strip()

    if not borger_id:
        raise ValueError(
            "borger_id skal være udfyldt."
        )

    if not ydelsesnavn:
        raise ValueError(
            "ydelsesnavn skal være udfyldt."
        )

    if not leverandoernavn:
        raise ValueError(
            "leverandoernavn skal være udfyldt."
        )

    slutdato = date.today()

    print()
    print("Lukker ydelse i CURA")
    print(
        "Ydelse:",
        ydelsesnavn,
    )
    print(
        "Leverandør:",
        leverandoernavn,
    )
    print(
        "Slutdato:",
        slutdato.strftime("%d.%m.%Y"),
    )

    resultat = await afslut_ydelse(
        page=page,
        session=session,
        citizen_id=borger_id,
        ydelse_navn=ydelsesnavn,
        leverandoer=leverandoernavn,
        slutdato=slutdato,
        stop_foer_gem=False,
    )

    if resultat.get("status") != "afsluttet":
        raise RuntimeError(
            "CURA bekræftede ikke, at ydelsen "
            "blev afsluttet. "
            f"Resultat: {resultat!r}"
        )

    print(
        "Ydelsen blev afsluttet i CURA."
    )

    return resultat