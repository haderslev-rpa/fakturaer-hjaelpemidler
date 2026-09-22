"""Fælles konfiguration for Fakturaer - Hjælpemidler."""
from __future__ import annotations

import logging
from decimal import Decimal

UDFOER_KONTERING = True
ENABLE_GODKENDELSE = True
ENABLE_CURA_LUKNING = True

PRISME_CREDENTIAL = "API_PRISME365_2"
PRISME_DOMAIN_SUFFIX = "prisme-365.dk"
CURA_CREDENTIAL = "API_CURA"
MANUEL_PRISME_BRUGER = "DISWP"
GODKENDER_1 = "DIRXFB"  # Ret hvis en anden robotgodkender skal bruges.
MANUEL_PRISME_KOMMENTAR = (
    "Flyttet til manuel behandling af fakturaer-hjaelpemidler"
)

QUEUE_ID = 15  # Ret til køens ID i Automation Server.
AFDELING = "160403020000"  # Ret til korrekt 12-cifret afdeling.
HJALPEMIDLER_EAN = "5798005224822"

SHAREPOINT_SITE_NAME = "Automatisering"
SHAREPOINT_LEVERANDOER_FILE_PATH = (
    "RPA - Processer/fakturaer-hjaelpemidler/"
    "tilladte_leverandoerer.xlsx"
)


KONTOSTRENG = "160403030000-530311005-40707-29-"  # Ret til korrekt kontostreng.
STANDARD_ENHED = "STK"
BELOEBSTOLERANCE = Decimal("1.00")

STATUS_MANUEL = "Manuel behandling"
STATUS_CODE_MANUEL_OIOUBL = "MANUAL_OIOUBL"
STATUS_CODE_MANUEL_CPR = "MANUAL_CPR"
STATUS_CODE_MANUEL_BORGER = "MANUAL_CITIZEN"
STATUS_CODE_MANUEL_PERIODE = "MANUAL_PERIOD"
STATUS_CODE_MANUEL_YDELSE = "MANUAL_SERVICE"
STATUS_CODE_MANUEL_BELOEB = "MANUAL_AMOUNT"

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging() -> None:
    logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
    for name in ("httpx", "httpcore", "automation_server_client", "debugpy"):
        logging.getLogger(name).setLevel(logging.WARNING)
