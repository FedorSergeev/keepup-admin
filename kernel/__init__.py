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
from keepup.kernel.services import (
    DuplicateProvider,
    ServiceError,
    ServiceRegistry,
    UnsatisfiedRequirement,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "KINDS",
    "KIND_OPTIONAL",
    "KIND_REQUIRED",
    "KIND_TRANSPORT",
    "PROFILE_METRICS_ONLY",
    "Candidate",
    "CatalogueError",
    "DuplicateProvider",
    "KernelError",
    "PluginDescriptor",
    "PluginLoader",
    "PluginState",
    "Requirement",
    "Runtime",
    "ServiceError",
    "ServiceRegistry",
    "UnsatisfiedRequirement",
    "apply_profile",
    "compose",
    "create_runtime",
    "declarations",
    "declared_ids",
    "maybe_await",
    "merge",
    "parse_requirement",
    "read",
]
