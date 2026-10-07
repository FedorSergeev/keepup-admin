/*
 * Adaptive layout bootstrap (keepup-93).
 *
 * Runs while the head is parsed, after the theme stylesheets, so
 * --layout-breakpoint already resolves and the narrow layout never flashes as
 * the desktop one. It also feeds the same token to Tailwind, so the md:
 * utilities the sections use switch at the same width as the chrome instead of
 * at Tailwind's own 768px. From here on main_new.js owns the class.
 *
 * A file of its own rather than a <script> in the page: an inline script needs
 * a nonce or a hash to survive the panel's Content-Security-Policy, and a hash
 * changes with every edit and with every application that copies the page
 * (keepup-93, keepup/security.py). A synchronous script in the head blocks the
 * parser exactly as the inline one did, and it waits for the stylesheets above
 * it, so getComputedStyle still reads the theme's token.
 */
(function () {
    var declared = getComputedStyle(document.documentElement)
        .getPropertyValue('--layout-breakpoint');
    var breakpoint = parseFloat(declared) || 768;
    if (window.matchMedia('(max-width: ' + (breakpoint - 0.02) + 'px)').matches) {
        document.documentElement.classList.add('is-narrow');
    }
    if (window.tailwind) {
        window.tailwind.config = {
            theme: { extend: { screens: { md: breakpoint + 'px' } } }
        };
    }
})();
