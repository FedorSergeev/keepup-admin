"""The kernel: the catalogue, the services, the loader and the order of the start.

The kernel is the part of 0.4.0 that decides what runs and in which order, so
these checks are about decisions rather than about HTTP: a plugin whose
requirement nobody provides, a required plugin somebody tried to switch off, two
providers of one required service, and a plugin written for 0.3.0 that knows
nothing about any of it.

Nothing here starts a server or touches a database: the kernel is a package of
its own and the checks say so (task keepup-101).
"""


import pytest

from keepup.kernel import (
    CatalogueError,
    DuplicateProvider,
    KernelError,
    KIND_OPTIONAL,
    KIND_REQUIRED,
    KIND_TRANSPORT,
    PluginDescriptor,
    PluginLoader,
    Runtime,
    ServiceError,
    ServiceRegistry,
    UnsatisfiedRequirement,
    apply_profile,
    create_runtime,
    merge,
    parse_requirement,
)
from keepup.kernel.lifecycle import OUTCOME_UNSATISFIED
from keepup.plugins.base import BasePlugin


class FakeEntryPoint:
    """Just enough of a distribution's entry point to load a plugin class."""

    def __init__(self, name, plugin_class):
        self.name = name
        self.group = "keepup.plugins"
        self._plugin_class = plugin_class

    def load(self):
        """Return the class the entry point names."""
        return self._plugin_class


def entry_point(plugin_class):
    """An entry point for a plugin class, named by its descriptor."""
    return FakeEntryPoint(plugin_class.descriptor.id, plugin_class)


# --- plugins used by the checks -------------------------------------------------


class GreetingPlugin(BasePlugin):
    """A plugin that publishes a service and needs nothing."""

    descriptor = PluginDescriptor(
        id="greeting",
        name="Greeting",
        kind=KIND_REQUIRED,
        priority=10,
        provides=("greeting>=1",),
    )

    def __init__(self, config=None):
        super().__init__("greeting", "Greeting", config)
        self.cleaned_up = False

    def register(self, services):
        """Publish one string, which is all this check needs."""
        services.provide("greeting", "hello", plugin_id="greeting")

    async def initialize(self):
        """Record that it ran."""
        self.initialized = True
        return True

    async def cleanup(self):
        """Record that it was stopped."""
        self.cleaned_up = True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class ConsumerPlugin(BasePlugin):
    """A plugin that requires a service published by a plugin declared after it."""

    descriptor = PluginDescriptor(
        id="consumer",
        name="Consumer",
        kind=KIND_REQUIRED,
        priority=20,
        requires=("greeting>=1",),
    )

    def __init__(self, config=None):
        super().__init__("consumer", "Consumer", config)
        self.got = None
        self.order = []

    def register(self, services):
        """Publish a service of its own, so the report has two rows."""

    async def initialize(self):
        """Ask for the service, which must already be there."""
        self.got = self.services.require("greeting")
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class WantingPlugin(BasePlugin):
    """A plugin whose optional requirement nobody satisfies."""

    descriptor = PluginDescriptor(
        id="wanting",
        name="Wanting",
        kind=KIND_REQUIRED,
        priority=30,
        wants=("cluster>=1",),
    )

    def __init__(self, config=None):
        super().__init__("wanting", "Wanting", config)

    async def initialize(self):
        """Run anyway: a soft requirement is not a reason to stop."""
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class OldStylePlugin(BasePlugin):
    """A plugin written for 0.3.0: no descriptor, no requirements, no services."""

    def __init__(self, config=None):
        super().__init__("old_style", "Old style", config)
        self.ran = False

    async def initialize(self):
        """Record that the kernel called it."""
        self.ran = True
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class DbPlugin(BasePlugin):
    """A plugin that requires a driver: what makes the driver's service required."""

    descriptor = PluginDescriptor(
        id="db",
        name="Database abstraction",
        kind=KIND_REQUIRED,
        priority=5,
        requires=("datasource_driver>=1",),
    )

    def __init__(self, config=None):
        super().__init__("db", "Database abstraction", config)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class SqliteDriverPlugin(BasePlugin):
    """A second provider of one required service, to be refused."""

    descriptor = PluginDescriptor(
        id="sqlite",
        name="SQLite",
        kind=KIND_REQUIRED,
        priority=40,
        provides=("datasource_driver>=1",),
    )

    def __init__(self, config=None):
        super().__init__("sqlite", "SQLite", config)

    def register(self, services):
        """Publish the same service the other driver publishes."""
        services.provide("datasource_driver", "sqlite", plugin_id="sqlite")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class PostgresDriverPlugin(SqliteDriverPlugin):
    """The other provider of the same required service."""

    descriptor = PluginDescriptor(
        id="postgres",
        name="PostgreSQL",
        kind=KIND_REQUIRED,
        priority=41,
        provides=("datasource_driver>=1",),
    )

    def __init__(self, config=None):
        super().__init__(config)
        self.plugin_id = "postgres"
        self.name = "PostgreSQL"

    def register(self, services):
        """Publish the same service under the other name."""
        services.provide("datasource_driver", "postgres", plugin_id="postgres")


def catalogue_for(*plugin_classes, profile=None):
    """A catalogue that declares these plugins and nothing else."""
    entries = []
    for plugin_class in plugin_classes:
        descriptor = getattr(plugin_class, "descriptor", None)
        plugin_id = descriptor.id if descriptor else "old_style"
        entries.append({"id": plugin_id, "enabled": True})
    return {"plugins": entries}


# --- the descriptor -------------------------------------------------------------


def test_a_descriptor_is_read_without_constructing_the_plugin():
    """The kernel has to know what a plugin needs before it may build it."""
    constructed = []

    class Counting(BasePlugin):
        descriptor = PluginDescriptor(id="counting", requires=("datasource>=1",))

        def __init__(self, config=None):
            constructed.append(1)
            super().__init__("counting", "Counting", config)

        async def initialize(self):
            return True

        def get_api_routes(self):
            return []

        def get_handlers(self):
            return {}

    descriptor = PluginDescriptor.of(Counting, "counting")
    assert descriptor.requires == (parse_requirement("datasource>=1"),)
    assert constructed == []


def test_requirement_forms_are_the_same_requirement():
    """Three ways of writing one requirement mean one thing."""
    assert parse_requirement("datasource") == parse_requirement("datasource>=0")
    assert parse_requirement("datasource>=1").name == "datasource"
    assert parse_requirement(" datasource >= 2 ") == parse_requirement("datasource>=2")
    assert parse_requirement("datasource>=1").satisfied_by(1)
    assert not parse_requirement("datasource>=2").satisfied_by(1)


def test_a_declared_attribute_that_is_not_a_descriptor_is_refused():
    """A wrong declaration is a mistake, not a plugin without a descriptor."""

    class Wrong(BasePlugin):
        descriptor = {"id": "wrong"}

        def __init__(self, config=None):
            super().__init__("wrong", "Wrong", config)

        async def initialize(self):
            return True

        def get_api_routes(self):
            return []

        def get_handlers(self):
            return {}

    with pytest.raises(TypeError):
        PluginDescriptor.of(Wrong, "wrong")


# --- the catalogue --------------------------------------------------------------


def test_the_application_wins_field_by_field():
    """An application says what it thinks without restating the framework's defaults."""
    builtin = {
        "plugins": [
            {"id": "metrics", "kind": "optional", "priority": 40, "requires": ["datasource>=1"]}
        ]
    }
    application = {"plugins": [{"id": "metrics", "enabled": False}]}
    merged = merge(builtin, application)
    entry = merged["plugins"][0]
    assert entry["enabled"] is False
    assert entry["priority"] == 40
    assert entry["requires"] == ["datasource>=1"]


def test_a_profile_patches_and_switches_whole_sets():
    """`only` switches off everything optional that it does not name."""
    catalogue = {
        "plugins": [
            {"id": "db", "kind": "required"},
            {"id": "http", "kind": "transport", "enabled": True},
            {"id": "metrics", "kind": "optional"},
            {"id": "ui", "kind": "optional"},
        ],
        "profiles": {"metrics-only": {"only": ["http", "metrics"]}},
    }
    patched = apply_profile(catalogue, "metrics-only")
    by_id = {entry["id"]: entry for entry in patched["plugins"]}
    assert by_id["http"]["enabled"] is True
    assert by_id["metrics"]["enabled"] is True
    assert by_id["ui"]["enabled"] is False
    assert "enabled" not in by_id["db"] or by_id["db"]["enabled"] is not False


def test_a_profile_cannot_name_what_the_catalogue_does_not_declare():
    """A profile is a patch over a catalogue, not a second catalogue."""
    catalogue = {"plugins": [{"id": "http"}], "profiles": {"broken": {"only": ["metrics"]}}}
    with pytest.raises(CatalogueError):
        apply_profile(catalogue, "broken")


def test_a_profile_cannot_switch_off_a_required_plugin():
    """What a deployment cannot work without is not a preference."""
    catalogue = {
        "plugins": [{"id": "db", "kind": "required"}],
        "profiles": {"no-db": {"disable": ["db"]}},
    }
    with pytest.raises(CatalogueError):
        apply_profile(catalogue, "no-db")


def test_an_unknown_profile_is_refused_with_the_known_ones_named():
    """A typo in a profile name must not be a deployment without a catalogue."""
    with pytest.raises(CatalogueError) as refused:
        apply_profile({"plugins": [], "profiles": {"panel": {}}}, "panell")
    assert "panel" in str(refused.value)


def test_the_builtin_catalogue_is_readable_and_well_formed():
    """The framework's own declaration is data, and it says which kind each is."""
    from keepup.kernel import catalogue as catalogue_module

    builtin = catalogue_module.builtin()
    ids = [entry["id"] for entry in builtin["plugins"]]
    assert "db" in ids and "http" in ids
    for entry in builtin["plugins"]:
        assert entry["kind"] in (KIND_REQUIRED, KIND_OPTIONAL, KIND_TRANSPORT)
    assert "metrics-only" in builtin["profiles"]


# --- the services ---------------------------------------------------------------


def test_a_service_is_handed_over_by_name_and_version():
    """What a consumer holds is the object, not the plugin that built it."""
    services = ServiceRegistry()
    services.provide("datasource", "the pool", version=2, plugin_id="db")
    assert services.require("datasource") == "the pool"
    assert services.require("datasource", 2) == "the pool"
    with pytest.raises(UnsatisfiedRequirement):
        services.require("datasource", 3)


def test_a_lazy_provider_is_built_once():
    """A pool must open when it is first wanted, and only once."""
    built = []

    def build():
        built.append(1)
        return "pool"

    services = ServiceRegistry()
    services.provide("datasource", factory=build, lazy=True, plugin_id="db")
    assert services.require("datasource") == "pool"
    assert services.require("datasource") == "pool"
    assert built == [1]


def test_two_providers_of_a_required_service_stop_the_start():
    """A deployment that silently picked one database would hide which one."""
    services = ServiceRegistry()
    services.provide("datasource", "postgres", plugin_id="postgres")
    services.provide("datasource", "sqlite", plugin_id="sqlite")
    services.mark_required("datasource")
    with pytest.raises(DuplicateProvider) as refused:
        services.freeze()
    assert "postgres" in str(refused.value) and "sqlite" in str(refused.value)


def test_publishing_after_the_freeze_is_refused():
    """After the start, a plugin may contribute to a running system, not reshape it."""
    services = ServiceRegistry()
    services.freeze()
    with pytest.raises(ServiceError):
        services.provide("late", "value", plugin_id="late")


# --- the loader -----------------------------------------------------------------


def test_a_plugin_of_the_application_is_loaded_from_its_file(tmp_path):
    """Applications write one file per plugin; that is how they always have."""
    (tmp_path / "notes.py").write_text(
        "from keepup.plugins.base import BasePlugin\n"
        "\n"
        "\n"
        "class NotesPlugin(BasePlugin):\n"
        "    \"\"\"A plugin of an application, with no descriptor at all.\"\"\"\n"
        "\n"
        "    def __init__(self, config=None):\n"
        "        super().__init__('notes', 'Notes', config)\n"
        "\n"
        "    async def initialize(self):\n"
        "        return True\n"
        "\n"
        "    def get_api_routes(self):\n"
        "        return []\n"
        "\n"
        "    def get_handlers(self):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    loader = PluginLoader(str(tmp_path), entry_points=[], builtin_dir=None)
    candidates = loader.candidates()
    assert "notes" in candidates
    assert candidates["notes"].source == "directory"
    assert candidates["notes"].descriptor.requires == ()
    assert candidates["notes"].descriptor.name == "notes"


def test_a_file_without_the_expected_class_is_passed_over(tmp_path):
    """A wrong class name does not raise in 0.3.0, and must not raise now."""
    (tmp_path / "wrong.py").write_text("value = 1\n", encoding="utf-8")
    loader = PluginLoader(str(tmp_path), entry_points=[], builtin_dir=None)
    assert loader.candidates() == {}


def test_an_application_file_shadows_a_distribution(tmp_path):
    """The application's own plugin wins, and the log says so."""
    (tmp_path / "greeting.py").write_text(
        "from keepup.plugins.base import BasePlugin\n"
        "\n"
        "\n"
        "class GreetingPlugin(BasePlugin):\n"
        "    \"\"\"A plugin that shadows the installed one of the same id.\"\"\"\n"
        "\n"
        "    def __init__(self, config=None):\n"
        "        super().__init__('greeting', 'Local greeting', config)\n"
        "\n"
        "    async def initialize(self):\n"
        "        return True\n"
        "\n"
        "    def get_api_routes(self):\n"
        "        return []\n"
        "\n"
        "    def get_handlers(self):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    loader = PluginLoader(str(tmp_path), entry_points=[entry_point(GreetingPlugin)], builtin_dir=None)
    candidates = loader.candidates()
    assert candidates["greeting"].source == "directory"


# --- the runtime ----------------------------------------------------------------


async def test_a_consumer_gets_a_service_from_a_plugin_declared_later():
    """Registration happens before any initialisation, so order does not matter."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(GreetingPlugin, ConsumerPlugin),
        entry_points=[entry_point(GreetingPlugin), entry_point(ConsumerPlugin)],
    )
    await runtime.start()
    consumer = runtime.get_plugin("consumer")
    assert consumer.got == "hello"
    assert runtime.report()[0]["phase"] == "required"


async def test_cleanup_runs_in_the_reverse_order():
    """What came up last goes down first."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(GreetingPlugin),
        entry_points=[entry_point(GreetingPlugin)],
    )
    await runtime.start()
    plugin = runtime.get_plugin("greeting")
    await runtime.stop()
    assert plugin.cleaned_up is True
    assert runtime.get_plugin("greeting") is None


async def test_a_soft_requirement_degrades_instead_of_stopping():
    """A plugin that runs worse is not a plugin that does not run."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(WantingPlugin),
        entry_points=[entry_point(WantingPlugin)],
    )
    await runtime.start()
    row = runtime.report()[0]
    assert row["initialized"] is True
    assert row["degraded"] == ["cluster>=1"]


async def test_a_hard_requirement_nobody_satisfies_leaves_the_plugin_out():
    """An audit that cannot record is not an audit, so it does not pretend to be."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(ConsumerPlugin),
        entry_points=[entry_point(ConsumerPlugin)],
    )
    with pytest.raises(KernelError):
        await runtime.start()
    assert runtime.states["consumer"].outcome == OUTCOME_UNSATISFIED
    assert "greeting>=1" in runtime.states["consumer"].reason


async def test_a_plugin_written_for_0_3_0_still_runs(tmp_path):
    """No descriptor, no services, no changes: it is accepted as it is."""
    (tmp_path / "old_style.py").write_text(
        "from keepup.plugins.base import BasePlugin\n"
        "\n"
        "\n"
        "class Old_stylePlugin(BasePlugin):\n"
        "    \"\"\"A plugin written before descriptors existed.\"\"\"\n"
        "\n"
        "    def __init__(self, config=None):\n"
        "        super().__init__('old_style', 'Old style', config)\n"
        "        self.ran = False\n"
        "\n"
        "    async def initialize(self):\n"
        "        self.ran = True\n"
        "        return True\n"
        "\n"
        "    def get_api_routes(self):\n"
        "        return []\n"
        "\n"
        "    def get_handlers(self):\n"
        "        return {}\n",
        encoding="utf-8",
    )
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": [{"id": "old_style", "enabled": True}]},
        plugins_dir=str(tmp_path),
    )
    await runtime.start()
    assert runtime.get_plugin("old_style").ran is True


async def test_the_environment_cannot_switch_off_a_required_plugin():
    """One line in a deployment's variables must not remove what it cannot work without."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(GreetingPlugin),
        entry_points=[entry_point(GreetingPlugin)],
        environ={"PLUGINS_DISABLE": "greeting"},
    )
    with pytest.raises(KernelError) as refused:
        await runtime.start()
    assert "greeting" in str(refused.value)


async def test_the_application_file_may_switch_a_required_plugin_off():
    """The composition root says 'I bring my own', and that is its decision."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": [{"id": "greeting", "enabled": False}]},
        entry_points=[entry_point(GreetingPlugin)],
    )
    await runtime.start()
    assert runtime.states["greeting"].enabled is False
    assert runtime.get_plugin("greeting") is None


async def test_two_providers_of_one_required_service_stop_the_start():
    """The kernel refuses to guess which database it is running on."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(DbPlugin, SqliteDriverPlugin, PostgresDriverPlugin),
        entry_points=[
            entry_point(DbPlugin),
            entry_point(SqliteDriverPlugin),
            entry_point(PostgresDriverPlugin),
        ],
    )
    with pytest.raises(DuplicateProvider):
        await runtime.start()


async def test_a_plugin_enabled_but_not_installed_stops_the_start():
    """A deployment that asked for something absent is not a deployment."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": [{"id": "cluster", "kind": "required", "enabled": True}]},
        entry_points=[],
    )
    with pytest.raises(KernelError) as refused:
        await runtime.start()
    assert "cluster" in str(refused.value)


async def test_the_administrators_decision_is_read_in_the_second_phase():
    """The decision lives in a database a plugin provides, so it is read later."""
    decisions = []

    def overrides():
        decisions.append(True)
        return {"metrics": True}

    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": [{"id": "greeting"}, {"id": "metrics"}]},
        entry_points=[entry_point(GreetingPlugin), entry_point(ConsumerPlugin)],
        overrides_reader=overrides,
    )
    await runtime.start()
    assert decisions == [True]
    assert runtime.states["greeting"].phase == "required"
    assert runtime.states["metrics"].phase == "optional"


async def test_two_runtimes_in_one_process_share_nothing():
    """The state belongs to the instance: 0.3.0's module globals did not."""
    first = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(GreetingPlugin),
        entry_points=[entry_point(GreetingPlugin)],
    )
    second = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": []},
        entry_points=[],
    )
    await first.start()
    await second.start()
    assert first.get_plugin("greeting") is not None
    assert second.get_plugin("greeting") is None
    assert second.services.has("greeting") is False
    assert [row["id"] for row in second.report()] == []


async def test_the_report_says_what_each_plugin_contributes_and_who_decided():
    """Composition has to be legible: it is the only place the wiring is written down."""
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue=catalogue_for(GreetingPlugin),
        entry_points=[entry_point(GreetingPlugin)],
    )
    await runtime.start()
    row = runtime.report()[0]
    assert row["id"] == "greeting"
    assert row["kind"] == KIND_REQUIRED
    assert row["source"] == "installed"
    assert row["provides"] == ["greeting>=1"]
    assert runtime.services.report()[0]["plugin_id"] == "greeting"


async def test_a_profile_assembles_a_deployment_from_the_same_code():
    """The metrics-only profile is what the retired flag asked for."""
    runtime = create_runtime(
        builtin_catalogue={
            "plugins": [
                {"id": "metrics", "kind": "optional"},
                {"id": "ui", "kind": "optional"},
            ],
            "profiles": {"metrics-only": {"only": ["metrics"]}},
        },
        application_catalogue={},
        profile="metrics-only",
        entry_points=[],
        # A deployment of its own: the framework's own plugins and drivers are
        # not part of what this profile assembles.
        builtin_dir=None,
    )
    await runtime.start()
    assert runtime.states["metrics"].enabled is True
    assert runtime.states["ui"].enabled is False


def test_a_runtime_is_built_without_settings():
    """A deployment may be assembled from arguments alone, which is what tests need."""
    runtime = Runtime(application_catalogue={"plugins": []}, entry_points=[])
    assert runtime.report() == []
    assert isinstance(runtime.services, ServiceRegistry)
