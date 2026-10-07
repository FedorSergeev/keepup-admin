"""The policy a browser holds the panel to (keepup-93).

The panel is an ordinary page with a session cookie in it, and every library
that has ever been loaded next to one is one escaping mistake away from running
somebody else's script in that session. The other security headers in
``keepup.factory`` close a door each; a Content-Security-Policy closes the class
of them: a script the page did not load itself does not run at all, and an
extension the framework's own sections never use -- an ``onclick`` attribute, an
inline ``<script>`` -- is refused by the browser rather than trusted.

The policy below describes the panel as the framework ships it, and every part
of it is a fact about the shipped front end rather than a preference:

* ``script-src 'self'`` -- the shell, the sections and the libraries come from
  the application's own address; nothing is loaded from a CDN. This is the
  directive the task exists for, so there is no ``'unsafe-inline'`` in it: an
  inline handler is refused, and a section asks the shell to act through
  ``KeepupActions`` instead (``keepup/static/js/main_new.js``).
* ``style-src 'self' 'unsafe-inline'`` -- the one exception on purpose. Sections
  and the panel set style attributes from data (a bar's width, a badge's tone),
  Tailwind builds its rules into a ``<style>`` element at run time, and both are
  the kind of value a style rule cannot express. Styles cannot read the session
  cookie or send it anywhere, so this is a far smaller door than the script one.
* ``frame-ancestors 'self'`` and ``form-action 'self'`` restate X-Frame-Options
  and keep a form the page did not render from posting the session elsewhere.
* ``connect-src 'self'`` -- sections talk to their own address and to the
  replicas' sockets on it, nowhere else.

An application whose own sections still carry inline handlers replaces this
through ``KeepupSettings.content_security_policy``, and one that is not ready to
have its panel held to a policy sends it as a report
(``KeepupSettings.csp_report_only``) until its sections are converted. The
constant is public so that such a policy can extend this one rather than start
from a blank string.
"""

#: What an application may import from this module. Everything else is
#: internal and may change without notice -- see doc/keepup.md.
__all__ = ["DEFAULT_CONTENT_SECURITY_POLICY"]


#: The policy the panel is served with unless the application says otherwise.
DEFAULT_CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'self'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self'",
))
