"""The constructor: plugins, the services they publish, and their life.

The kernel knows nothing about the application and nothing about the
capabilities it ships. It reads a catalogue (the framework's own declaration,
the application's file and a profile), loads what the catalogue enables, lets
every plugin publish the services it provides, initialises them in an order
their requirements allow, and reports what became of each one.

See ``doc/plugin_constructor.md`` for the contract and
``doc/service-catalogue.md`` for the names a plugin may publish or require.
"""

from keepup.kernel.catalogue import (
    CatalogueError,
    apply_profile,
    compose,
    declarations,
    declared_ids,
    merge,
    read,
)
from keepup.kernel.contributions import (
    CONTRIBUTION_KINDS,
    Contributions,
    MiddlewareSpec,
    collect,
    mount,
)
from keepup.kernel.datasource import (
    SERVICE_DATASOURCE,
    SERVICE_DATASOURCE_DRIVER,
    DataSource,
    DatasourceDriver,
    DriverSpec,
)
from keepup.kernel.descriptor import (
    KIND_OPTIONAL,
    KIND_REQUIRED,
    KIND_TRANSPORT,
    KINDS,
    PluginDescriptor,
    Requirement,
    parse_requirement,
)
from keepup.kernel.lifecycle import (
    PROFILE_METRICS_ONLY,
    KernelError,
    PluginState,
    Runtime,
    create_runtime,
    maybe_await,
)
from keepup.kernel.loader import ENTRY_POINT_GROUP, Candidate, PluginLoader
from keepup.kernel.call import (
    Call,
    CallError,
    RouteSpec,
    admit,
    admitted,
    invoke,
    route_kind,
)
from keepup.kernel.services import (
    DuplicateProvider,
    ServiceError,
    ServiceRegistry,
    UnsatisfiedRequirement,
)
from keepup.kernel.transports import TransportPlugin, route_specs_of, serve_all, transports_of

__all__ = [
    "CONTRIBUTION_KINDS",
    "ENTRY_POINT_GROUP",
    "SERVICE_DATASOURCE",
    "SERVICE_DATASOURCE_DRIVER",
    "KINDS",
    "KIND_OPTIONAL",
    "KIND_REQUIRED",
    "KIND_TRANSPORT",
    "PROFILE_METRICS_ONLY",
    "Call",
    "CallError",
    "Candidate",
    "CatalogueError",
    "Contributions",
    "DataSource",
    "DatasourceDriver",
    "DriverSpec",
    "DuplicateProvider",
    "KernelError",
    "MiddlewareSpec",
    "PluginDescriptor",
    "PluginLoader",
    "PluginState",
    "Requirement",
    "RouteSpec",
    "Runtime",
    "ServiceError",
    "ServiceRegistry",
    "TransportPlugin",
    "UnsatisfiedRequirement",
    "admit",
    "admitted",
    "apply_profile",
    "collect",
    "compose",
    "create_runtime",
    "declarations",
    "declared_ids",
    "invoke",
    "maybe_await",
    "merge",
    "mount",
    "parse_requirement",
    "read",
    "route_kind",
    "route_specs_of",
    "serve_all",
    "transports_of",
]
