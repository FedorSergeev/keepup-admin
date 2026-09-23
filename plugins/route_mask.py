"""The request mask a plugin route declares (task keepup-16).

A plugin route is data, and so is what it accepts: alongside ``path`` and
``handler`` a route may declare ``params`` -- per parameter a type, whether it
is required, where it arrives from and what its value may be.

Before this, the source of truth was the handler's signature
(``registry.accepted_params``), with two consequences. Every argument with a
default was public, so a plugin author could not keep one internal -- the
decision was made for them by how they wrote the function. And nothing outside
the process could know what a route accepts, so a wrong request had to travel
all the way into the application to be refused.

This module is deliberately free of FastAPI, of the request object and of the
database: dictionaries in, dictionaries and a list of complaints out. Turning
the complaints into a refusal is ``registry.py``'s job. The point is that the
deciding part can be tested by calling it, rather than by starting a server --
the same reason ``mock/gpu_partitioning.py`` is shaped that way.

A route that declares no mask behaves exactly as it did before. That is not
caution, it is the condition of the change: some four hundred routes are
converted by the applications' own tasks, and a framework that changed the
default would have turned all of them into refusals at once.

See doc/keepup.md.
"""

import inspect
import re
from typing import Any, Dict, List, Optional, Tuple

#: The field a route declares its mask under.
MASK_FIELD = "params"

#: Where a parameter arrives from. A path parameter is what the route matched
#: on, so it is always required and never optional.
IN_PATH = "path"
IN_QUERY = "query"
IN_ANY = "any"
SOURCES = (IN_PATH, IN_QUERY, IN_ANY)

#: What a declaration may say about one parameter.
DECLARATION_FIELDS = frozenset({
    "type", "required", "in", "choices", "min", "max", "max_length", "pattern",
    "description",
})

#: Spellings of a false flag. ``bool("false")`` is true, which is how a query
#: string turns "no" into "yes" -- every flag goes through here instead.
FALSE_SPELLINGS = frozenset({"", "0", "false", "no", "off"})

#: Supplied by the framework, never by the caller.
FRAMEWORK_SUPPLIED = frozenset({"current_user", "request"})


class MaskError(Exception):
    """The declaration itself is wrong -- the plugin author's mistake.

    Raised while registering a route, never while answering a request: a mask
    naming a parameter the handler does not take is a typo, and finding it on a
    request would mean hearing about it from a user.
    """


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in FALSE_SPELLINGS


def _to_str(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        # A flag is not a number, however willing Python is to make it one.
        raise ValueError("not an integer")
    return int(str(value).strip())


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("not a number")
    return float(str(value).strip())


#: The types a mask may declare, and how each converts a string from the query.
TYPES = {
    "str": _to_str,
    "int": _to_int,
    "float": _to_float,
    "bool": _as_bool,
}

#: Types a numeric bound applies to.
NUMERIC = frozenset({"int", "float"})


class Parameter:
    """One parameter of a mask: what it is and what it may be."""

    def __init__(self, name: str, declaration: Dict[str, Any], path_names):
        unknown = set(declaration) - DECLARATION_FIELDS
        if unknown:
            raise MaskError(
                f"parameter {name}: unknown declaration fields: "
                f"{', '.join(sorted(unknown))}")

        self.name = name
        self.type = declaration.get("type", "str")
        if self.type not in TYPES:
            raise MaskError(
                f"parameter {name}: unknown type {self.type!r}; "
                f"allowed: {', '.join(sorted(TYPES))}")

        self.source = declaration.get("in", IN_PATH if name in path_names else IN_ANY)
        if self.source not in SOURCES:
            raise MaskError(
                f"parameter {name}: unknown source {self.source!r}; "
                f"allowed: {', '.join(SOURCES)}")

        if name in path_names and self.source != IN_PATH:
            raise MaskError(
                f"parameter {name} stands in the route's path, so its source is "
                f"{IN_PATH!r}, not the declared {self.source!r}")

        # A path parameter is what the route matched on: it cannot be absent,
        # and declaring it optional would describe a request that cannot exist.
        self.required = True if self.source == IN_PATH else bool(declaration.get("required", False))

        self.choices = declaration.get("choices")
        self.min = declaration.get("min")
        self.max = declaration.get("max")
        self.max_length = declaration.get("max_length")
        self.pattern = declaration.get("pattern")
        self.description = declaration.get("description", "")

        self._check_constraints()

    def _check_constraints(self) -> None:
        """A constraint that does not fit the declared type is a typo, not a rule."""
        if self.choices is not None:
            if not isinstance(self.choices, (list, tuple)) or not self.choices:
                raise MaskError(f"parameter {self.name}: choices must be a non-empty list")
            for value in self.choices:
                try:
                    TYPES[self.type](value)
                except (TypeError, ValueError):
                    raise MaskError(
                        f"parameter {self.name}: the choice {value!r} does not "
                        f"convert to the declared type {self.type}")

        for bound in ("min", "max"):
            if getattr(self, bound) is not None and self.type not in NUMERIC:
                raise MaskError(
                    f"parameter {self.name}: {bound} applies to a number, "
                    f"and the declared type is {self.type}")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise MaskError(f"parameter {self.name}: min is greater than max")

        if self.max_length is not None:
            if self.type != "str":
                raise MaskError(
                    f"parameter {self.name}: max_length applies to a string, "
                    f"and the declared type is {self.type}")
            if not isinstance(self.max_length, int) or self.max_length <= 0:
                raise MaskError(
                    f"parameter {self.name}: max_length must be a positive integer")

        if self.pattern is not None:
            if self.type != "str":
                raise MaskError(
                    f"parameter {self.name}: pattern applies to a string, "
                    f"and the declared type is {self.type}")
            try:
                self._compiled = re.compile(self.pattern)
            except re.error as error:
                raise MaskError(f"parameter {self.name}: unusable pattern: {error}")
        else:
            self._compiled = None

    def admit(self, value: Any) -> Tuple[Any, Optional[str]]:
        """Convert and check one value; answers the value or the complaint."""
        try:
            converted = TYPES[self.type](value)
        except (TypeError, ValueError):
            return None, f"{self.name}: expected {self.type}"

        if self.choices is not None:
            allowed = [TYPES[self.type](choice) for choice in self.choices]
            if converted not in allowed:
                return None, (f"{self.name}: must be one of "
                              f"{', '.join(str(a) for a in allowed)}")

        if self.min is not None and converted < self.min:
            return None, f"{self.name}: less than {self.min}"
        if self.max is not None and converted > self.max:
            return None, f"{self.name}: greater than {self.max}"
        if self.max_length is not None and len(converted) > self.max_length:
            return None, f"{self.name}: longer than {self.max_length} characters"
        # Whole-string on purpose: a pattern that matches a substring is the
        # classic way to declare a restriction and not get one.
        if self._compiled is not None and not self._compiled.fullmatch(converted):
            return None, f"{self.name}: does not match the pattern"

        return converted, None

    def describe(self) -> Dict[str, Any]:
        """What a proxy needs to refuse this parameter before the application."""
        described = {"type": self.type, "required": self.required, "in": self.source}
        for field in ("choices", "min", "max", "max_length", "pattern", "description"):
            value = getattr(self, field)
            if value is not None and value != "":
                described[field] = list(value) if field == "choices" else value
        return described


class Mask:
    """What one route accepts."""

    def __init__(self, parameters: Dict[str, Parameter]):
        self.parameters = parameters

    def admit(self, params: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Check a request against the mask.

        Every complaint is collected rather than the first one raised: a client
        told one problem per request fixes its call as many times as it has
        mistakes, and the list costs nothing.
        """
        complaints: List[str] = []

        unknown = set(params) - set(self.parameters) - FRAMEWORK_SUPPLIED
        for name in sorted(unknown):
            complaints.append(f"{name}: the route does not take this parameter")

        values: Dict[str, Any] = {}
        for name, parameter in self.parameters.items():
            if name not in params:
                if parameter.required:
                    complaints.append(f"{name}: required parameter is missing")
                continue
            value, complaint = parameter.admit(params[name])
            if complaint:
                complaints.append(complaint)
            else:
                values[name] = value

        return values, complaints

    def describe(self) -> Dict[str, Any]:
        return {name: parameter.describe() for name, parameter in self.parameters.items()}


def path_parameters(path: str) -> List[str]:
    """The names standing in the route's own path."""
    return re.findall(r"\{([^{}/]+)\}", path or "")


def parse(declaration: Optional[Dict[str, Any]], path: str = "") -> Optional[Mask]:
    """Build a mask from a route's ``params``; ``None`` when it declares none."""
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise MaskError(f"the mask of route {path} must be a name-to-declaration mapping")

    names = path_parameters(path)
    parameters = {}
    for name, item in declaration.items():
        if not isinstance(item, dict):
            raise MaskError(f"parameter {name}: the declaration must be a mapping")
        parameters[name] = Parameter(name, item, names)

    missing = [name for name in names if name not in parameters]
    if missing:
        raise MaskError(
            f"the mask of route {path} does not declare the parameters of its "
            f"own path: {', '.join(missing)}")
    return Mask(parameters)


def check_signature(handler, mask: Optional[Mask]) -> None:
    """Refuse a mask naming a parameter the handler does not take.

    A handler declared with ``**kwargs`` is asking for everything and gets it,
    as it already does without a mask; there is nothing to check against.
    """
    if mask is None:
        return
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        # A callable that cannot be inspected is left alone, as elsewhere:
        # refusing it would break a plugin over introspection.
        return

    parameters = list(signature.parameters.values())
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
        return

    accepted = {p.name for p in parameters
                if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.KEYWORD_ONLY)}
    unknown = set(mask.parameters) - accepted
    if unknown:
        raise MaskError(
            f"the mask names parameters the handler does not take: "
            f"{', '.join(sorted(unknown))}")


def describe_routes(routes) -> List[Dict[str, Any]]:
    """The masks of every registered route, for a gateway or a proxy.

    A route that declared no mask is listed as having declared none rather than
    given an invented one: "we do not know" and "anything goes" are different
    answers, and a proxy built on the second would let through what the first
    only failed to describe.
    """
    described = []
    for route in routes or []:
        path = route.get("path", "")
        try:
            mask = parse(route.get(MASK_FIELD), path)
        except MaskError:
            # Registration refuses such a route, so this is only reachable for
            # a route that never registered; say so rather than guess.
            mask = None
            declared = False
        else:
            declared = mask is not None
        described.append({
            "path": path,
            "methods": list(route.get("methods", [])),
            "declared": declared,
            "params": mask.describe() if mask else {},
        })
    return described
