"""The API schema and the documentation pages built from it (keepup-96).

FastAPI serves its OpenAPI schema at /openapi.json to anybody, and the schema
describes every route of the application -- the administrative ones with their
parameters and bodies. For somebody outside that is a map of where to knock.
FastAPI has no notion of who is asking, so the framework takes both routes over:

- the schema answers an administrator only, like every other administrative
  route of the framework, unless the application opens it on purpose
  (``KeepupSettings.openapi_public``) or removes it (``openapi_url=None``);
- the documentation pages stay public shells: they fetch the schema from the
  browser, with the panel's cookie, so an administrator sees the API and anybody
  else sees the refusal.

Building the schema in code -- ``app.openapi()``, which ci/generate_openapi.py
calls -- is untouched: it never went through the route.
"""

from fastapi import Depends
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.responses import JSONResponse

from keepup.auth.dependencies import get_current_admin

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = ["OAUTH2_REDIRECT_URL", "register_api_documentation"]

#: Where Swagger UI returns from an OAuth2 sign-in; FastAPI's own default.
OAUTH2_REDIRECT_URL = "/docs/oauth2-redirect"


def register_api_documentation(app, settings) -> None:
    """The schema route and the documentation pages, as the settings ask.

    Called right after the application is created: FastAPI used to register
    these in its constructor, ahead of any route of the application, and a page
    registered later could be shadowed by a route that happens to match it.
    """
    schema_url = settings.openapi_url
    if not schema_url:
        # No schema, no pages: they would have nothing to show.
        return

    if settings.openapi_public:
        async def api_schema():
            return JSONResponse(app.openapi())
    else:
        async def api_schema(admin: dict = Depends(get_current_admin)):
            return JSONResponse(app.openapi())

    app.add_api_route(schema_url, api_schema, methods=["GET"], include_in_schema=False)

    if settings.docs_url:
        async def swagger_page():
            return get_swagger_ui_html(openapi_url=schema_url, title=f"{app.title} - API",
                                       oauth2_redirect_url=OAUTH2_REDIRECT_URL)

        async def swagger_redirect():
            return get_swagger_ui_oauth2_redirect_html()

        app.add_api_route(settings.docs_url, swagger_page, methods=["GET"],
                          include_in_schema=False)
        app.add_api_route(OAUTH2_REDIRECT_URL, swagger_redirect, methods=["GET"],
                          include_in_schema=False)

    if settings.redoc_url:
        async def redoc_page():
            return get_redoc_html(openapi_url=schema_url, title=f"{app.title} - API")

        app.add_api_route(settings.redoc_url, redoc_page, methods=["GET"],
                          include_in_schema=False)
