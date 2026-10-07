"""The identity_provider section and the plugin it names (keepup-91).

A deployment inside somebody else's system names its provider in a configmap.
Everything here is about the start refusing what it cannot act on -- a typo, a
missing secret, a plugin that is not there or cannot do what it is asked -- because
a deployment that quietly came up without its provider would let people in some
other way than it was told to.

    python3 -m pytest keepup/tests/identity_config_tests.py -v
"""

import sys
import types
from pathlib import Path

import pytest

from keepup.auth.identity import runtime as identity_runtime
from keepup.auth.identity.config import (
    IdentityProviderConfig,
    IdentityProviderMisconfigured,
    load_section,
    parse,
    resolve,
)
from keepup.auth.identity.contract import ExternalIdentity, IdentityProvider
from keepup.auth.identity.loader import build_provider, find_class

MODULE = "keepup_identity_config_test_plugins"


class TokensOnly(IdentityProvider):
    async def verify_token(self, token):
        return ExternalIdentity(subject="s")


class Everything(TokensOnly):
    async def verify_password(self, username, password):
        return ExternalIdentity(subject="s")

    async def decide(self, identity, user, request):
        return True


class Nothing(IdentityProvider):
    pass


class Exploding(TokensOnly):
    def __init__(self, settings):
        raise RuntimeError(f"cannot start with {settings['secret']}")


class NotAProvider:
    pass


@pytest.fixture(autouse=True)
def plugin_module():
    """The classes above, importable as `module:Class` the way a deployment names them."""
    module = types.ModuleType(MODULE)
    for kind in (TokensOnly, Everything, Nothing, Exploding, NotAProvider):
        setattr(module, kind.__name__, kind)
    sys.modules[MODULE] = module
    yield module
    del sys.modules[MODULE]
    identity_runtime.install(None)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "auth.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def section(**overrides):
    base = {"name": "corp-as", "plugin": f"{MODULE}:TokensOnly"}
    base.update(overrides)
    return base


# --- the contract ---------------------------------------------------------------

def test_capabilities_are_what_the_class_overrides():
    assert TokensOnly.capabilities() == ("verify_token",)
    assert Everything.capabilities() == ("verify_token", "verify_password", "decide")
    assert Nothing.capabilities() == ()


def test_a_provider_that_can_do_nothing_stops_the_start():
    with pytest.raises(IdentityProviderMisconfigured, match="implements none"):
        build_provider(parse(section(plugin=f"{MODULE}:Nothing")))


def test_accepting_tokens_needs_verify_token():
    class PasswordsOnly(IdentityProvider):
        async def verify_password(self, username, password):
            return ExternalIdentity(subject="s")

    config = IdentityProviderConfig(name="corp-as", plugin=PasswordsOnly({}))
    with pytest.raises(IdentityProviderMisconfigured, match="verify_token.*accept_tokens: false"):
        build_provider(config)


def test_password_sign_in_needs_verify_password():
    with pytest.raises(IdentityProviderMisconfigured, match="verify_password"):
        build_provider(parse(section(password_sign_in=True)))


def test_provider_authorization_needs_decide():
    with pytest.raises(IdentityProviderMisconfigured, match="decide.*authorization: local"):
        build_provider(parse(section(authorization="provider")))


def test_a_provider_used_for_nothing_stops_the_start():
    with pytest.raises(IdentityProviderMisconfigured, match="used for nothing"):
        build_provider(parse(section(accept_tokens=False)))


# --- the section ------------------------------------------------------------------

def test_an_unknown_key_stops_the_start():
    with pytest.raises(IdentityProviderMisconfigured, match="acept_tokens"):
        parse(section(acept_tokens=True))


def test_a_bad_value_names_the_key():
    with pytest.raises(IdentityProviderMisconfigured, match="authorization"):
        parse(section(authorization="everybody"))


def test_environment_is_substituted_in_settings():
    config = parse(section(settings={"url": "https://${AS_HOST}/api",
                                     "nested": {"list": ["${AS_KEY}"]}}),
                   environ={"AS_HOST": "as.corp", "AS_KEY": "k-1"})
    assert config.settings == {"url": "https://as.corp/api", "nested": {"list": ["k-1"]}}


def test_a_missing_variable_stops_the_start_and_names_it_not_its_value():
    with pytest.raises(IdentityProviderMisconfigured) as refused:
        parse(section(settings={"key": "${CORP_AS_SECRET}", "url": "https://x"}), environ={})
    assert "CORP_AS_SECRET" in str(refused.value)
    assert "settings.key" in str(refused.value)


def test_environment_is_not_substituted_outside_settings():
    """Only the plugin's settings are templates; the name is an identifier."""
    with pytest.raises(IdentityProviderMisconfigured, match="name"):
        parse(section(name="${NAME}"), environ={"NAME": "x"})


def test_the_role_mapping_adds_up_and_falls_back_to_the_default():
    config = parse(section(role_mapping={"AS_ADMIN": "ADMIN", "AS_OPS": ["OPERATOR", "CLIENT"],
                                         "AS_USER": "CLIENT"}, default_role="GUEST"))
    assert config.mapped_roles(["AS_OPS", "AS_ADMIN"]) == ["ADMIN", "OPERATOR", "CLIENT"]
    assert config.mapped_roles(["AS_USER", "AS_OPS"]) == ["OPERATOR", "CLIENT"]
    assert config.mapped_roles(["SOMETHING_ELSE"]) == ["GUEST"]
    assert config.mapped_roles([]) == ["GUEST"]


def test_defaults_are_the_safe_ones():
    config = parse(section())
    assert config.accept_tokens is True
    assert config.password_sign_in is False
    assert config.authorization == "local"
    assert config.new_accounts == "refuse"
    assert config.decision_cache_seconds == 0


# --- the file ---------------------------------------------------------------------

def test_no_file_and_no_section_is_no_provider(tmp_path):
    assert load_section(tmp_path / "absent.yaml") is None
    assert load_section(write(tmp_path, "auth:\n  provider: local\n")) is None
    assert resolve(None, tmp_path / "absent.yaml") is None


def test_an_unreadable_file_stops_the_start(tmp_path):
    with pytest.raises(IdentityProviderMisconfigured, match="cannot read"):
        load_section(write(tmp_path, "identity_provider: [unclosed\n"))


def test_the_section_is_read_from_the_file_the_environment_names(tmp_path, monkeypatch):
    path = write(tmp_path, f"identity_provider:\n  name: corp-as\n  plugin: {MODULE}:TokensOnly\n")
    monkeypatch.setenv("AUTH_CONFIG_PATH", str(path))
    config = resolve(None)
    assert config.name == "corp-as"
    assert config.plugin == f"{MODULE}:TokensOnly"


def test_file_and_code_together_stop_the_start(tmp_path):
    path = write(tmp_path, f"identity_provider:\n  name: a\n  plugin: {MODULE}:TokensOnly\n")
    from_code = IdentityProviderConfig(name="b", plugin=TokensOnly({}))
    with pytest.raises(IdentityProviderMisconfigured, match="configured twice"):
        resolve(from_code, path)


def test_code_alone_is_used(tmp_path):
    from_code = IdentityProviderConfig(name="b", plugin=TokensOnly({}))
    assert resolve(from_code, tmp_path / "absent.yaml") is from_code


# --- finding the plugin -------------------------------------------------------------

def test_plugin_by_module_and_class():
    provider = build_provider(parse(section(settings={"a": 1})))
    assert isinstance(provider, TokensOnly)
    assert provider.settings == {"a": 1}


def test_plugin_by_entry_point_name():
    class Point:
        def __init__(self, name, target):
            self.name, self._target = name, target

        def load(self):
            return self._target

    discover = lambda: [Point("other", NotAProvider), Point("corp-as", Everything)]
    assert find_class("corp-as", discover) is Everything
    provider = build_provider(parse(section(plugin="corp-as", password_sign_in=True)), discover)
    assert isinstance(provider, Everything)


def test_missing_plugin_stops_the_start():
    with pytest.raises(IdentityProviderMisconfigured, match="no such entry point"):
        find_class("nobody-installed-this", lambda: [])
    with pytest.raises(IdentityProviderMisconfigured, match="cannot import"):
        find_class("no_such_module_anywhere:Provider")
    with pytest.raises(IdentityProviderMisconfigured, match="has no Missing"):
        find_class(f"{MODULE}:Missing")


def test_a_class_that_is_not_a_provider_is_refused():
    with pytest.raises(IdentityProviderMisconfigured, match="not a subclass"):
        find_class(f"{MODULE}:NotAProvider")


def test_a_failing_constructor_stops_the_start_without_repeating_its_text():
    with pytest.raises(IdentityProviderMisconfigured) as refused:
        build_provider(parse(section(plugin=f"{MODULE}:Exploding",
                                     settings={"secret": "s3cr3t-value"})))
    assert "RuntimeError" in str(refused.value)
    assert "s3cr3t-value" not in str(refused.value)


# --- the application ----------------------------------------------------------------

def test_the_runtime_is_built_and_replaced(tmp_path, caplog):
    path = write(tmp_path, f"identity_provider:\n  name: corp-as\n  plugin: {MODULE}:TokensOnly\n")
    with caplog.at_level("INFO"):
        runtime = identity_runtime.configure(None, path)
    assert identity_runtime.current() is runtime
    assert "Identity provider: corp-as (TokensOnly) -- tokens" in caplog.text

    # An application without a provider must not inherit the previous one's.
    assert identity_runtime.configure(None, tmp_path / "absent.yaml") is None
    assert identity_runtime.current() is None


def test_create_app_refuses_a_provider_configured_twice(tmp_path, monkeypatch):
    from keepup.factory import create_app
    from keepup.settings import KeepupSettings

    path = write(tmp_path, f"identity_provider:\n  name: a\n  plugin: {MODULE}:TokensOnly\n")
    monkeypatch.setenv("AUTH_CONFIG_PATH", str(path))
    settings = KeepupSettings(static_mounts=(), plugin_manager=None,
                              identity_provider=IdentityProviderConfig(name="b",
                                                                       plugin=TokensOnly({})))
    with pytest.raises(IdentityProviderMisconfigured, match="configured twice"):
        create_app(settings)
