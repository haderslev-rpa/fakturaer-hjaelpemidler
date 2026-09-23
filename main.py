import asyncio
import logging
import os
import sys
from pprint import pprint

from automation_server_client import AutomationServer, Workqueue, WorkItemError
from q_cura.functionality.launch import launch_cura
from q_haderslev_vbo.automation_server.ats_is_item_in_queue import is_item_in_queue
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data
from q_haderslev_vbo.playwright.browser_session import BrowserSession

from behandel import behandel_page
from konfiguration import ENABLE_CURA_LUKNING, QUEUE_ID, configure_logging
from populate_queue import hent_queue_items


def get_headless_flag():
    return os.getenv("HEADLESS", "true").lower() == "true"


configure_logging()


async def populate_queue(workqueue: Workqueue, debug: bool):
    logger = logging.getLogger(__name__)
    logger.info("Populate queue mode started (debug=%s)", debug)
    raw_items = hent_queue_items()
    added = 0
    existing = 0
    for raw_item in raw_items:
        data_json = {}
        update_item_data(data_json, box_updates=raw_item, update=False)
        reference = (
            f"{raw_item['header_reference']} | {raw_item['fakturanr']} | "
            f"{raw_item['leverandoernavn']}"
        )
        if is_item_in_queue(
            queue_id=QUEUE_ID,
            item_reference=reference,
            new=True,
            in_progress=True,
            completed=True,
            failed=True,
            pending_user_action=True,
            updated_at=False,
        ):
            existing += 1
            print("Springer over, findes allerede:", reference)
            continue
        workqueue.add_item(data=data_json, reference=reference)
        added += 1
        print("Tilføjet til køen:", reference)
    print("Klargjort:", len(raw_items), "| Tilføjet:", added, "| Eksisterede:", existing)


async def process_workqueue(workqueue: Workqueue, debug: bool):
    logger = logging.getLogger(__name__)
    headless = get_headless_flag()
    session = BrowserSession(headless=headless, debug=debug)
    await session.start()
    page = await session.new_page()
    try:
        if ENABLE_CURA_LUKNING:
            await launch_cura(page=page, session=session)
        for item in workqueue:
            with item:
                try:
                    print("=" * 80, "NEXT ITEM", "=" * 80, sep="\n")
                    pprint(item.data)
                    result = await behandel_page(item=item, session=session, page=page)
                    data = item.data
                    status = result.get("status", "Completed") if result else "Completed"
                    status_code = result.get("status_code", "Færdig") if result else "Færdig"
                    update_item_data(
                        data, item=item, status=status, status_code=status_code, state="Completed"
                    )
                    item.update(data)
                    item.complete(status)
                except WorkItemError as error:
                    logger.error("WorkItemError for item %s: %s", item.reference, error)
                    item.fail(str(error))
                    await session.close()
                    session = BrowserSession(headless=headless, debug=debug)
                    await session.start()
                    page = await session.new_page()
                    if ENABLE_CURA_LUKNING:
                        await launch_cura(page=page, session=session)
                except Exception as error:
                    logger.exception("Uventet fejl")
                    try:
                        if session.context and session.context.pages:
                            page = session.context.pages[-1]
                            await session.screenshot(
                                page, f"hard_exception_{type(error).__name__}", always=True
                            )
                    except Exception:
                        logger.warning("Kunne ikke tage screenshot ved hard error")
                    raise
    finally:
        await session.close()


if __name__ == "__main__":
    DEBUG = "--debug" in sys.argv
    QUEUE_MODE = "--queue" in sys.argv
    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()
    if QUEUE_MODE:
        asyncio.run(populate_queue(workqueue, debug=DEBUG))
        sys.exit(0)
    asyncio.run(process_workqueue(workqueue, debug=DEBUG))
