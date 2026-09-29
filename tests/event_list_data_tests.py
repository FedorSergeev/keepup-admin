"""The event list answers with the event's data (keepup-57).

The list put the event's *text*, parsed as JSON, into `event_data` and did not
answer with the stored data at all. The data is now in `data`; `event_data`
answers as before, deprecated.

    python3 -m pytest keepup/tests/event_list_data_tests.py -v
"""

import asyncio

import pytest

from keepup import events
from keepup.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()
    events.init_event_manager()


def listed(event_type):
    answer = asyncio.run(events.event_manager.get_events(event_type=event_type))
    return answer["events"]


def test_the_event_s_data_is_in_the_answer():
    asyncio.run(events.event_manager.create_event(
        event_type="list_data_probe", event_text="Order 12 paid",
        event_data={"order_id": 12, "amount": "9.90"}))
    event = listed("list_data_probe")[0]
    assert event["data"] == {"order_id": 12, "amount": "9.90"}
    # Unchanged for whoever still reads it: the text, parsed when it can be.
    assert event["event_data"] == "Order 12 paid"


def test_an_event_without_data_answers_none():
    asyncio.run(events.event_manager.create_event(
        event_type="list_no_data_probe", event_text="nothing attached"))
    assert listed("list_no_data_probe")[0]["data"] is None
