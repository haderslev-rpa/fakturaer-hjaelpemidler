"""Match fakturaen med præcis én ydelse i CURA."""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from q_cura_api.functionality.organisation_hent import get_organizations

logger = logging.getLogger(__name__)
FRIT_VALG_NAVN = "(Hjælpemidler) Frit valg"


def find_organisation_ids(
    leverandoernavn_prisme: str,
    leverandoernavn_oioubl: str,
) -> dict[str, Any]:
    """Find leverandørens organisation-id'er og id'et til Frit valg.

    Output:
        Dictionary med leverandoer_organization_ids og
        frit_valg_organization_id.
    """
    result = get_organizations(
        "*(Hjælpemidler)*",
        raw=False,
        include_inactive=True,
    )
    if not isinstance(result, dict):
        raise TypeError("get_organizations skal returnere en dictionary.")

    organizations = result.get("organizationer", [])
    if not isinstance(organizations, list):
        raise TypeError("Feltet 'organizationer' skal være en liste.")

    invoice_names = [
        value for value in (
            str(leverandoernavn_prisme or "").strip(),
            str(leverandoernavn_oioubl or "").strip(),
        ) if value
    ]
    if not invoice_names:
        raise ValueError("Leverandørnavnet mangler både i Prisme og OIOUBL.")

    supplier_ids: set[str] = set()
    free_choice_ids: list[str] = []

    for organization in organizations:
        if not isinstance(organization, dict):
            continue
        name = str(organization.get("name") or "").strip()
        organization_id = str(organization.get("organization_id") or "").strip()
        if not name or not organization_id:
            continue

        if _normaliser_organisation(name) == _normaliser_organisation(FRIT_VALG_NAVN):
            free_choice_ids.append(organization_id)

        if any(_leverandoer_matcher(invoice_name, name) for invoice_name in invoice_names):
            supplier_ids.add(organization_id)

    if len(free_choice_ids) != 1:
        raise ValueError(
            f"Der forventes præcis én CURA-organisation med navnet {FRIT_VALG_NAVN!r}. "
            f"Fundet: {len(free_choice_ids)}."
        )

    logger.info(
        "CURA-organisationer fundet: %s leverandør-id(er), Frit valg-id fundet",
        len(supplier_ids),
    )
    return {
        "leverandoer_organization_ids": supplier_ids,
        "frit_valg_organization_id": free_choice_ids[0],
    }


def find_matchende_ydelser(
    ydelser: list[dict[str, Any]],
    leverandoer_organization_ids: set[str],
    frit_valg_organization_id: str,
    leveringsdato: date,
    fakturabeloeb: Decimal,
    tolerance: Decimal,
) -> list[dict[str, Any]]:
    """Match først på leverandør-id og derefter, om nødvendigt, Frit valg.

    Output:
        Liste med ydelser, der matcher status, dato, beløb og organisation.
    """
    if not isinstance(ydelser, list):
        raise TypeError("ydelser skal være en liste.")
    if not isinstance(leveringsdato, date):
        raise TypeError("leveringsdato skal være en date-værdi.")

    invoice_amount = _to_decimal(fakturabeloeb, "fakturabeloeb")
    allowed_difference = _to_decimal(tolerance, "tolerance")
    supplier_ids = {str(value).strip() for value in leverandoer_organization_ids if str(value).strip()}
    free_choice_id = str(frit_valg_organization_id or "").strip()
    if not free_choice_id:
        raise ValueError("frit_valg_organization_id skal være udfyldt.")

    valid_services = []
    for service in ydelser:
        if not isinstance(service, dict):
            continue

        status = str(service.get("status") or "").strip().casefold()
        if status not in {"requested", "active", "accepted", "in-progress"}:
            continue

        start_date = _parse_optional_date(service.get("startdato"))
        end_date = _parse_optional_date(service.get("slutdato"))
        if start_date is None:
            continue
        if leveringsdato < start_date:
            continue

        latest_delivery_date = _add_months(start_date, 6)
        if leveringsdato > latest_delivery_date:
            continue
        if end_date is not None and end_date < leveringsdato:
            continue

        try:
            service_amount = udtraek_beloeb_fra_bemaerkninger(service)
        except ValueError:
            continue

        difference = abs(invoice_amount - service_amount)
        if difference > allowed_difference:
            continue

        matched = dict(service)
        matched["bevilling_beloeb"] = service_amount
        matched["bevilling_startdato"] = start_date.isoformat()
        matched["seneste_leveringsdato"] = latest_delivery_date.isoformat()
        matched["beloebsdifference"] = difference
        matched["organization_id"] = str(service.get("organization_id") or "").strip()
        valid_services.append(matched)

    supplier_matches = [
        service for service in valid_services
        if service.get("organization_id") in supplier_ids
    ]
    if supplier_matches:
        for service in supplier_matches:
            service["frit_valg"] = False
            service["leverandoer_match_kilde"] = "organisation_id"
        logger.info("Ydelsesmatch: %s match på leverandør-id", len(supplier_matches))
        return supplier_matches

    free_choice_matches = [
        service for service in valid_services
        if service.get("organization_id") == free_choice_id
    ]
    for service in free_choice_matches:
        service["frit_valg"] = True
        service["leverandoer_match_kilde"] = "frit_valg_organisation_id"

    logger.info("Ydelsesmatch: %s match på Frit valg-id", len(free_choice_matches))
    return free_choice_matches


def validate_entydig_ydelse(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Returnér den ene ydelse eller rejs ValueError."""
    if len(matches) != 1:
        raise ValueError(
            "Der forventes præcis én matchende CURA-ydelse. "
            f"Fundet: {len(matches)}."
        )
    return matches[0]


def udtraek_beloeb_fra_bemaerkninger(ydelse: dict[str, Any]) -> Decimal:
    """Udtræk første beløb fra det normaliserede felt Bemærkninger."""
    text = str(
        ydelse.get("bemærkninger")
        or ydelse.get("bemaerkninger")
        or ""
    ).strip()
    if not text:
        raise ValueError("Ydelsen mangler Bemærkninger.")

    match = re.search(r"-?\d[\d. ]*(?:,\d+|\.\d+)?", text)
    if not match:
        raise ValueError("Bemærkninger indeholder ikke et beløb.")

    amount_text = match.group(0).replace(" ", "")
    if "," in amount_text:
        amount_text = amount_text.replace(".", "").replace(",", ".")
    try:
        return Decimal(amount_text)
    except InvalidOperation as error:
        raise ValueError(f"Beløbet kan ikke fortolkes: {amount_text!r}.") from error


def _leverandoer_matcher(forventet: str, faktisk: str) -> bool:
    expected = _normaliser_organisation(forventet)
    actual = _normaliser_organisation(faktisk)
    return bool(expected and actual and (expected in actual or actual in expected))


def _normaliser_organisation(value: Any) -> str:
    text = " ".join(str(value or "").casefold().split())
    text = re.sub(r"^\s*\(\s*hjælpemidler\s*\)\s*", "", text)
    text = re.sub(r"\s*\(\s*eksterne\s+ydelsesleverandører\s*\)\s*$", "", text)
    return " ".join(text.split()).strip()


def _parse_optional_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _add_months(value: date, months: int) -> date:
    total = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(total, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _to_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    text = str(value or "").strip().replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{name} kan ikke fortolkes som beløb: {value!r}.") from error
