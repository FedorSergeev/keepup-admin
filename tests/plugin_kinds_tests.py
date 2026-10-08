"""The kinds of plugin, and the three rules that follow from them.

Task keepup-120. A required plugin is a library: a deployment is not a
deployment without it, and the release says so in three places that are checked
here. It may declare a ``check()`` -- does a connection open, is the schema
there -- and a refusal there is a start that stops rather than a panel that
half works. A provider of a required service has to come from an installed
distribution, because a driver sees every row and every secret and a file
dropped into the plugins directory is not in a lock file or an audit. And the
panel decides whether an optional capability runs, never who provides something
the deployment cannot run without.
"""

import json

import pytest

from keepup.kernel import KIND_OPTIONAL, KIND_REQUIRED, KernelError, PluginDescriptor, create_runtime
from keepup.plugins.base import BasePlugin


class ConsumerPlugin(BasePlugin):
    """A required plugin that cannot run without a data source."""

    descriptor = PluginDescriptor(
        id="consumer",
        name="Consumer",
        kind=KIND_REQUIRED,
        priority=10,
        requires=("datasource>=1",),
    )

    def __init__(self, config=None):
        super().__init__("consumer", "Consumer", config)

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class CheckedPlugin(ConsumerPlugin):
    """A required plugin whose own check answers whether the deployment works."""

    descriptor = PluginDescriptor(
        id="checked",
        name="Checked",
        kind=KIND_REQUIRED,
        priority=10,
        provides=("datasource>=1",),
    )

    #: What check() answers: True, False, or an exception to raise.
    verdict = True

    def __init__(self, config=None):
        super().__init__(config)
        self.plugin_id = "checked"
        self.name = "Checked"
        self.checked = False

    async def check(self):
        """The self-check the kernel runs once the plugin is up."""
        self.checked = True
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


class RequiredProviderPlugin(BasePlugin):
    """A provider of a required service: required itself, and installed."""

    descriptor = PluginDescriptor(
        id="provider", name="Provider", kind=KIND_REQUIRED,
        priority=5, provides=("datasource>=1",),
    )

    def __init__(self, config=None):
        super().__init__("provider", "Provider", config)

    def register(self, services):
        """Publish the service the consumer cannot run without."""
        services.provide("datasource", "a source", plugin_id="provider")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


class OptionalConsumerPlugin(ConsumerPlugin):
    """A plugin that needs a data source and may be switched off."""

    descriptor = PluginDescriptor(
        id="consumer", name="Consumer", kind=KIND_OPTIONAL,
        priority=10, requires=("datasource>=1",),
    )


class DataSourcePlugin(BasePlugin):
    """A plugin that provides the service the consumer needs."""

    descriptor = PluginDescriptor(
        id="provider",
        name="Provider",
        kind=KIND_OPTIONAL,
        priority=20,
        provides=("datasource>=1",),
    )

    def __init__(self, config=None):
        super().__init__("provider", "Provider", config)

    def register(self, services):
        """Publish the service under the name the consumer requires."""
        services.provide("datasource", "a source", plugin_id="provider")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}


def entry_point(plugin_class):
    """An entry point for a plugin class, named by its descriptor."""

    class FakeEntryPoint:
        def __init__(self):
            self.name = plugin_class.descriptor.id
            self.group = "keepup.plugins"

        def load(self):
            return plugin_class

    return FakeEntryPoint()


def runtime_with(*plugin_classes, plugins_dir=None, overrides=None, enabled=True):
    """A runtime whose catalogue declares exactly these plugins."""
    return create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={
            "plugins": [{"id": plugin_class.descriptor.id,
                         **({"enabled": True} if enabled else {})}
                        for plugin_class in plugin_classes]
        },
        entry_points=[entry_point(plugin_class) for plugin_class in plugin_classes],
        plugins_dir=plugins_dir,
        overrides_reader=(lambda: overrides) if overrides is not None else None,
    )


# --- the self-check of a required plugin ----------------------------------------


async def test_a_required_plugin_that_checks_out_starts():
    """The ordinary case: it came up, and its own check agrees."""
    runtime = runtime_with(CheckedPlugin)
    await runtime.start()
    plugin = runtime.get_plugin("checked")
    assert plugin.checked is True
    assert runtime.report()[0]["outcome"] == "initialized"


async def test_a_required_plugin_whose_check_fails_stops_the_start():
    """A deployment that cannot work is worse than one that does not start."""
    CheckedPlugin.verdict = False
    try:
        runtime = runtime_with(CheckedPlugin)
        with pytest.raises(KernelError) as refused:
            await runtime.start()
        assert "checked" in str(refused.value)
        row = runtime.report()[0]
        assert row["outcome"] == "failed"
        assert row["reason"] == "check() returned false"
        assert runtime.get_plugin("checked") is None
    finally:
        CheckedPlugin.verdict = True


async def test_a_required_plugin_whose_check_raises_stops_the_start_with_the_reason():
    """The reason is recorded, not swallowed: somebody has to read it at 3 a.m."""
    CheckedPlugin.verdict = RuntimeError("the schema is older than the code")
    try:
        runtime = runtime_with(CheckedPlugin)
        with pytest.raises(KernelError):
            await runtime.start()
        row = runtime.report()[0]
        assert "the schema is older than the code" in row["reason"]
    finally:
        CheckedPlugin.verdict = True


# --- who may provide what the deployment cannot run without ----------------------


PROVIDER_FILE = '''"""A provider of a required service, dropped into the application's directory."""

from keepup.kernel import KIND_REQUIRED, PluginDescriptor
from keepup.plugins.base import BasePlugin


class ProviderPlugin(BasePlugin):
    """It provides the database -- and it is a file, not a distribution."""

    descriptor = PluginDescriptor(id="provider", name="Provider", kind=KIND_REQUIRED,
                                  provides=("datasource>=1",))

    def __init__(self, config=None):
        super().__init__("provider", "Provider", config)

    def register(self, services):
        services.provide("datasource", "a source", plugin_id="provider")

    async def initialize(self):
        return True

    def get_api_routes(self):
        return []

    def get_handlers(self):
        return {}
'''


async def test_a_provider_from_the_plugins_directory_is_refused(tmp_path):
    """A driver is the one component that must be nameable by pip freeze and an audit."""
    (tmp_path / "provider.py").write_text(PROVIDER_FILE, encoding="utf-8")
    runtime = runtime_with(ConsumerPlugin, plugins_dir=str(tmp_path))
    with pytest.raises(KernelError) as refused:
        await runtime.start()
    assert "file of the application" in str(refused.value)
    assert "datasource" in str(refused.value)


async def test_the_panel_may_not_choose_the_provider_of_a_required_service():
    """Otherwise an administrator of the panel becomes somebody who reads every row."""
    runtime = runtime_with(
        OptionalConsumerPlugin, DataSourcePlugin, enabled=False,
        overrides={"consumer": True, "provider": True},
    )
    with pytest.raises(KernelError) as refused:
        await runtime.start()
    assert "not in the panel" in str(refused.value)


async def test_the_catalogue_may_choose_the_provider_of_a_required_service():
    """The composition root decides, and that is a decision made in code."""
    runtime = runtime_with(ConsumerPlugin, RequiredProviderPlugin, enabled=True)
    await runtime.start()
    assert runtime.get_plugin("consumer") is not None
    assert runtime.get_plugin("provider") is not None
    assert runtime.services.require("datasource") == "a source"


async def test_the_report_says_where_each_plugin_came_from():
    """A distribution and a file of the application are told apart in the report."""
    runtime = runtime_with(CheckedPlugin)
    await runtime.start()
    row = runtime.report()[0]
    assert row["origin"] == "entry-point"
    assert row["kind"] == KIND_REQUIRED
    assert row["phase"] == "required"


async def test_an_optional_plugin_from_the_directory_is_still_welcome(tmp_path):
    """The rule is about required services, not about files: a capability may be one."""
    (tmp_path / "notes.py").write_text(
        "from keepup.plugins.base import BasePlugin\n"
        "\n"
        "\n"
        "class NotesPlugin(BasePlugin):\n"
        "    \"\"\"An ordinary plugin of an application, in a file.\"\"\"\n"
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
    runtime = create_runtime(
        builtin_catalogue={},
        builtin_dir=None,
        application_catalogue={"plugins": [{"id": "notes", "enabled": True}]},
        plugins_dir=str(tmp_path),
    )
    await runtime.start()
    assert runtime.get_plugin("notes") is not None
    assert runtime.report()[0]["origin"] == "directory"


def test_the_kinds_are_the_three_the_specification_names():
    """The kinds are data, so the guide, the spec and the code can be one list."""
    from keepup.kernel import KINDS

    assert set(KINDS) == {KIND_REQUIRED, KIND_OPTIONAL, "transport"}
    assert json.dumps(sorted(KINDS))  # a plain value, not an enum or a callable
