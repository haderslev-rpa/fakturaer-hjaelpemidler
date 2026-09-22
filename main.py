import asyncio
import logging
import os
import sys
from pprint import pprint

from behandel import behandel_page
from populate_queue import hent_queue_items

from q_cura.functionality.launch import (launch_cura,)

from automation_server_client import (
    AutomationServer,
    Workqueue,
    WorkItemError,
    WorkItemStatus,
)
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from q_haderslev_vbo.automation_server.ats_is_item_in_queue import is_item_in_queue
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from konfiguration import ENABLE_CURA_LUKNING, QUEUE_ID, configure_logging


def get_headless_flag():
    """Læs HEADLESS fra miljøet. Standard er true."""
    return os.getenv("HEADLESS", "true").lower() == "true"


configure_logging()


async def populate_queue(
    workqueue: Workqueue,
    debug: bool,
):
    """
    Hent fakturaer fra populate_queue.py og tilføj
    nye fakturaer til Automation Server-køen.

    Output:
        Funktionen returnerer ikke data.

        Funktionen printer en samlet optælling over:
        - klargjorte fakturaer
        - tilføjede items
        - eksisterende items
    """
    logger = logging.getLogger(
        __name__
    )

    logger.info(
        "Populate queue mode started "
        "(debug=%s)",
        debug,
    )

    print()
    print("=" * 80)
    print("STARTER QUEUE-KØRSEL")
    print("=" * 80)

    raw_items = hent_queue_items()

    print()
    print(
        "Antal klargjorte fakturaer:",
        len(raw_items),
    )

    antal_tilfoejet = 0
    antal_findes_i_koe = 0

    for raw_item in raw_items:
        data_json = {}

        update_item_data(
            data_json,
            box_updates=raw_item,
            update=False,
        )

        item_reference = (
            f"{raw_item['header_reference']} | "
            f"{raw_item['fakturanr']} | "
            f"{raw_item['leverandoernavn']}"
        )

        if is_item_in_queue(
            queue_id=QUEUE_ID,
            item_reference=item_reference,
            new=True,
            in_progress=True,
            completed=True,
            failed=True,
            pending_user_action=True,
            updated_at=False,
        ):
            antal_findes_i_koe += 1

            print(
                "Springer over, fordi itemet "
                "allerede findes:",
                item_reference,
            )

            continue

        workqueue.add_item(
            data=data_json,
            reference=item_reference,
        )

        antal_tilfoejet += 1

        print(
            "Tilføjet til køen:",
            item_reference,
        )

    print()
    print("=" * 80)
    print("QUEUE-KØRSEL AFSLUTTET")
    print("=" * 80)
    print(
        "Klargjort i alt:",
        len(raw_items),
    )
    print(
        "Tilføjet til køen:",
        antal_tilfoejet,
    )
    print(
        "Fandtes allerede:",
        antal_findes_i_koe,
    )


async def process_workqueue(
    workqueue: Workqueue,
    debug: bool,
):
    """
    Behandler fakturaer fra Automation Server-køen.

    Der oprettes én fælles browsersession for hele
    kørslen.

    Hvis CURA-lukning er aktiveret, åbnes og
    initialiseres CURA, før første item behandles.

    Output:
        Funktionen returnerer ikke data.

        Hvert item afsluttes enten som:
        - Completed
        - Manuel behandling
        - Failed
    """
    logger = logging.getLogger(
        __name__
    )

    logger.info(
        "Process workqueue mode started "
        "(debug=%s)",
        debug,
    )

    headless = get_headless_flag()

    session = BrowserSession(
        headless=headless,
        debug=debug,
    )

    await session.start()

    page = await session.new_page()

    try:
        # CURA åbnes kun, når processen faktisk
        # skal afslutte ydelser.
        if ENABLE_CURA_LUKNING:
            print()
            print(
                "[PROCESS] Åbner og initialiserer CURA"
            )

            await launch_cura(
                page=page,
                session=session,
            )

            print(
                "[PROCESS] CURA er initialiseret"
            )

        else:
            print()
            print(
                "[PROCESS] CURA-lukning er slået fra"
            )

        # Workqueue er iterable.
        # Hvert item behandles ét ad gangen.
        for item in workqueue:
            with item:
                data = item.data

                try:
                    print()
                    print(
                        "=" * 80
                    )
                    print(
                        "NEXT ITEM"
                    )
                    print(
                        "=" * 80
                    )

                    pprint(
                        data
                    )

                    resultat = await behandel_page(
                        item=item,
                        session=session,
                        page=page,
                    )

                    # Genindlæs itemdata efter behandling.
                    data = item.data

                    if resultat:
                        status = resultat.get(
                            "status",
                            "Completed",
                        )

                        status_code = resultat.get(
                            "status_code",
                            "Færdig",
                        )

                    else:
                        status = "Completed"
                        status_code = "Færdig"

                    update_item_data(
                        data,
                        item=item,
                        status=status,
                        status_code=status_code,
                        state="Completed",
                    )

                    item.update(
                        data
                    )

                    item.complete(
                        status
                    )

                    print()
                    print(
                        "[PROCESS] Item afsluttet"
                    )
                    print(
                        "Reference:",
                        item.reference,
                    )
                    print(
                        "Status:",
                        status,
                    )
                    print(
                        "Statuskode:",
                        status_code,
                    )

                except WorkItemError as error:
                    # Soft error:
                    # Itemet fejler, men processen kan
                    # fortsætte med næste item.
                    logger.error(
                        "WorkItemError for item %s: %s",
                        item.reference,
                        error,
                    )

                    item.fail(
                        str(error)
                    )

                    print()
                    print(
                        "[PROCESS] Item fejlede med "
                        "WorkItemError"
                    )
                    print(
                        "Reference:",
                        item.reference,
                    )
                    print(
                        "Fejl:",
                        str(error),
                    )

                    # Luk den eksisterende browser og
                    # opret en ny ren browsersession.
                    await session.close()

                    session = BrowserSession(
                        headless=headless,
                        debug=debug,
                    )

                    await session.start()

                    page = await session.new_page()

                    # Den nye browser skal logges ind i
                    # CURA igen, hvis lukning er aktiveret.
                    if ENABLE_CURA_LUKNING:
                        print(
                            "[PROCESS] Geninitialiserer CURA"
                        )

                        await launch_cura(
                            page=page,
                            session=session,
                        )

                        print(
                            "[PROCESS] CURA er "
                            "geninitialiseret"
                        )

                except Exception as error:
                    # Hard error:
                    # Tag screenshot, luk browseren og
                    # stop processen.
                    logger.exception(
                        "Uventet fejl"
                    )

                    try:
                        if (
                            session.context
                            and session.context.pages
                        ):
                            page = (
                                session.context.pages[-1]
                            )

                            await session.screenshot(
                                page,
                                (
                                    "hard_exception_"
                                    f"{type(error).__name__}"
                                ),
                                always=True,
                            )

                    except Exception:
                        logger.warning(
                            "Kunne ikke tage screenshot "
                            "ved hard error"
                        )

                    raise

    finally:
        # Browsersessionen lukkes både ved normal
        # afslutning og ved uventede fejl.
        await session.close()

        print()
        print(
            "[PROCESS] Browser-session lukket korrekt"
        )


if __name__ == "__main__":
    DEBUG = "--debug" in sys.argv
    QUEUE_MODE = "--queue" in sys.argv

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    if QUEUE_MODE:
        # Bevidst kommenteret ud, så eksisterende NEW-items ikke slettes.
        # workqueue.clear_workqueue(WorkItemStatus.NEW)
        asyncio.run(populate_queue(workqueue, debug=DEBUG))
        sys.exit(0)

    asyncio.run(process_workqueue(workqueue, debug=DEBUG))
