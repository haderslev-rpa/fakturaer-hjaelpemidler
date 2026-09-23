"""Vælg kontostreng, regnskabsår og bogføringsdato."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from excelgrundlag import hent_kontostrengsraekker

logger = logging.getLogger(__name__)


def find_kontostreng(
    ydelsesnavn: str,
    alder: int,
    ydelsesdato: date,
    fakturadato: date | None,
    dagsdato: date | None = None,
) -> dict[str, Any]:
    """Returnér første Excel-række, hvor Tekst findes i ydelsesnavnet.

    Output:
        Dictionary med excel_ark, excel_tekst, kontostreng,
        regnskabsaar og bogfoeringsdato.
    """
    today = dagsdato or date.today()
    arknoegle, regnskabsaar = _vaelg_ark(today, ydelsesdato)
    rows = hent_kontostrengsraekker(arknoegle)
    normalized_service_name = _normaliser(ydelsesnavn)
    if not normalized_service_name:
        raise ValueError("ydelsesnavn skal være udfyldt.")

    age_column = "Under 18" if alder < 18 else "Under 67" if alder < 67 else "Over 67"

    for row in rows:
        if not isinstance(row, dict):
            continue
        excel_text = str(row.get("Tekst") or "").strip()
        normalized_excel_text = _normaliser(excel_text)
        if not normalized_excel_text or normalized_excel_text not in normalized_service_name:
            continue

        account_string = str(row.get(age_column) or "").strip()
        if not account_string:
            raise ValueError(
                f"Rækken med teksten {excel_text!r} mangler kontostreng i kolonnen {age_column!r}."
            )

        posting_date = _vaelg_bogfoeringsdato(
            regnskabsaar=regnskabsaar,
            fakturadato=fakturadato,
            dagsdato=today,
        )
        sheet_name = (
            "kontostrenge 2026"
            if arknoegle == "kontostrenge_2026"
            else "Kontostrenge andre år"
        )
        logger.info(
            "Kontostreng fundet: ark=%s, tekst=%s, alderskolonne=%s",
            sheet_name,
            excel_text,
            age_column,
        )
        return {
            "excel_ark": sheet_name,
            "excel_tekst": excel_text,
            "alderskolonne": age_column,
            "kontostreng": account_string,
            "regnskabsaar": regnskabsaar,
            "bogfoeringsdato": posting_date,
        }

    raise ValueError(
        "Ingen kontostrengsrække blev fundet, hvor teksten i kolonnen 'Tekst' "
        f"findes i ydelsesnavnet. Ydelse: {ydelsesnavn!r}."
    )


def _vaelg_ark(today: date, ydelsesdato: date) -> tuple[str, int]:
    if today.year == 2026:
        return "kontostrenge_2026", 2026
    if today.month == 1 and today.day <= 20 and ydelsesdato.year == 2026:
        return "kontostrenge_2026", 2026
    return "kontostrenge_andre_aar", today.year


def _vaelg_bogfoeringsdato(
    regnskabsaar: int,
    fakturadato: date | None,
    dagsdato: date,
) -> date:
    if fakturadato is not None and fakturadato.year == regnskabsaar:
        return fakturadato
    if regnskabsaar == 2026:
        return date(2026, 12, 31)
    return dagsdato


def _normaliser(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())
