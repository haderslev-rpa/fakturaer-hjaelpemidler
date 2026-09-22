"""Producer til Fakturaer - Hjælpemidler."""
from __future__ import annotations

from io import BytesIO
import logging
import re
from typing import Any

from openpyxl import load_workbook
from q_prisme365_api.api_client import initialiser_prisme
from q_prisme365_api.functionality.fakturaer import search_fakturaer
from q_sharepoint_api.sp_api import get_client

from konfiguration import (
    AFDELING,
    HJALPEMIDLER_EAN,
    PRISME_CREDENTIAL,
    SHAREPOINT_LEVERANDOER_FILE_PATH,
    SHAREPOINT_SITE_NAME,
)

logger = logging.getLogger(__name__)


def hent_tilladte_leverandoerer() -> list[
    dict[str, str]
]:
    """
    Hent Excel-filen fra SharePoint til RAM.

    Excel-filen skal indeholde disse faste kolonner:

        Fakturaafsender
        CVR nr

    Output:
        En liste med leverandører:

        [
            {
                "fakturaafsender": "...",
                "cvr_nr": "38051040",
            }
        ]
    """
    client = get_client()

    site_id = client.get_site_id(
        SHAREPOINT_SITE_NAME
    )

    result = (
        client.download_file_to_memory_by_path(
            site_id=site_id,
            file_path=(
                SHAREPOINT_LEVERANDOER_FILE_PATH
            ),
            save_dir=None,
        )
    )

    file_bytes = result.get(
        "file_bytes"
    )

    if not file_bytes:
        raise ValueError(
            "SharePoint-filen blev hentet, "
            "men file_bytes er tom."
        )

    workbook = load_workbook(
        BytesIO(file_bytes),
        read_only=True,
        data_only=True,
    )

    sheet = workbook.active

    rows = sheet.iter_rows(
        values_only=True
    )

    try:
        headers = [
            str(value or "").strip()
            for value in next(rows)
        ]

    except StopIteration as error:
        raise ValueError(
            "Leverandørfilen er tom."
        ) from error

    # Kolonnenavnene er faste og er derfor
    # ikke en del af konfiguration.py.
    fakturaafsender_kolonne = (
        "Fakturaafsender"
    )

    cvr_kolonne = "CVR nr"

    required_columns = {
        fakturaafsender_kolonne,
        cvr_kolonne,
    }

    missing_columns = (
        required_columns.difference(
            headers
        )
    )

    if missing_columns:
        raise ValueError(
            "Leverandørfilen mangler kolonner: "
            f"{sorted(missing_columns)!r}."
        )

    name_index = headers.index(
        fakturaafsender_kolonne
    )

    cvr_index = headers.index(
        cvr_kolonne
    )

    suppliers = []

    seen = set()

    for row in rows:
        name = str(
            row[name_index]
            or ""
        ).strip()

        cvr = _normaliser_cvr(
            row[cvr_index]
        )

        # Helt tomme rækker springes over.
        if not name and not cvr:
            continue

        if not name or not cvr:
            raise ValueError(
                "En række i leverandørfilen "
                "mangler Fakturaafsender "
                "eller CVR nr."
            )

        key = (
            name.casefold(),
            cvr,
        )

        if key in seen:
            continue

        seen.add(key)

        suppliers.append(
            {
                "fakturaafsender": name,
                "cvr_nr": cvr,
            }
        )

    if not suppliers:
        raise ValueError(
            "Leverandørfilen indeholder "
            "ingen gyldige leverandører."
        )

    print(
        "Tilladte leverandører indlæst:",
        len(suppliers),
    )

    return suppliers


def hent_queue_items() -> list[dict[str, Any]]:
    """
    Hent relevante fakturaer fra Prisme.

    Funktionen henter også OIOUBL-dokumentet, så dokumentstien
    allerede ligger i box, når itemet tilføjes til køen.

    Returns:
        En liste med dictionaries i dette format:

        {
            "ean": "...",
            "afdeling": "...",
            "fakturanr": "...",
            "rec_id_loc": 123,
            "dokumentsti": "...",
            "leverandoernavn": "...",
            "header_reference": "...",
        }
    """
    print()
    print("=" * 80)
    print("QUEUE: HENTER TILLADTE LEVERANDØRER")
    print("=" * 80)

    suppliers = hent_tilladte_leverandoerer()

    print(
        "Antal tilladte leverandører i Excel:",
        len(suppliers),
    )

    print()
    print("=" * 80)
    print("QUEUE: HENTER FAKTURAER FRA PRISME")
    print("=" * 80)

    initialiser_prisme(
        PRISME_CREDENTIAL
    )

    search_arguments = {
        "hent_detaljer": True,
        "hent_dokumenter": True,
        "hent_dokumentplacering": True,
    }

    if AFDELING:
        search_arguments["afdeling"] = AFDELING

    invoices = search_fakturaer(
        **search_arguments
    )

    print(
        "Antal fakturaer fundet i Prisme:",
        len(invoices),
    )

    output = []

    antal_forkert_ean = 0
    antal_ikke_tilladt = 0
    antal_mangler_data = 0

    for invoice in invoices:
        if not isinstance(
            invoice,
            dict,
        ):
            antal_mangler_data += 1
            continue

        ean = str(
            invoice.get(
                "EAN",
                "",
            )
            or ""
        ).strip()

        if ean != HJALPEMIDLER_EAN:
            antal_forkert_ean += 1
            continue

        vendor_name = str(
            invoice.get(
                "Leverandørnavn",
                "",
            )
            or ""
        ).strip()

        cvr = _normaliser_cvr(
            invoice.get("CVR")
        )

        match = _find_supplier(
            name=vendor_name,
            cvr=cvr,
            suppliers=suppliers,
        )

        if match is None:
            antal_ikke_tilladt += 1
            continue

        header_reference = str(
            invoice.get(
                "HeaderReference",
                "",
            )
            or ""
        ).strip()

        invoice_number = str(
            invoice.get(
                "Fakturanr",
                "",
            )
            or ""
        ).strip()

        rec_id_loc = invoice.get(
            "RecIdLoc"
        )

        department = str(
            invoice.get(
                "Afdeling",
                "",
            )
            or AFDELING
        ).strip()

        # search_fakturaer har allerede fundet det
        # entydige OIOUBL-dokument.
        oioubl_document = invoice.get(
            "OIOUBL-dokument"
        )

        document_path = ""

        if isinstance(
            oioubl_document,
            dict,
        ):
            document_path = str(
                oioubl_document.get(
                    "Dokumentsti",
                    "",
                )
                or ""
            ).strip()

        missing_fields = []

        if not ean:
            missing_fields.append(
                "EAN"
            )

        if not department:
            missing_fields.append(
                "Afdeling"
            )

        if not invoice_number:
            missing_fields.append(
                "Fakturanr"
            )

        if rec_id_loc in (
            None,
            "",
        ):
            missing_fields.append(
                "RecIdLoc"
            )

        if not document_path:
            missing_fields.append(
                "Dokumentsti"
            )

        if not vendor_name:
            missing_fields.append(
                "Leverandørnavn"
            )

        if not header_reference:
            missing_fields.append(
                "HeaderReference"
            )

        if missing_fields:
            antal_mangler_data += 1

            print(
                "Springer faktura over. "
                "Følgende felter mangler:",
                ", ".join(missing_fields),
            )

            continue

        queue_item = {
            "ean": ean,
            "afdeling": department,
            "fakturanr": invoice_number,
            "rec_id_loc": rec_id_loc,
            "dokumentsti": document_path,
            "leverandoernavn": vendor_name,
            "header_reference": (
                header_reference
            ),
        }

        output.append(
            queue_item
        )

        print(
            "Klargjort til kø:",
            invoice_number,
            "|",
            vendor_name,
            "|",
            header_reference,
        )

    print()
    print("=" * 80)
    print("QUEUE: PRISME-UDVÆLGELSE AFSLUTTET")
    print("=" * 80)
    print(
        "Fakturaer fundet i Prisme:",
        len(invoices),
    )
    print(
        "Klargjort til kø:",
        len(output),
    )
    print(
        "Forkert EAN:",
        antal_forkert_ean,
    )
    print(
        "Ikke på leverandørlisten:",
        antal_ikke_tilladt,
    )
    print(
        "Mangler obligatoriske data:",
        antal_mangler_data,
    )

    return output


def _find_supplier(name: str, cvr: str, suppliers: list[dict[str, str]]) -> dict[str, str] | None:
    """Find leverandør. CVR er primært; navn bruges som ekstra sikkerhed."""
    normalized_name = _normaliser_tekst(name)
    for supplier in suppliers:
        if cvr and cvr == supplier["cvr_nr"]:
            return supplier
    for supplier in suppliers:
        allowed_name = _normaliser_tekst(supplier["fakturaafsender"])
        if allowed_name and (allowed_name in normalized_name or normalized_name in allowed_name):
            return supplier
    return None


def _normaliser_cvr(value: Any) -> str:
    text = re.sub(r"\D", "", str(value or ""))
    if len(text) > 8 and text.startswith("45"):
        text = text[-8:]
    return text


def _normaliser_tekst(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())
