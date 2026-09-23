"""Producer til Fakturaer - Hjælpemidler."""
from __future__ import annotations

import logging
from typing import Any

from q_prisme365_api.api_client import initialiser_prisme
from q_prisme365_api.functionality.fakturaer import search_fakturaer

from excelgrundlag import hent_tilladte_leverandoerer, normaliser_cvr
from konfiguration import AFDELING, HJALPEMIDLER_EAN, PRISME_CREDENTIAL

logger = logging.getLogger(__name__)


def hent_queue_items() -> list[dict[str, Any]]:
    """Hent relevante Prisme-fakturaer og returnér data til queue-items."""
    print()
    print("=" * 80)
    print("QUEUE: HENTER TILLADTE LEVERANDØRER")
    print("=" * 80)
    suppliers = hent_tilladte_leverandoerer()
    print("Antal tilladte leverandører i Excel:", len(suppliers))

    print()
    print("=" * 80)
    print("QUEUE: HENTER FAKTURAER FRA PRISME")
    print("=" * 80)
    initialiser_prisme(PRISME_CREDENTIAL)
    invoices = search_fakturaer(
        afdeling=AFDELING,
        hent_detaljer=True,
        hent_dokumenter=True,
        hent_dokumentplacering=True,
    )
    print("Antal fakturaer fundet i Prisme:", len(invoices))

    output = []
    counters = {"forkert_ean": 0, "ikke_tilladt": 0, "mangler_data": 0}
    for invoice in invoices:
        if not isinstance(invoice, dict):
            counters["mangler_data"] += 1
            continue
        ean = str(invoice.get("EAN") or "").strip()
        if ean != HJALPEMIDLER_EAN:
            counters["forkert_ean"] += 1
            continue

        vendor_name = str(invoice.get("Leverandørnavn") or "").strip()
        cvr = normaliser_cvr(invoice.get("CVR"))
        supplier = _find_supplier(vendor_name, cvr, suppliers)
        if supplier is None:
            counters["ikke_tilladt"] += 1
            continue

        document = invoice.get("OIOUBL-dokument")
        document_path = (
            str(document.get("Dokumentsti") or "").strip()
            if isinstance(document, dict)
            else ""
        )
        item = {
            "ean": ean,
            "afdeling": str(invoice.get("Afdeling") or AFDELING).strip(),
            "fakturanr": str(invoice.get("Fakturanr") or "").strip(),
            "rec_id_loc": invoice.get("RecIdLoc"),
            "dokumentsti": document_path,
            "leverandoernavn": vendor_name,
            "header_reference": str(invoice.get("HeaderReference") or "").strip(),
        }
        missing = [key for key, value in item.items() if value in (None, "")]
        if missing:
            counters["mangler_data"] += 1
            print("Springer faktura over. Mangler:", ", ".join(missing))
            continue
        output.append(item)
        print(
            "Klargjort til kø:",
            item["fakturanr"], "|", item["leverandoernavn"], "|", item["header_reference"],
        )

    print()
    print("=" * 80)
    print("QUEUE: PRISME-UDVÆLGELSE AFSLUTTET")
    print("=" * 80)
    print("Fakturaer fundet i Prisme:", len(invoices))
    print("Klargjort til kø:", len(output))
    print("Forkert EAN:", counters["forkert_ean"])
    print("Ikke på leverandørlisten:", counters["ikke_tilladt"])
    print("Mangler obligatoriske data:", counters["mangler_data"])
    return output


def _find_supplier(
    name: str,
    cvr: str,
    suppliers: list[dict[str, str]],
) -> dict[str, str] | None:
    normalized_name = " ".join(name.casefold().split())
    for supplier in suppliers:
        if cvr and cvr == supplier["cvr_nr"]:
            return supplier
    for supplier in suppliers:
        allowed = " ".join(supplier["fakturaafsender"].casefold().split())
        if allowed and (allowed in normalized_name or normalized_name in allowed):
            return supplier
    return None
