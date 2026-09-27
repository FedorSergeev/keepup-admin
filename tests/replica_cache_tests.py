"""The theme and the section catalogue are not read on every request (keepup-45).

Each replica keeps them for a bounded time, drops them the moment it changes
them itself, and drops them when another replica says it changed them. Against
the session's throwaway SQLite database, with a stand-in for the replicas' bus.

    python3 -m pytest keepup/tests/replica_cache_tests.py -v
"""

import asyncio

import pytest

from keepup import cache, modules, notification_bus
from keepup.db import DatabaseManagerV2
from keepup.schema import init_db
from keepup.themes import ACTIVE_THEME_CACHE, config_service


@pytest.fixture(scope="module", autouse=True)
def framework_tables():
    init_db()


@pytest.fixture(autouse=True)
def fresh_caches():
    cache.invalidate_all()
    yield
    cache.invalidate_all()


@pytest.fixture
def queries(monkeypatch):
    """The statements that reached the database, by the table they read."""
    seen = []
    for name in ("execute", "execute_one"):
        real = getattr(DatabaseManagerV2, name)

        def counting(query, params=None, _real=real):
            seen.append(" ".join(str(query).split()))
            return _real(query, params)
        monkeypatch.setattr(DatabaseManagerV2, name, counting)
    return seen


def reads_of(seen, table):
    return [q for q in seen if f"FROM {table}" in q]


def active_theme_reads(seen):
    return [q for q in seen if "FROM visual_themes WHERE is_active = TRUE" in q]


# --- the cache itself -------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_value_is_kept_for_its_lifetime_and_read_again_after():
    clock, loads = Clock(), []
    kept = cache.ReplicaCache("t", ttl=30, clock=clock)
    load = lambda: loads.append(1) or len(loads)  # noqa: E731
    assert kept.get("k", load) == 1
    clock.now += 29
    assert kept.get("k", load) == 1
    clock.now += 2
    assert kept.get("k", load) == 2


def test_a_failed_load_is_not_kept():
    kept = cache.ReplicaCache("t", ttl=30)

    def broken():
        raise RuntimeError("database is down")
    with pytest.raises(RuntimeError):
        kept.get("k", broken)
    assert kept.get("k", lambda: "fresh") == "fresh"


def test_the_configured_lifetime_reaches_existing_caches():
    existing = cache.get_cache("lifetime-check")
    try:
        cache.set_ttl(5)
        assert existing.ttl == 5 and cache.get_cache("another").ttl == 5
        cache.set_ttl(None)                         # "as before"
        assert existing.ttl == 5
    finally:
        cache.set_ttl(cache.DEFAULT_TTL_SECONDS)


# --- the active theme ---------------------------------------------------------------

def test_the_active_theme_is_read_once_for_many_pages(queries):
    for _ in range(5):
        config_service.get_active_theme_page_file()
    assert len(active_theme_reads(queries)) == 1


def test_activating_a_theme_is_seen_on_the_next_page(queries):
    previous = config_service.get_active_theme()
    config_service.create_theme("cache-check", previous["main_page_file"])
    themes = config_service.get_all_themes()
    other = next(t for t in themes if t["theme_name"] == "cache-check")
    try:
        assert config_service.set_active_theme(other["id"])
        assert config_service.get_active_theme()["theme_name"] == other["theme_name"]
    finally:
        back = next(t for t in themes if t["theme_name"] == previous["theme_name"])
        config_service.set_active_theme(back["id"])


def test_what_a_caller_gets_is_its_own_copy():
    theme = config_service.get_active_theme()
    theme["main_page_file"] = "edited-by-a-caller.html"
    assert config_service.get_active_theme()["main_page_file"] != "edited-by-a-caller.html"


# --- the section catalogue ------------------------------------------------------------

def test_a_roles_sections_are_read_once_for_many_pages(queries):
    for _ in range(5):
        modules.get_modules_for_role("ROLE_ADMIN")
    assert len(reads_of(queries, "frontend_modules")) == 1


def test_a_changed_grant_is_seen_on_the_next_page():
    modules.create_or_update_module({"id": "cache-check", "name": "Cache check",
                                     "js": "c.js", "css": "c.css", "initFunction": "f"})
    try:
        assert "cache-check" not in {m["module_id"] for m in modules.get_modules_for_role("ROLE_CACHE")}
        modules.update_role_modules("ROLE_CACHE", ["cache-check"])
        assert {m["module_id"] for m in modules.get_modules_for_role("ROLE_CACHE")} == {"cache-check"}
        modules.delete_module("cache-check")
        assert modules.get_modules_for_role("ROLE_CACHE") == []
    finally:
        modules.update_role_modules("ROLE_CACHE", [])


# --- the other replicas ------------------------------------------------------------------

class Bus:
    """A running bus that records what it was asked to publish."""
    is_running = True

    def __init__(self):
        self.published = []
        self.handlers = []

    def subscribe(self, handler):
        self.handlers.append(handler)

    async def publish(self, envelope):
        self.published.append(envelope)
        return True


@pytest.fixture
def bus(monkeypatch):
    fake = Bus()
    monkeypatch.setattr(notification_bus, "_bus", fake, raising=False)
    monkeypatch.setattr(notification_bus, "get_notification_bus", lambda: fake)
    return fake


async def test_a_change_made_in_a_worker_thread_is_announced_to_the_other_replicas(bus):
    cache.attach_to_bus(bus)
    await asyncio.to_thread(modules.update_role_modules, "ROLE_CACHE", [])
    for _ in range(50):
        if bus.published:
            break
        await asyncio.sleep(0.01)
    assert {"kind": cache.ENVELOPE_KIND, "cache": modules.SECTIONS_CACHE} in bus.published


async def test_an_announcement_from_another_replica_drops_the_entry(bus, queries):
    cache.attach_to_bus(bus)
    config_service.get_active_theme()
    await bus.handlers[-1]({"kind": cache.ENVELOPE_KIND, "cache": ACTIVE_THEME_CACHE,
                            "origin": "another-replica"})
    config_service.get_active_theme()
    assert len(active_theme_reads(queries)) == 2


def test_the_server_starts_serving_with_empty_caches():
    """Start-up changesets of an application may have changed what start-up read."""
    source = (cache.__file__.replace("cache.py", "factory.py"))
    text = open(source, encoding="utf-8").read()
    assert text.index("cache.invalidate_all()") < text.index("        yield\n")
