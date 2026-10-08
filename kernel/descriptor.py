"""Who a plugin is, before any of its code runs.

A plugin is a class, and everything the kernel needs in order to decide whether
to call it is a class attribute: the identifier, what services it needs, what it
publishes, what it contributes and which kind of plugin it is. The kernel reads
that attribute without instantiating anything, which is what lets a plugin whose
requirements cannot be met be reported rather than constructed.

A plugin written for 0.3.0 has no descriptor, and is accepted: its identifier is
derived from its module name the way the plugin manager has always derived it,
and it needs nothing.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Tuple

__all__ = [
    "DESCRIPTOR_ATTRIBUTE",
    "KIND_OPTIONAL",
    "KIND_REQUIRED",
    "KIND_TRANSPORT",
    "KINDS",
    "PluginDescriptor",
    "Requirement",
    "parse_requirement",
]

#: The class attribute a plugin declares itself in.
DESCRIPTOR_ATTRIBUTE = "descriptor"

#: A plugin without which this deployment is not a deployment: a data source, a
#: catalogue. It cannot be switched off by an administrator or by the
#: environment, and its absence stops the start (doc/plugin_constructor.md 4.7).
KIND_REQUIRED = "required"
#: A plugin the deployment may do without, and the panel may switch.
KIND_OPTIONAL = "optional"
#: A way into the application -- HTTP, gRPC, a command line. At least one is
#: usually enabled, and a deployment that enables none is a worker on purpose.
KIND_TRANSPORT = "transport"

KINDS = (KIND_REQUIRED, KIND_OPTIONAL, KIND_TRANSPORT)


@dataclass(frozen=True)
class Requirement:
    """One service a plugin needs, and the version it needs at least.

    Attributes:
        name: the service name, a noun the kernel owns.
        minimum: the lowest major version that satisfies the requirement; 0
            means any version.
    """

    name: str
    minimum: int = 0

    def satisfied_by(self, version: int) -> bool:
        """Whether a provider of this version satisfies the requirement.

        Args:
            version: the major version the provider announced.

        Returns:
            True when the provider is new enough.
        """
        return version >= self.minimum

    def __str__(self) -> str:
        return self.name if not self.minimum else f"{self.name}>={self.minimum}"


def parse_requirement(text: Any) -> Requirement:
    """Read ``"datasource"``, ``"datasource>=1"`` or ``"datasource >= 1"``.

    Args:
        text: the declaration, or a Requirement that is returned unchanged.

    Returns:
        The parsed requirement.

    Raises:
        ValueError: when the declaration is not a string, or names nothing.
    """
    if isinstance(text, Requirement):
        return text
    if not isinstance(text, str):
        raise ValueError(f"a requirement is a string, not {type(text).__name__}")
    stripped = text.strip()
    if not stripped:
        raise ValueError("a requirement names a service")
    if ">=" not in stripped:
        return Requirement(stripped)
    name, _, version = stripped.partition(">=")
    name = name.strip()
    version = version.strip()
    if not name or not version.isdigit():
        raise ValueError(f"a requirement reads 'name>=version', not {text!r}")
    return Requirement(name, int(version))


def parse_provided(text: Any) -> Tuple[str, int]:
    """Read what a plugin says it provides, and at which version.

    Args:
        text: ``"datasource"``, ``"datasource>=1"`` or a ``(name, version)``.

    Returns:
        The service name and its major version.

    Raises:
        ValueError: when the declaration is malformed.
    """
    if isinstance(text, tuple) and len(text) == 2:
        return str(text[0]), int(text[1])
    requirement = parse_requirement(text)
    return requirement.name, requirement.minimum or 1


@dataclass(frozen=True)
class PluginDescriptor:
    """Who a plugin is: what it needs, what it gives and how it is treated.

    Attributes:
        id: the unique identifier; the module name for a directory plugin.
        name: what the panel shows.
        version: the plugin's own version, not the framework's.
        distribution: the distribution it travels in, for ``pip install``.
        priority: lower initialises first; ties keep declaration order.
        requires: services without which the plugin must not run.
        wants: services without which the plugin runs worse; the report marks
            it degraded and names them.
        provides: services the plugin publishes, as ``"name>=version"``.
        contributions: the kinds of contribution it makes -- routes, sections,
            tables, jobs, events, metrics, middleware, transport, settings,
            permissions.
        kind: one of :data:`KINDS`.
        default_enabled: what an absent ``enabled`` flag means.
        config_schema: JSON-schema-shaped, for validating the plugin's own
            configuration before it runs.
    """

    id: str
    name: str = ""
    version: str = "0.0.0"
    distribution: str = ""
    priority: int = 0
    requires: Tuple[Any, ...] = ()
    wants: Tuple[Any, ...] = ()
    provides: Tuple[Any, ...] = ()
    contributions: Tuple[str, ...] = ()
    kind: str = KIND_OPTIONAL
    default_enabled: bool = False
    config_schema: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise the declared tuples and refuse a kind nobody knows."""
        object.__setattr__(self, "requires", tuple(parse_requirement(item) for item in self.requires))
        object.__setattr__(self, "wants", tuple(parse_requirement(item) for item in self.wants))
        object.__setattr__(self, "provides", tuple(parse_provided(item) for item in self.provides))
        object.__setattr__(self, "contributions", tuple(str(item) for item in self.contributions))
        if self.kind not in KINDS:
            raise ValueError(f"{self.id}: unknown plugin kind {self.kind!r}")
        if not self.id:
            raise ValueError("a plugin descriptor names an id")

    @property
    def is_required(self) -> bool:
        """Whether this deployment is not a deployment without the plugin."""
        return self.kind == KIND_REQUIRED

    @property
    def is_transport(self) -> bool:
        """Whether the plugin is a way into the application."""
        return self.kind == KIND_TRANSPORT

    @property
    def service_names(self) -> Tuple[str, ...]:
        """The names of the services the plugin publishes."""
        return tuple(name for name, _ in self.provides)

    @classmethod
    def of(cls, plugin_class: Any, plugin_id: str, name: str = "") -> "PluginDescriptor":
        """Read a plugin's descriptor, or derive one for a 0.3.0 plugin.

        Args:
            plugin_class: the plugin class.
            plugin_id: the identifier the loader found it under.
            name: what to call it when it declares nothing.

        Returns:
            The declared descriptor, or a derived one.

        Raises:
            TypeError: when the declared attribute is not a descriptor.
        """
        declared = getattr(plugin_class, DESCRIPTOR_ATTRIBUTE, None)
        if declared is None:
            return cls(id=plugin_id, name=name or plugin_id, derived=True)  # type: ignore[call-arg]
        if not isinstance(declared, PluginDescriptor):
            raise TypeError(
                f"{plugin_id}: {DESCRIPTOR_ATTRIBUTE} is a "
                f"{type(declared).__name__}, not a PluginDescriptor"
            )
        return declared

    #: Whether the descriptor was derived rather than declared. Kept out of the
    #: constructor's signature so that a declared descriptor and a derived one
    #: cannot differ in anything else; set through the private field below.
    derived: bool = field(default=False, compare=False)
