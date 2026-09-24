"""The plugin runtime: the contract, the manager, and who is switched on.

``base`` holds :class:`BasePlugin` and :class:`PluginManager`, ``enablement``
decides which declared plugins run, ``routes`` turns a declared route into an
endpoint, ``admin`` publishes what the deployment decided about each plugin,
and ``registry`` assembles the lot -- and is the address an application imports
from. The directory plugins are loaded from belongs to the application: the
framework is a different package and holds none.
"""
