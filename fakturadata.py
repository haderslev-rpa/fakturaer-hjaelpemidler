"""Udtræk procesdata fra q-oioubl-faktura-parserens eksisterende output."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)")
PERIOD_WORDS = ("periode", "perioden", "fra", "til")
DELIVERY_WORDS = ("leveringsdato", "leveret", "levering")


def find_entydigt_cpr(parsed_invoice: dict[str, Any]) -> str:
    """Returnér præcis ét unikt CPR fra parserens normale output."""
    values = []
    for line in parsed_invoice.get("lines", []):
        for cpr in line.get("cpr", []):
            normalized = re.sub(r"\D", "", str(cpr))
            if len(normalized) == 10 and normalized not in values:
                values.append(normalized)
    header_cpr = re.sub(r"\D", "", str(parsed_invoice.get("header", {}).get("cpr") or ""))
    if len(header_cpr) == 10 and header_cpr not in values:
        values.append(header_cpr)
    if len(values) != 1:
        raise ValueError(f"Der forventes præcis ét CPR på fakturaen. Fundet: {len(values)}.")
    return values[0]


def find_fakturadato(parsed_invoice: dict[str, Any]) -> date | None:
    return _parse_date(parsed_invoice.get("header", {}).get("invoice_date"))


def find_periode_og_leveringsdato(parsed_invoice: dict[str, Any]) -> dict[str, Any]:
    """Find periode i parser-output, derefter leveringsdato, derefter fakturadato.

    Der læses kun fra headerfelter og linjernes description, note og all_details.
    raw_xml og flattened_xml anvendes ikke.
    """
    texts = _process_texts(parsed_invoice)
    invoice_date = find_fakturadato(parsed_invoice)

    for text in texts:
        lowered = text.casefold()
        dates = _extract_dates(text)
        if len(dates) >= 2 and any(word in lowered for word in PERIOD_WORDS):
            start, end = dates[0], dates[1]
            if end < start:
                start, end = end, start
            return {
                "fakturadato": invoice_date,
                "leveringsdato": None,
                "fakturaperiode_start": start,
                "fakturaperiode_slut": end,
                "periode_kilde": "invoice_period",
            }

    for text in texts:
        lowered = text.casefold()
        dates = _extract_dates(text)
        if dates and any(word in lowered for word in DELIVERY_WORDS):
            delivery_date = dates[0]
            return {
                "fakturadato": invoice_date,
                "leveringsdato": delivery_date,
                "fakturaperiode_start": delivery_date,
                "fakturaperiode_slut": delivery_date,
                "periode_kilde": "delivery_date",
            }

    if invoice_date is not None:
        return {
            "fakturadato": invoice_date,
            "leveringsdato": None,
            "fakturaperiode_start": invoice_date,
            "fakturaperiode_slut": invoice_date,
            "periode_kilde": "invoice_date",
        }
    raise ValueError("Fakturaperiode, leveringsdato og fakturadato kunne ikke findes.")


def find_beloeb_uden_moms(parsed_invoice: dict[str, Any]) -> Decimal:
    """Returnér hele fakturaens beløb uden moms.

    total_amount bruges først. Hvis feltet er tomt, bruges payable_amount.
    Hvis vat_amount findes sammen med payable_amount, trækkes moms fra.
    """
    header = parsed_invoice.get("header", {})
    total = _optional_decimal(header.get("total_amount"))
    if total is not None:
        return total
    payable = _optional_decimal(header.get("payable_amount"))
    vat = _optional_decimal(header.get("vat_amount"))
    if payable is None:
        raise ValueError("Parserens total_amount og payable_amount er tomme.")
    return payable - vat if vat is not None else payable


def _process_texts(parsed_invoice: dict[str, Any]) -> list[str]:
    header = parsed_invoice.get("header", {})
    texts = [
        str(header.get("additional_information") or "").strip(),
    ]
    for line in parsed_invoice.get("lines", []):
        texts.extend([
            str(line.get("description") or "").strip(),
            str(line.get("note") or "").strip(),
            str(line.get("all_details") or "").strip(),
        ])
    return [text for text in texts if text]


def _extract_dates(text: str) -> list[date]:
    result = []
    for match in DATE_PATTERN.findall(text):
        parsed = _parse_date(match)
        if parsed is not None and parsed not in result:
            result.append(parsed)
    return result


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y", "%d.%m.%y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _optional_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Beløbet kan ikke fortolkes: {value!r}.") from error
