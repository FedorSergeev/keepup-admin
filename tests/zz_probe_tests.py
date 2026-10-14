def test_probe_ids():
    from keepup.kernel import security
    from keepup_auth.dependencies import get_panel_user
    from plugin_runtime_tests import running

    print("test's:", id(get_panel_user), get_panel_user.__module__)
    print("security's:", id(security.subject_dependency()), security.subject_dependency().__module__)
    client = running([{"path": "/api/probe/plain", "methods": ["GET"],
                       "handler": lambda: "ok"}])
    print("answer:", client.get("/api/probe/plain").status_code)
