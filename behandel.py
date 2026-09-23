"""Fakturaer - Hjælpemidler. Behandler ét ATS-item ad gangen."""
from __future__ import annotations

import logging
from datetime import date
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
    update_faktura_bogfoeringsdato,
)
from q_prisme365_api.functionality.faktura_kontering import konter_faktura

from fakturadata import (
    beregn_alder_fra_cpr,
    find_entydigt_cpr,
    find_fakturabeloeb,
    find_periode_og_leveringsdato,
)
from kontostrengsopslag import find_kontostreng
from konfiguration import (
    AFDELING, BELOEBSTOLERANCE, CURA_CREDENTIAL, ENABLE_CURA_LUKNING,
    ENABLE_GODKENDELSE, GODKENDER_1, MAKS_FAKTURABELOEB_UDEN_MOMS,
    MANUEL_PRISME_BRUGER, MANUEL_PRISME_KOMMENTAR, PRISME_CREDENTIAL,
    PRISME_DOMAIN_SUFFIX, STANDARD_ENHED, STATUS_CODE_MANUEL_BELOEB,
    STATUS_CODE_MANUEL_BORGER, STATUS_CODE_MANUEL_CPR,
    STATUS_CODE_MANUEL_KONTOSTRENG, STATUS_CODE_MANUEL_OIOUBL,
    STATUS_CODE_MANUEL_PERIODE, STATUS_CODE_MANUEL_YDELSE,
    STATUS_MANUEL, UDFOER_KONTERING,
)
from luk_ydelse_i_cura import luk_ydelse_i_cura
from ydelsesmatch import (
    find_organisation_ids,
    find_matchende_ydelser,
    validate_entydig_ydelse,
)

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
    FIND_KONTOSTRENG = "2.4 Kontostreng fundet"
    OPDATER_BOGFOERINGSDATO = "2.5 Bogføringsdato opdateret"
    BYG_KONTERINGSLINJER = "3.0 Konteringslinjer bygget"
    KONTER_FAKTURA = "3.1 Faktura konteret"
    GODKEND_FAKTURA = "4.0 Faktura godkendt"
    LUK_YDELSE_I_CURA = "5.1 Ydelse lukket i CURA"
    MANUEL_OIOUBL = "8.1 Manuel - OIOUBL kunne ikke findes entydigt"
    MANUEL_CPR = "8.2 Manuel - CPR er ikke entydigt"
    MANUEL_BORGER = "8.3 Manuel - Borger ikke fundet i CURA"
    MANUEL_PERIODE = "8.4 Manuel - Fakturaperiode kunne ikke fastlægges"
    MANUEL_YDELSE = "8.5 Manuel - CURA-ydelse er ikke entydig"
    MANUEL_BELOEB = "8.6 Manuel - Fakturabeløb kræver manuel behandling"
    MANUEL_KONTOSTRENG = "8.7 Manuel - Kontostreng kunne ikke findes entydigt"


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
        return any(state in str(value) for value in data.get("state", []))

    def set_state(state: str) -> None:
        update_item_data(data, item=item, state=state)

    def save_box() -> None:
        update_item_data(data, item=item)

    def log_step(step: str, text: str) -> None:
        logger.info("[%s] %s", step, text)

    def manuel_behandling(state: str, status_code: str, note: str):
        logger.warning("Manuel behandling: %s", note)
        box["manuel_aarsag"] = note
        rec_id_loc = box.get("rec_id_loc")
        if rec_id_loc in (None, ""):
            raise ValueError("RecIdLoc mangler før manuel behandling.")
        update_faktura_beskrivelse(
            rec_id_loc=int(rec_id_loc),
            fakturabeskrivelse=("Robot: " + note)[:250],
            verificer=True,
        )
        delegate_faktura(
            rec_id_loc=int(rec_id_loc),
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
    logger.info("Starter behandling af faktura %s", header_reference)

    try:
        faktura = _hent_faktura_med_dokumenter(header_reference)
    except (ValueError, LookupError) as error:
        if box.get("rec_id_loc") not in (None, ""):
            return manuel_behandling(States.MANUEL_OIOUBL, STATUS_CODE_MANUEL_OIOUBL, str(error))
        raise

    box["rec_id_loc"] = faktura.get("RecIdLoc")
    document_path = str(box.get("dokumentsti") or "").strip()
    if not document_path:
        document = faktura.get("OIOUBL-dokument")
        if isinstance(document, dict):
            document_path = str(document.get("Dokumentsti") or "").strip()
    if not document_path:
        return manuel_behandling(
            States.MANUEL_OIOUBL, STATUS_CODE_MANUEL_OIOUBL,
            "Dokumentstien til OIOUBL mangler.",
        )
    box["dokumentsti"] = document_path
    save_box()
    set_state(States.HENT_FAKTURA)

    parsed_invoice = OIOUBLParser().parse(document_path)
    set_state(States.LAES_OIOUBL)
    header = parsed_invoice.get("header", {})
    if not isinstance(header, dict):
        header = {}

    leverandoernavn_prisme = str(
        box.get("leverandoernavn") or faktura.get("Leverandørnavn") or ""
    ).strip()
    leverandoernavn_oioubl = str(header.get("supplier_name") or "").strip()
    if not leverandoernavn_prisme and not leverandoernavn_oioubl:
        return manuel_behandling(
            States.MANUEL_YDELSE, STATUS_CODE_MANUEL_YDELSE,
            "Leverandørnavnet mangler både i Prisme og OIOUBL.",
        )
    box["leverandoernavn_prisme"] = leverandoernavn_prisme
    box["leverandoernavn_oioubl"] = leverandoernavn_oioubl
    save_box()

    try:
        cpr = find_entydigt_cpr(parsed_invoice)
    except ValueError as error:
        return manuel_behandling(States.MANUEL_CPR, STATUS_CODE_MANUEL_CPR, str(error))
    box["cpr"] = cpr
    box["borger_alder"] = beregn_alder_fra_cpr(cpr)
    save_box()
    set_state(States.FIND_BORGER)

    try:
        period = find_periode_og_leveringsdato(parsed_invoice)
    except ValueError as error:
        return manuel_behandling(States.MANUEL_PERIODE, STATUS_CODE_MANUEL_PERIODE, str(error))
    box.update({key: _serialize(value) for key, value in period.items()})
    save_box()
    set_state(States.FIND_FAKTURAPERIODE)

    try:
        amounts = find_fakturabeloeb(parsed_invoice=parsed_invoice, faktura=faktura)
    except ValueError as error:
        return manuel_behandling(States.MANUEL_BELOEB, STATUS_CODE_MANUEL_BELOEB, str(error))

    net_amount = amounts["fakturabeloeb_netto"]
    vat_amount = amounts["fakturabeloeb_moms"]
    gross_amount = amounts["fakturabeloeb_brutto"]
    box["fakturabeloeb_netto"] = str(net_amount)
    box["fakturabeloeb_moms"] = str(vat_amount)
    box["fakturabeloeb_brutto"] = str(gross_amount)
    save_box()

    if net_amount > MAKS_FAKTURABELOEB_UDEN_MOMS:
        return manuel_behandling(
            States.MANUEL_BELOEB, STATUS_CODE_MANUEL_BELOEB,
            f"Fakturabeløbet uden moms er {net_amount} kr. og overstiger "
            f"{MAKS_FAKTURABELOEB_UDEN_MOMS} kr.",
        )

    citizen = get_borger_by_cpr(cpr)
    if not citizen.get("findes_borger_i_cura"):
        return manuel_behandling(
            States.MANUEL_BORGER, STATUS_CODE_MANUEL_BORGER,
            "Borgeren blev ikke fundet i CURA.",
        )
    borger_id = str(citizen.get("borger_id") or "").strip()
    if not borger_id:
        return manuel_behandling(
            States.MANUEL_BORGER, STATUS_CODE_MANUEL_BORGER,
            "Borgerens CURA-id mangler.",
        )
    box["borger_id"] = borger_id
    save_box()
    set_state(States.FIND_BORGER_I_CURA)

    services_result = borger_ydelser_hent(borger_id=borger_id, raw=False)
    services = _extract_services(services_result)
    set_state(States.HENT_CURA_YDELSER)

    try:
        organization_result = find_organisation_ids(
            leverandoernavn_prisme=leverandoernavn_prisme,
            leverandoernavn_oioubl=leverandoernavn_oioubl,
        )
    except (ValueError, TypeError) as error:
        return manuel_behandling(
            States.MANUEL_YDELSE, STATUS_CODE_MANUEL_YDELSE, str(error)
        )

    supplier_ids = organization_result["leverandoer_organization_ids"]
    free_choice_id = organization_result["frit_valg_organization_id"]
    matches = find_matchende_ydelser(
        ydelser=services,
        leverandoer_organization_ids=supplier_ids,
        frit_valg_organization_id=free_choice_id,
        leveringsdato=period["leveringsdato"],
        fakturabeloeb=net_amount,
        tolerance=BELOEBSTOLERANCE,
    )
    try:
        service = validate_entydig_ydelse(matches)
    except ValueError as error:
        return manuel_behandling(
            States.MANUEL_YDELSE, STATUS_CODE_MANUEL_YDELSE, str(error)
        )

    box["cura_ydelse_startdato"] = service.get("startdato", "")
    box["cura_ydelse_slutdato"] = service.get("slutdato", "")
    box["cura_ydelse_beloeb"] = str(service.get("bevilling_beloeb", ""))
    box["cura_frit_valg"] = bool(service.get("frit_valg"))
    box["beloebsdifference"] = str(service.get("beloebsdifference", ""))
    box["seneste_leveringsdato"] = service.get("seneste_leveringsdato", "")
    save_box()
    set_state(States.MATCH_CURA_YDELSE)
    set_state(States.KONTROLLER_BELOEB)

    try:
        service_date = date.fromisoformat(str(service.get("startdato") or "")[:10])
        account = find_kontostreng(
            ydelsesnavn=str(service.get("ydelsesnavn") or ""),
            alder=int(box["borger_alder"]),
            ydelsesdato=service_date,
            fakturadato=period.get("fakturadato"),
        )
    except (ValueError, TypeError) as error:
        return manuel_behandling(
            States.MANUEL_KONTOSTRENG, STATUS_CODE_MANUEL_KONTOSTRENG, str(error)
        )

    box["excel_ark"] = account.get("excel_ark", "")
    box["excel_tekst"] = account.get("excel_tekst", "")
    box["kontostreng"] = account.get("kontostreng", "")
    box["bogfoeringsdato"] = _serialize(account.get("bogfoeringsdato"))
    save_box()
    set_state(States.FIND_KONTOSTRENG)

    if not har_state(States.OPDATER_BOGFOERINGSDATO):
        update_faktura_bogfoeringsdato(
            header_reference=header_reference,
            bogfoeringsdato=account["bogfoeringsdato"],
            verificer=True,
        )
        set_state(States.OPDATER_BOGFOERINGSDATO)

    paragraph = str(service.get("paragraf") or "").strip()
    service_name = str(service.get("ydelsesnavn") or "").strip()
    posting_text = " ".join(
        value for value in (paragraph, service_name) if value
    )[:60]
    accounting_lines = [{
        "RecIdLoc": int(box["rec_id_loc"]),
        "Kontostreng": account["kontostreng"],
        "Bruttobeløb": gross_amount,
        "Ydelsesmodtager": cpr,
        "Enhed": STANDARD_ENHED,
        "Afdeling fakturaen er tilknyttet": str(faktura.get("Afdeling") or AFDELING),
        "Posteringstekst": posting_text,
        "Kreditorkonto": str(faktura.get("Kreditorkonto") or ""),
    }]
    box["posteringstekst"] = posting_text
    save_box()
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

    if ENABLE_CURA_LUKNING and not har_state(States.LUK_YDELSE_I_CURA):
        result = await luk_ydelse_i_cura(
            page=page,
            session=session,
            borger_id=borger_id,
            ydelsesnavn=service_name,
            leverandoernavn=str(
                box.get("leverandoernavn")
                or leverandoernavn_prisme
                or leverandoernavn_oioubl
                or ""
            ).strip(),
        )
        if not isinstance(result, dict) or str(result.get("status") or "").casefold() != "afsluttet":
            raise RuntimeError(
                "CURA bekræftede ikke afslutningen. "
                f"Resultat: {result!r}"
            )
        set_state(States.LUK_YDELSE_I_CURA)

    logger.info("Faktura %s er behandlet automatisk", header_reference)
    return None


def _extract_services(result: Any) -> list[dict[str, Any]]:
    """Returnér den normaliserede ydelsesliste fra raw=False-resultatet."""
    if isinstance(result, dict) and isinstance(result.get("ydelser"), list):
        return result["ydelser"]
    raise TypeError(
        "borger_ydelser_hent(raw=False) returnerede ikke en dictionary "
        "med en liste i feltet 'ydelser'."
    )


def _hent_faktura_med_dokumenter(header_reference: str) -> dict[str, Any]:
    """Hent præcis én faktura med detaljer og dokumenter."""
    invoices = search_fakturaer(
        header_reference=header_reference,
        hent_detaljer=True,
        hent_dokumenter=True,
        hent_dokumentplacering=True,
        dokument_domain_suffix=PRISME_DOMAIN_SUFFIX,
        top=2,
    )
    if not isinstance(invoices, list):
        raise TypeError("search_fakturaer skal returnere en liste.")
    if len(invoices) != 1:
        raise LookupError(
            f"Fakturaopslaget var ikke entydigt. Antal: {len(invoices)}."
        )
    return invoices[0]


def _serialize(value: Any) -> Any:
    """Konvertér eksempelvis date til ISO-tekst."""
    return value.isoformat() if hasattr(value, "isoformat") else value
