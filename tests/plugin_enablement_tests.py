"""Which declared plugins run, and where that decision comes from.

Until this rule existed the answer was the union of the roles' plugin lists:
a plugin unrelated to any role had to be granted to one or it silently did
not start, and four of twenty declared plugins ran. The rule is pure --
configuration and environment in, decisions out -- so the cases are checked
without an application.

This suite arrived here from an application repository, where it was written
while the framework was a submodule of that tree and loaded
``plugins/enablement.py`` by path. The subject is the framework's own rule, so
it moved with the rule.
"""
import unittest

from keepup.plugins import enablement as pe


def config(*plugins):
    return {"plugins": [dict(p) for p in plugins],
            "roles": [{"name": "ADMIN", "plugins": ["alpha"]}]}


ALPHA_ON = {"id": "alpha", "name": "Alpha", "priority": 10, "enabled": True}
BETA_OFF = {"id": "beta", "name": "Beta", "priority": 20, "enabled": False}
GAMMA_UNSAID = {"id": "gamma", "name": "Gamma", "priority": 5}


class ResolveFromTheFileTests(unittest.TestCase):

    def test_the_flag_enables(self):
        d = pe.resolve(config(ALPHA_ON), {}).decisions["alpha"]
        self.assertTrue(d.enabled)
        self.assertEqual(pe.SOURCE_CONFIG, d.source)

    def test_the_flag_disables(self):
        d = pe.resolve(config(BETA_OFF), {}).decisions["beta"]
        self.assertFalse(d.enabled)
        self.assertEqual(pe.SOURCE_CONFIG, d.source)

    def test_no_flag_means_not_enabled(self):
        # Otherwise switching mechanism would start all twenty at once.
        d = pe.resolve(config(GAMMA_UNSAID), {}).decisions["gamma"]
        self.assertFalse(d.enabled)
        self.assertEqual(pe.SOURCE_DEFAULT, d.source)

    def test_a_role_listing_does_not_enable(self):
        # The role lists "alpha"; the flag says nothing.
        alpha = {"id": "alpha", "name": "Alpha"}
        self.assertFalse(pe.resolve(config(alpha), {}).decisions["alpha"].enabled)

    def test_decisions_keep_the_declaration_order(self):
        r = pe.resolve(config(BETA_OFF, ALPHA_ON, GAMMA_UNSAID), {})
        self.assertEqual(["beta", "alpha", "gamma"], list(r.decisions))

    def test_a_truthy_string_is_not_true(self):
        d = pe.resolve(config({"id": "alpha", "enabled": "false"}), {}).decisions["alpha"]
        self.assertFalse(d.enabled)


class ResolveFromTheEnvironmentTests(unittest.TestCase):

    def test_the_environment_enables_what_the_file_does_not(self):
        r = pe.resolve(config(BETA_OFF), {pe.ENABLE_ENV: "beta"})
        self.assertTrue(r.decisions["beta"].enabled)
        self.assertEqual(pe.SOURCE_ENV, r.decisions["beta"].source)

    def test_the_environment_disables_what_the_file_enables(self):
        r = pe.resolve(config(ALPHA_ON), {pe.DISABLE_ENV: "alpha"})
        self.assertFalse(r.decisions["alpha"].enabled)
        self.assertEqual(pe.SOURCE_ENV, r.decisions["alpha"].source)

    def test_named_in_both_lists_is_disabled(self):
        r = pe.resolve(config(ALPHA_ON), {pe.ENABLE_ENV: "alpha", pe.DISABLE_ENV: "alpha"})
        self.assertFalse(r.decisions["alpha"].enabled)

    def test_lists_tolerate_spaces_and_empty_items(self):
        r = pe.resolve(config(BETA_OFF, GAMMA_UNSAID), {pe.ENABLE_ENV: " beta , ,gamma,"})
        self.assertTrue(r.decisions["beta"].enabled)
        self.assertTrue(r.decisions["gamma"].enabled)

    def test_an_unknown_id_is_reported_not_fatal(self):
        r = pe.resolve(config(ALPHA_ON), {pe.ENABLE_ENV: "nobody", pe.DISABLE_ENV: "ghost"})
        self.assertEqual(["nobody", "ghost"], r.unknown)
        self.assertNotIn("nobody", r.decisions)
        self.assertTrue(r.decisions["alpha"].enabled)

    def test_an_empty_variable_changes_nothing(self):
        r = pe.resolve(config(ALPHA_ON), {pe.ENABLE_ENV: "", pe.DISABLE_ENV: ""})
        self.assertEqual(pe.SOURCE_CONFIG, r.decisions["alpha"].source)
        self.assertEqual([], r.unknown)


class EnabledIdsTests(unittest.TestCase):

    def test_only_the_enabled_in_declaration_order(self):
        r = pe.resolve(config(BETA_OFF, ALPHA_ON, GAMMA_UNSAID), {pe.ENABLE_ENV: "gamma"})
        self.assertEqual(["alpha", "gamma"], pe.enabled_ids(r))


class SummaryLineTests(unittest.TestCase):

    def test_names_the_count_and_the_not_enabled(self):
        r = pe.resolve(config(ALPHA_ON, BETA_OFF, GAMMA_UNSAID), {})
        line = pe.summary_line(r)
        self.assertIn("1 of 3", line)
        self.assertIn("beta", line)
        self.assertIn("gamma", line)
        self.assertNotIn("alpha", line.split("not enabled")[1])

    def test_everything_enabled_says_so(self):
        r = pe.resolve(config(ALPHA_ON), {})
        self.assertIn("1 of 1", pe.summary_line(r))
        self.assertNotIn("not enabled:", pe.summary_line(r))


class FakeManager:
    def __init__(self, loaded=(), initialized=()):
        self.plugins = {i: object() for i in loaded}
        self.loaded_plugins = {i: self.plugins[i] for i in initialized}


class StatusReportTests(unittest.TestCase):

    def test_one_row_per_declared_plugin_with_its_state(self):
        r = pe.resolve(config(ALPHA_ON, BETA_OFF, GAMMA_UNSAID), {pe.ENABLE_ENV: "gamma"})
        rows = pe.status_report(config(ALPHA_ON, BETA_OFF, GAMMA_UNSAID), r,
                                FakeManager(loaded=["alpha", "gamma"], initialized=["alpha"]))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(["alpha", "beta", "gamma"], [row["id"] for row in rows])
        self.assertEqual({"id": "alpha", "name": "Alpha", "priority": 10, "enabled": True,
                          "source": pe.SOURCE_CONFIG, "loaded": True, "initialized": True},
                         {k: by_id["alpha"][k] for k in
                          ("id", "name", "priority", "enabled", "source", "loaded", "initialized")})
        self.assertEqual(False, by_id["beta"]["enabled"])
        self.assertEqual(False, by_id["beta"]["loaded"])
        # Enabled by the environment, loaded, but initialize() failed: the one
        # case only visible in the log until now.
        self.assertEqual(pe.SOURCE_ENV, by_id["gamma"]["source"])
        self.assertTrue(by_id["gamma"]["loaded"])
        self.assertFalse(by_id["gamma"]["initialized"])

    def test_a_missing_priority_reads_as_zero(self):
        cfg = config({"id": "alpha", "enabled": True})
        rows = pe.status_report(cfg, pe.resolve(cfg, {}), FakeManager())
        self.assertEqual(0, rows[0]["priority"])
        self.assertEqual("alpha", rows[0]["name"])


class ResolveFromThePanelTests(unittest.TestCase):
    """A third source, stored in the deployment's own database.

    The file is committed and shared between deployments; the environment
    belongs to one stand and protects it. The panel sits between: stronger
    than the file, weaker than the environment.
    """

    def test_an_override_enables_what_the_file_disables(self):
        d = pe.resolve(config(BETA_OFF), {}, overrides={"beta": True}).decisions["beta"]
        self.assertTrue(d.enabled)
        self.assertEqual(pe.SOURCE_PANEL, d.source)

    def test_an_override_disables_what_the_file_enables(self):
        d = pe.resolve(config(ALPHA_ON), {}, overrides={"alpha": False}).decisions["alpha"]
        self.assertFalse(d.enabled)
        self.assertEqual(pe.SOURCE_PANEL, d.source)

    def test_the_environment_disable_list_wins_over_the_panel(self):
        d = pe.resolve(config(ALPHA_ON), {pe.DISABLE_ENV: "alpha"},
                       overrides={"alpha": True}).decisions["alpha"]
        self.assertFalse(d.enabled)
        self.assertEqual(pe.SOURCE_ENV, d.source)

    def test_the_environment_enable_list_wins_over_the_panel(self):
        d = pe.resolve(config(BETA_OFF), {pe.ENABLE_ENV: "beta"},
                       overrides={"beta": False}).decisions["beta"]
        self.assertTrue(d.enabled)
        self.assertEqual(pe.SOURCE_ENV, d.source)

    def test_an_override_for_an_undeclared_plugin_changes_nothing(self):
        r = pe.resolve(config(ALPHA_ON), {}, overrides={"nonesuch": True})
        self.assertEqual(["alpha"], list(r.decisions))

    def test_no_overrides_behaves_as_before(self):
        self.assertEqual(pe.resolve(config(ALPHA_ON), {}).decisions["alpha"].source,
                         pe.resolve(config(ALPHA_ON), {}, overrides={}).decisions["alpha"].source)


class DecidedByTheEnvironmentTests(unittest.TestCase):
    """What the panel's refusal is built on."""

    def test_a_plugin_in_either_list_is_the_deployment_s_to_decide(self):
        self.assertTrue(pe.decided_by_environment("alpha", {pe.DISABLE_ENV: "alpha,beta"}))
        self.assertTrue(pe.decided_by_environment("beta", {pe.ENABLE_ENV: "beta"}))

    def test_a_plugin_named_nowhere_is_not(self):
        self.assertFalse(pe.decided_by_environment("alpha", {pe.ENABLE_ENV: "beta"}))
        self.assertFalse(pe.decided_by_environment("alpha", {}))


class PendingRestartTests(unittest.TestCase):
    """The routes are built at start-up, so a new decision waits for one.

    The flag is the difference between the snapshot taken at start-up and the
    decision in force now -- not a stored "awaiting restart" column, which
    would have to be cleared when the override is put back and would not be.
    """

    def _rows(self, cfg, environ, overrides, manager):
        running = pe.resolve(cfg, environ)
        desired = pe.resolve(cfg, environ, overrides=overrides)
        return {r["id"]: r for r in pe.status_report(
            cfg, running, manager, desired=desired, environ=environ)}

    def test_a_plugin_switched_off_is_marked_and_still_running(self):
        cfg = config(ALPHA_ON)
        rows = self._rows(cfg, {}, {"alpha": False},
                          FakeManager(loaded=["alpha"], initialized=["alpha"]))
        self.assertTrue(rows["alpha"]["pending_restart"])
        self.assertTrue(rows["alpha"]["enabled"])        # what is running
        self.assertFalse(rows["alpha"]["desired_enabled"])
        self.assertTrue(rows["alpha"]["initialized"])

    def test_a_plugin_switched_on_is_marked_and_not_yet_running(self):
        cfg = config(BETA_OFF)
        rows = self._rows(cfg, {}, {"beta": True}, FakeManager())
        self.assertTrue(rows["beta"]["pending_restart"])
        self.assertFalse(rows["beta"]["enabled"])
        self.assertTrue(rows["beta"]["desired_enabled"])

    def test_putting_the_decision_back_clears_the_mark(self):
        cfg = config(ALPHA_ON)
        rows = self._rows(cfg, {}, {"alpha": True},
                          FakeManager(loaded=["alpha"], initialized=["alpha"]))
        self.assertFalse(rows["alpha"]["pending_restart"])

    def test_without_a_desired_resolution_nothing_is_pending(self):
        cfg = config(ALPHA_ON)
        rows = {r["id"]: r for r in pe.status_report(
            cfg, pe.resolve(cfg, {}), FakeManager(loaded=["alpha"], initialized=["alpha"]))}
        self.assertFalse(rows["alpha"]["pending_restart"])
        self.assertTrue(rows["alpha"]["desired_enabled"])

    def test_the_row_says_whether_the_deployment_decides(self):
        cfg = config(ALPHA_ON)
        rows = self._rows(cfg, {pe.DISABLE_ENV: "alpha"}, {}, FakeManager())
        self.assertTrue(rows["alpha"]["decided_by_environment"])
        rows = self._rows(cfg, {}, {}, FakeManager())
        self.assertFalse(rows["alpha"]["decided_by_environment"])
