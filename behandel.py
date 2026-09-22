"""Fakturaer - Hjælpemidler. Behandler ét ATS-item ad gangen."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from q_cura_api.api_client import set_cura_credential
from q_cura_api.functionality.borger_soeg_cpr import get_borger_by_cpr
from q_cura_api.functionality.borger_ydelser import borger_ydelser_hent
from q_oioubl_faktura_parser.functionality.parser import OIOUBLParser
from q_prisme365_api.api_client import initialiser_prisme
from q_prisme365_api.functionality.fakturaer import (
    approve_faktura,
    delegate_faktura,
    search_fakturaer,
    update_faktura_beskrivelse,
)
from q_prisme365_api.functionality.faktura_kontering import konter_faktura

from fakturadata import (
    find_beloeb_uden_moms,
    find_entydigt_cpr,
    find_periode_og_leveringsdato,
)
from konfiguration import (
    AFDELING,
    BELOEBSTOLERANCE,
    CURA_CREDENTIAL,
    ENABLE_CURA_LUKNING,
    ENABLE_GODKENDELSE,
    GODKENDER_1,
    KONTOSTRENG,
    MANUEL_PRISME_BRUGER,
    MANUEL_PRISME_KOMMENTAR,
    PRISME_CREDENTIAL,
    PRISME_DOMAIN_SUFFIX,
    STANDARD_ENHED,
    STATUS_CODE_MANUEL_BELOEB,
    STATUS_CODE_MANUEL_BORGER,
    STATUS_CODE_MANUEL_CPR,
    STATUS_CODE_MANUEL_OIOUBL,
    STATUS_CODE_MANUEL_PERIODE,
    STATUS_CODE_MANUEL_YDELSE,
    STATUS_MANUEL,
    UDFOER_KONTERING,
)
from luk_ydelse_i_cura import luk_ydelse_i_cura
from ydelsesmatch import beloeb_matcher, find_matchende_ydelser, validate_entydig_ydelse

logger = logging.getLogger(__name__)


class States:
    HENT_FAKTURA = "1.0 Faktura og dokumenter hentet"
    LAES_OIOUBL = "1.1 OIOUBL læst"
    FIND_BORGER = "1.2 Borger identificeret på faktura"
    FIND_FAKTURAPERIODE = "1.3 Fakturaperiode fastlagt"
    FIND_BORGER_I_CURA = "2.0 Borger fundet i CURA"
    HENT_CURA_YDELSER = "2.1 Ydelser hentet fra CURA"
    MATCH_CURA_YDELSE = "2.2 Entydig ydelse matchet"
    KONTROLLER_BELOEB = "2.3 Fakturabeløb og ydelsesbeløb matcher"
    BYG_KONTERINGSLINJER = "3.0 Konteringslinjer bygget"
    KONTER_FAKTURA = "3.1 Faktura konteret"
    GODKEND_FAKTURA = "4.0 Faktura godkendt"
    LUK_YDELSE_I_CURA = "5.1 Ydelse lukket i CURA"
    MANUEL_OIOUBL = "8.1 Manuel - OIOUBL kunne ikke findes entydigt"
    MANUEL_CPR = "8.2 Manuel - CPR er ikke entydigt"
    MANUEL_BORGER = "8.3 Manuel - Borger ikke fundet i CURA"
    MANUEL_PERIODE = "8.4 Manuel - Fakturaperiode kunne ikke fastlægges"
    MANUEL_YDELSE = "8.5 Manuel - CURA-ydelse er ikke entydig"
    MANUEL_BELOEB = "8.6 Manuel - Fakturabeløb matcher ikke CURA-takst"


async def behandel_page(item, session=None, page=None):
    """Behandl én faktura. Returnér None eller manuel status til main.py."""
    from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data

    data = item.data
    if not isinstance(data, dict):
        raise TypeError("item.data skal være en dictionary.")
    data.setdefault("box", {})
    data.setdefault("state", [])
    box = data["box"]
    if not isinstance(box, dict) or not isinstance(data["state"], list):
        raise TypeError("box skal være en dictionary og state skal være en liste.")

    header_reference = str(box.get("header_reference") or "").strip()
    if not header_reference:
        raise ValueError("Itemet mangler header_reference i box.")

    def har_state(state: str) -> bool:
        return any(state in str(existing) for existing in data.get("state", []))

    def set_state(state: str) -> None:
        update_item_data(data, item=item, state=state)

    def save_box() -> None:
        update_item_data(data, item=item)

    def log_step(
        step: str,
        text: str,
    ) -> None:
        """
        Skriver procestrinnet både til loggeren og
        direkte i terminalen.

        Output:
            Funktionen returnerer ikke data.
        """
        logger.info(
            "[%s] %s",
            step,
            text,
        )

        print(
            f"[PROCESS] {step}: {text}"
        )

    def manuel_behandling(state: str, status_code: str, note: str):
        log_step("MANUEL", note)
        box["manuel_aarsag"] = note
        rec_id = box.get("rec_id_loc")
        if rec_id in (None, ""):
            raise ValueError("RecIdLoc mangler før manuel behandling.")
        update_faktura_beskrivelse(
            rec_id_loc=int(rec_id),
            fakturabeskrivelse=("Robot: " + note)[:250],
            verificer=True,
        )
        delegate_faktura(
            rec_id_loc=int(rec_id),
            afdeling=AFDELING,
            til_bruger=MANUEL_PRISME_BRUGER,
            kommentar=MANUEL_PRISME_KOMMENTAR,
            verificer=True,
        )
        save_box()
        set_state(state)
        return {"status": STATUS_MANUEL, "status_code": status_code}

    initialiser_prisme(PRISME_CREDENTIAL)
    set_cura_credential(CURA_CREDENTIAL)

    try:
        faktura = _hent_faktura_med_dokumenter(header_reference)
    except (ValueError, LookupError) as error:
        if box.get("rec_id_loc"):
            return manuel_behandling(States.MANUEL_OIOUBL, STATUS_CODE_MANUEL_OIOUBL, str(error))
        raise

    box["rec_id_loc"] = faktura.get("RecIdLoc")

    document_path = str(
        box.get(
            "dokumentsti",
            "",
        )
        or ""
    ).strip()

    # Reserve til ældre queue-items, hvor
    # dokumentstien ikke ligger i box.
    if not document_path:
        document = faktura.get(
            "OIOUBL-dokument"
        )

        if isinstance(
            document,
            dict,
        ):
            document_path = str(
                document.get(
                    "Dokumentsti",
                    "",
                )
                or ""
            ).strip()

    if not document_path:
        return manuel_behandling(
            States.MANUEL_OIOUBL,
            STATUS_CODE_MANUEL_OIOUBL,
            "Dokumentstien til OIOUBL mangler.",
        )

    box["dokumentsti"] = document_path

    save_box()
    set_state(States.HENT_FAKTURA)

    parsed_invoice = OIOUBLParser().parse(document_path)
    set_state(States.LAES_OIOUBL)

    try:
        cpr = find_entydigt_cpr(parsed_invoice)
    except ValueError as error:
        return manuel_behandling(States.MANUEL_CPR, STATUS_CODE_MANUEL_CPR, str(error))
    box["cpr"] = cpr
    save_box()
    set_state(States.FIND_BORGER)

    try:
        period = find_periode_og_leveringsdato(parsed_invoice)
    except ValueError as error:
        return manuel_behandling(States.MANUEL_PERIODE, STATUS_CODE_MANUEL_PERIODE, str(error))
    box.update({key: _serialize(value) for key, value in period.items()})
    save_box()
    set_state(States.FIND_FAKTURAPERIODE)

    citizen = get_borger_by_cpr(cpr)
    if not citizen.get("findes_borger_i_cura"):
        return manuel_behandling(
            States.MANUEL_BORGER,
            STATUS_CODE_MANUEL_BORGER,
            "Borgeren blev ikke fundet i CURA.",
        )
    borger_id = str(citizen.get("borger_id") or "").strip()
    box["borger_id"] = borger_id
    save_box()
    set_state(States.FIND_BORGER_I_CURA)

    services_result = borger_ydelser_hent(borger_id=borger_id, raw=False)
    services = services_result.get("ydelser", [])
    box["antal_cura_ydelser"] = len(services)
    save_box()
    set_state(States.HENT_CURA_YDELSER)

    supplier_name = str(box.get("leverandoer_matchnavn") or box.get("leverandoernavn") or "").strip()
    matches = find_matchende_ydelser(
        ydelser=services,
        leverandoernavn=supplier_name,
        periode_start=period["fakturaperiode_start"],
        periode_slut=period["fakturaperiode_slut"],
    )
    box["antal_matchende_ydelser"] = len(matches)
    save_box()
    try:
        service = validate_entydig_ydelse(matches)
    except ValueError as error:
        return manuel_behandling(States.MANUEL_YDELSE, STATUS_CODE_MANUEL_YDELSE, str(error))

    box.update({
        "cura_ydelse_id": service.get("id", ""),
        "cura_ydelsesnavn": service.get("ydelsesnavn", ""),
        "cura_ydelse_startdato": service.get("startdato", ""),
        "cura_ydelse_slutdato": service.get("slutdato", ""),
        "cura_ydelse_takst": str(service.get("takst", "")),
    })
    save_box()
    set_state(States.MATCH_CURA_YDELSE)

    invoice_net_amount = find_beloeb_uden_moms(parsed_invoice)
    box["fakturabeloeb_uden_moms"] = str(invoice_net_amount)
    save_box()
    if not beloeb_matcher(invoice_net_amount, service.get("takst"), BELOEBSTOLERANCE):
        return manuel_behandling(
            States.MANUEL_BELOEB,
            STATUS_CODE_MANUEL_BELOEB,
            f"Fakturabeløb uden moms {invoice_net_amount} matcher ikke CURA-takst {service.get('takst')}.",
        )
    set_state(States.KONTROLLER_BELOEB)

    gross_amount = Decimal(str(faktura.get("Importeret fakturabeløb") or invoice_net_amount))
    accounting_lines = [{
        "RecIdLoc": int(box["rec_id_loc"]),
        "Kontostreng": KONTOSTRENG,
        "Bruttobeløb": gross_amount,
        "Ydelsesmodtager": cpr,
        "Enhed": STANDARD_ENHED,
        "Afdeling fakturaen er tilknyttet": str(faktura.get("Afdeling") or AFDELING),
        "Posteringstekst": (f"Hjælpemiddel - {supplier_name}")[:60],
        "Kreditorkonto": str(faktura.get("Kreditorkonto") or ""),
    }]
    set_state(States.BYG_KONTERINGSLINJER)

    if not har_state(States.KONTER_FAKTURA):
        konter_faktura(
            header_reference=header_reference,
            konteringslinjer=accounting_lines,
            cpr_numre_valideret=True,
            udfoer=UDFOER_KONTERING,
        )
        set_state(States.KONTER_FAKTURA)

    if ENABLE_GODKENDELSE and not har_state(States.GODKEND_FAKTURA):
        approve_faktura(
            rec_id_loc=int(box["rec_id_loc"]),
            fakturabeloeb=gross_amount,
            konteringslinjer_totalbeloeb=gross_amount,
            godkender_1=GODKENDER_1,
            afdeling=AFDELING,
        )
        set_state(States.GODKEND_FAKTURA)


    # ==========================================================
    # LUK YDELSE I CURA
    # ==========================================================
    if ENABLE_CURA_LUKNING:
        if not har_state(
            States.LUK_YDELSE_I_CURA
        ):
            log_step(
                "LUK_YDELSE_I_CURA",
                "Starter afslutning af ydelsen i CURA",
            )

            resultat_cura_lukning = (
                await luk_ydelse_i_cura(
                    page=page,
                    session=session,
                    borger_id=borger_id,
                    ydelsesnavn=str(
                        service.get(
                            "ydelsesnavn",
                            "",
                        )
                        or ""
                    ).strip(),
                    leverandoernavn=str(
                        box.get(
                            "leverandoernavn",
                            "",
                        )
                        or ""
                    ).strip(),
                )
            )

            if not isinstance(
                resultat_cura_lukning,
                dict,
            ):
                raise RuntimeError(
                    "luk_ydelse_i_cura returnerede "
                    "ikke en dictionary."
                )

            cura_status = str(
                resultat_cura_lukning.get(
                    "status",
                    "",
                )
                or ""
            ).strip()

            if cura_status.casefold() != "afsluttet":
                raise RuntimeError(
                    "CURA bekræftede ikke, at "
                    "ydelsen blev afsluttet. "
                    f"Resultat: "
                    f"{resultat_cura_lukning!r}"
                )

            box[
                "cura_lukning_udfoert"
            ] = True

            box[
                "cura_lukning_slutdato"
            ] = resultat_cura_lukning.get(
                "slutdato",
                "",
            )

            save_box()

            set_state(
                States.LUK_YDELSE_I_CURA
            )

            log_step(
                "LUK_YDELSE_I_CURA",
                (
                    "Ydelsen blev afsluttet i CURA "
                    f"med slutdato "
                    f"{box['cura_lukning_slutdato']}"
                ),
            )

        else:
            log_step(
                "LUK_YDELSE_I_CURA",
                (
                    "Springer over, da ydelsen "
                    "allerede er registreret som "
                    "lukket i state"
                ),
            )

    else:
        log_step(
            "LUK_YDELSE_I_CURA",
            (
                "CURA-lukning er slået fra i "
                "konfiguration.py"
            ),
        )

    # Ingen særlig slutstatus.
    # main.py markerer itemet som færdigt.
    return None


def _hent_faktura_med_dokumenter(header_reference: str) -> dict[str, Any]:
    invoices = search_fakturaer(
        header_reference=header_reference,
        hent_detaljer=True,
        hent_dokumenter=True,
        hent_dokumentplacering=True,
        dokument_domain_suffix=PRISME_DOMAIN_SUFFIX,
        top=2,
    )
    if len(invoices) != 1:
        raise LookupError(f"Fakturaopslaget var ikke entydigt. Antal: {len(invoices)}.")
    return invoices[0]


def _serialize(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value
