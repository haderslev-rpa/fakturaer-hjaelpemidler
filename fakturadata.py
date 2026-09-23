"""Udtræk procesdata fra det eksisterende OIOUBL-parseroutput."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)"
)
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
    header_cpr = re.sub(
        r"\D", "", str(parsed_invoice.get("header", {}).get("cpr") or "")
    )
    if len(header_cpr) == 10 and header_cpr not in values:
        values.append(header_cpr)
    if len(values) != 1:
        raise ValueError(f"Der forventes præcis ét CPR på fakturaen. Fundet: {len(values)}.")
    return values[0]


def beregn_alder_fra_cpr(cpr: str, reference_date: date | None = None) -> int:
    """Beregn alder fra CPR. Århundrede udledes af løbenummeret."""
    digits = re.sub(r"\D", "", cpr)
    if len(digits) != 10:
        raise ValueError("CPR skal bestå af 10 cifre.")
    day, month, year2 = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
    sequence = int(digits[6:10])
    if 0 <= sequence <= 3999:
        century = 1900
    elif 4000 <= sequence <= 4999:
        century = 2000 if year2 <= 36 else 1900
    elif 5000 <= sequence <= 8999:
        century = 2000 if year2 <= 57 else 1800
    else:
        century = 2000 if year2 <= 36 else 1900
    birth_date = date(century + year2, month, day)
    today = reference_date or date.today()
    age = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        age -= 1
    return age


def find_periode_og_leveringsdato(parsed_invoice: dict[str, Any]) -> dict[str, Any]:
    """Find periode, derefter leveringsdato og til sidst fakturadato."""
    texts = _process_texts(parsed_invoice)
    invoice_date = _parse_date(parsed_invoice.get("header", {}).get("invoice_date"))

    for text in texts:
        dates = _extract_dates(text)
        if len(dates) >= 2 and any(word in text.casefold() for word in PERIOD_WORDS):
            start, end = sorted(dates[:2])
            return {
                "fakturadato": invoice_date,
                "leveringsdato": start,
                "fakturaperiode_start": start,
                "fakturaperiode_slut": end,
                "periode_kilde": "invoice_period",
            }
    for text in texts:
        dates = _extract_dates(text)
        if dates and any(word in text.casefold() for word in DELIVERY_WORDS):
            delivery = dates[0]
            return {
                "fakturadato": invoice_date,
                "leveringsdato": delivery,
                "fakturaperiode_start": delivery,
                "fakturaperiode_slut": delivery,
                "periode_kilde": "delivery_date",
            }
    if invoice_date is not None:
        return {
            "fakturadato": invoice_date,
            "leveringsdato": invoice_date,
            "fakturaperiode_start": invoice_date,
            "fakturaperiode_slut": invoice_date,
            "periode_kilde": "invoice_date",
        }
    raise ValueError("Fakturaperiode, leveringsdato og fakturadato kunne ikke findes.")


def find_fakturabeloeb(
    parsed_invoice: dict[str, Any],
    faktura: dict[str, Any],
) -> dict[str, Decimal]:
    """
    Find fakturaens netto-, moms- og bruttobeløb.

    Datakilder:
    1. OIOUBL-parserens header bruges først.
    2. Manglende bruttobeløb og momsbeløb hentes
       som reserve fra Prisme.

    Beregning:
        netto = brutto - moms

    Hvis parserens total_amount er udfyldt, bruges
    værdien som nettobeløb.

    Output:
        {
            "fakturabeloeb_netto": Decimal("100.00"),
            "fakturabeloeb_moms": Decimal("25.00"),
            "fakturabeloeb_brutto": Decimal("125.00"),
        }
    """
    if not isinstance(
        parsed_invoice,
        dict,
    ):
        raise TypeError(
            "parsed_invoice skal være en dictionary."
        )

    if not isinstance(
        faktura,
        dict,
    ):
        raise TypeError(
            "faktura skal være en dictionary."
        )

    header = parsed_invoice.get(
        "header",
        {},
    )

    if not isinstance(
        header,
        dict,
    ):
        raise TypeError(
            "Parserens header skal være en dictionary."
        )

    # ------------------------------------------------------
    # OIOUBL-BELØB
    # ------------------------------------------------------

    parser_netto = _optional_decimal(
        header.get(
            "total_amount"
        )
    )

    parser_moms = _optional_decimal(
        header.get(
            "vat_amount"
        )
    )

    parser_brutto = _optional_decimal(
        header.get(
            "payable_amount"
        )
    )

    # ------------------------------------------------------
    # PRISME-BELØB
    # ------------------------------------------------------

    prisme_brutto = _optional_decimal(
        faktura.get(
            "Importeret fakturabeløb"
        )
    )

    prisme_moms = _optional_decimal(
        faktura.get(
            "Momsbeløb"
        )
    )

    # Parseren er førstevalg.
    # Prisme bruges, hvis parserfeltet er tomt.
    fakturabeloeb_brutto = (
        parser_brutto
        if parser_brutto is not None
        else prisme_brutto
    )

    fakturabeloeb_moms = (
        parser_moms
        if parser_moms is not None
        else prisme_moms
    )

    if fakturabeloeb_brutto is None:
        raise ValueError(
            "Fakturaens bruttobeløb kunne ikke findes. "
            "Både parserens payable_amount og Prismes "
            "Importeret fakturabeløb er tomme."
        )

    if fakturabeloeb_moms is None:
        raise ValueError(
            "Fakturaens momsbeløb kunne ikke findes. "
            "Både parserens vat_amount og Prismes "
            "Momsbeløb er tomme."
        )

    # Hvis parseren har et egentligt nettobeløb,
    # bruges dette. Ellers beregnes netto.
    if parser_netto is not None:
        fakturabeloeb_netto = (
            parser_netto
        )
    else:
        fakturabeloeb_netto = (
            fakturabeloeb_brutto
            - fakturabeloeb_moms
        )

    if fakturabeloeb_netto < Decimal("0"):
        raise ValueError(
            "Det beregnede nettobeløb er negativt. "
            f"Brutto: {fakturabeloeb_brutto}. "
            f"Moms: {fakturabeloeb_moms}. "
            f"Netto: {fakturabeloeb_netto}."
        )

    print()
    print("=" * 80)
    print("FAKTURABELØB")
    print("=" * 80)
    print(
        "Nettobeløb:",
        fakturabeloeb_netto,
    )
    print(
        "Momsbeløb:",
        fakturabeloeb_moms,
    )
    print(
        "Bruttobeløb:",
        fakturabeloeb_brutto,
    )
    print(
        "Nettokilde:",
        (
            "OIOUBL total_amount"
            if parser_netto is not None
            else "Bruttobeløb minus momsbeløb"
        ),
    )

    return {
        "fakturabeloeb_netto": (
            fakturabeloeb_netto
        ),
        "fakturabeloeb_moms": (
            fakturabeloeb_moms
        ),
        "fakturabeloeb_brutto": (
            fakturabeloeb_brutto
        ),
    }


def _process_texts(parsed_invoice: dict[str, Any]) -> list[str]:
    header = parsed_invoice.get("header", {})
    texts = [str(header.get("additional_information") or "").strip()]
    for line in parsed_invoice.get("lines", []):
        texts.extend(
            str(line.get(key) or "").strip()
            for key in ("description", "note", "all_details")
        )
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
