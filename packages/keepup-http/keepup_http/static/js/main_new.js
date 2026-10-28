
let currentUser = null;

// endregion

/* ==========================================================================
   Panel session.

   The session is an httpOnly cookie the page cannot read. What the page keeps
   under 'authToken' is the marker below, so sections that check for a token and
   send `Bearer ${token}` keep working: the server reads `Bearer cookie` as no
   header and uses the cookie. A change made by cookie must carry the CSRF value,
   which the wrapper below adds to every same-origin request that is not a read.
   ========================================================================== */
const SESSION_MARKER = 'cookie';
// A cookie belongs to a host, not to a port: several keepup panels on one host
// used to write the same `ss_csrf`, and whichever signed in last left the
// others unable to prove a change came from their own page. Since 0.7.0 the
// CSRF cookie carries the application's own name, and the server publishes
// both names in /api/public/config -- they are not credentials. Over HTTPS the
// name also carries the `__Host-` prefix (keepup-92), which belongs to this
// exact host, so a sibling subdomain cannot plant it; the plain names of
// 0.6.0 are kept as a fallback for a page whose config did not arrive.
const LEGACY_CSRF_COOKIES = ['__Host-ss_csrf', 'ss_csrf'];
const CSRF_HEADER = 'X-CSRF-Token';
const CSRF_SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

let publicConfigPromise = null;
let csrfCookieNamesPromise = null;

function readCookie(name) {
    const prefix = name + '=';
    const found = (document.cookie || '').split(';')
        .map(part => part.trim())
        .find(part => part.startsWith(prefix));
    return found ? decodeURIComponent(found.slice(prefix.length)) : null;
}

function csrfCookieNames() {
    if (!csrfCookieNamesPromise) {
        csrfCookieNamesPromise = Promise.resolve()
            .then(() => (typeof loadPublicConfig === 'function' ? loadPublicConfig() : null))
            .then(config => {
                const base = config && config.panel_cookies && config.panel_cookies.csrf;
                return base ? ['__Host-' + base, base] : LEGACY_CSRF_COOKIES;
            })
            .catch(() => LEGACY_CSRF_COOKIES);
    }
    return csrfCookieNamesPromise;
}

async function readCsrfCookie() {
    for (const name of await csrfCookieNames()) {
        const value = readCookie(name);
        if (value) return value;
    }
    return null;
}

function isSameOriginUrl(url) {
    try {
        return new URL(url, window.location.href).origin === window.location.origin;
    } catch (error) {
        return false;
    }
}

function installCsrfFetch(target) {
    const originalFetch = target.fetch;
    if (!originalFetch || originalFetch.csrfAware) {
        return;
    }
    const wrapped = async function (input, init) {
        const request = typeof Request !== 'undefined' && input instanceof Request ? input : null;
        const method = String((init && init.method) || (request && request.method) || 'GET').toUpperCase();
        const url = request ? request.url : String(input);
        if (!CSRF_SAFE_METHODS.includes(method) && isSameOriginUrl(url)) {
            const csrf = await readCsrfCookie();
            if (csrf) {
                const headers = new Headers((init && init.headers) || (request && request.headers) || undefined);
                if (!headers.has(CSRF_HEADER)) {
                    headers.set(CSRF_HEADER, csrf);
                }
                init = Object.assign({}, init || {}, { headers });
            }
        }
        return originalFetch.call(target, input, init);
    };
    wrapped.csrfAware = true;
    target.fetch = wrapped;
}

installCsrfFetch(window);

const ROLE_ADMIN = 'ADMIN';
const ROLE_CLIENT = 'CLIENT';

// --- The roles a user holds ------------------------------------------------------
//
// A user holds a set of roles, and the panel decides by the set: somebody who is
// both an administrator and a client holds two, and comparing one field with
// ROLE_ADMIN would have made them choose. The set is the only answer, in the
// answers and in the table (keepup-51, keepup-168). Cut out by these markers and
// executed under node -- see tests/user_roles_tests.py.
function userRolesOf(user) {
    return user && Array.isArray(user.roles) ? user.roles : [];
}

function userHasRole(user, role) {
    return userRolesOf(user).indexOf(role) !== -1;
}

function currentUserIsAdmin() {
    return userHasRole(currentUser, ROLE_ADMIN);
}

window.AppRoles = { of: userRolesOf, has: userHasRole };
// --- end of the roles a user holds ----------------------------------------------

/* ==========================================================================
   What a section asks the shell to do (keepup-93).

   A section used to put JavaScript in its markup -- an onclick attribute that
   called editUser(7) -- and a browser served the panel with a
   Content-Security-Policy refuses to run that at all (keepup/security.py): an
   attribute is a script the page did not load, and it is exactly what a
   stored-XSS hole (keepup-62) needs in order to become a running one. A section
   now names an action it registered here and carries the values its handler
   needs in data-* attributes:

       <button data-action="users.edit" data-user-id="7">Edit</button>

       KeepupActions.register({
           'users.edit': (element) => editUser(Number(element.dataset.userId)),
       });

   One listener per event kind on the document serves every section, including
   the ones loaded later: nothing is bound per render, so a rebuilt table needs
   no rebinding. Only a registered name is ever called -- markup can name an
   action, never a function -- and a name nothing registered does nothing.
   ========================================================================== */
// --- what a section asks the shell to do (keepup-93)
const KEEPUP_ACTION_ATTRIBUTE = 'data-action';

//: The elements whose action belongs to a change or a submit, not to a click.
//: A click on the button of a form whose action sits on the form itself would
//: otherwise run the same handler twice -- once for the click and once for the
//: submit it causes. A form answers its submit, a field its change.
const KEEPUP_CONTROL_TAGS = ['FORM', 'INPUT', 'SELECT', 'TEXTAREA', 'OPTION'];

const KeepupActions = (function () {
    const handlers = new Map();

    function register(name, handler) {
        if (name && typeof name === 'object') {
            Object.keys(name).forEach(function (key) { handlers.set(key, name[key]); });
            return;
        }
        handlers.set(name, handler);
    }

    function actionOf(target) {
        if (!target || typeof target.closest !== 'function') return null;
        const element = target.closest('[' + KEEPUP_ACTION_ATTRIBUTE + ']');
        return element && handlers.has(element.getAttribute(KEEPUP_ACTION_ATTRIBUTE))
            ? element : null;
    }

    function handle(event) {
        const element = actionOf(event.target);
        if (!element) return;
        if (event.type === 'click' && KEEPUP_CONTROL_TAGS.indexOf(element.tagName) !== -1) return;
        const handler = handlers.get(element.getAttribute(KEEPUP_ACTION_ATTRIBUTE));
        // A form is answered here rather than sent, and a link that only names
        // an action is a button wearing an anchor.
        if (event.type === 'submit' || element.tagName === 'A') event.preventDefault();
        handler(element, event);
    }

    document.addEventListener('click', handle);
    document.addEventListener('change', handle);
    document.addEventListener('submit', handle);

    return { register: register };
})();
window.KeepupActions = KeepupActions;
// --- end of what a section asks the shell to do --------------------------------


/* ==========================================================================
   Adaptive layout.

   The narrow/wide boundary is a design token, --layout-breakpoint, so a theme
   can move it. A media query cannot read a custom property and there is no CSS
   build step here to expand one, so the value is read once and the resulting
   state is published as the .is-narrow class on <html>; every adaptive rule in
   main_new.css matches on that class. The theme's head script sets the class
   before the first paint -- this module only takes over and keeps it in step.
   ========================================================================== */
const LAYOUT_NARROW_CLASS = 'is-narrow';
const LAYOUT_BREAKPOINT_FALLBACK = 768;
const LAYOUT_CHANGED_EVENT = 'app:layout:changed';

let layoutMediaQuery = null;

function readLayoutBreakpoint() {
    const declared = getComputedStyle(document.documentElement)
        .getPropertyValue('--layout-breakpoint');
    const value = parseFloat(declared);
    return Number.isFinite(value) && value > 0 ? value : LAYOUT_BREAKPOINT_FALLBACK;
}

function isNarrowLayout() {
    return document.documentElement.classList.contains(LAYOUT_NARROW_CLASS);
}

function applyLayoutState(narrow) {
    const changed = narrow !== isNarrowLayout();
    document.documentElement.classList.toggle(LAYOUT_NARROW_CLASS, narrow);
    if (!changed) return;
    AppEventTarget.dispatchEvent(new CustomEvent(LAYOUT_CHANGED_EVENT, {
        detail: { narrow }
    }));
}

function initLayoutBreakpoint() {
    // 0.02px below the token: matchMedia ranges are inclusive, so a plain
    // max-width equal to the breakpoint would make both layouts true at exactly
    // that width, and the md: utilities Tailwind builds from the same value
    // start at the breakpoint itself.
    layoutMediaQuery = window.matchMedia(`(max-width: ${readLayoutBreakpoint() - 0.02}px)`);
    applyLayoutState(layoutMediaQuery.matches);
    layoutMediaQuery.addEventListener('change', event => applyLayoutState(event.matches));
}

/* --------------------------------------------------------------------------
   Navigation drawer. Below the breakpoint the sidebar floats over the content
   instead of sitting beside it, so it needs a way out that a permanent column
   never did: the backdrop, Esc, and picking a section all close it. Without
   the last one the user lands on the section they chose behind a menu that is
   still covering it.
   -------------------------------------------------------------------------- */
const NAV_OPEN_CLASS = 'nav-open';
const NAV_LOCKED_CLASS = 'nav-locked';

function isNavOpen() {
    return document.documentElement.classList.contains(NAV_OPEN_CLASS);
}

function setNavOpen(open) {
    document.documentElement.classList.toggle(NAV_OPEN_CLASS, open);
    document.body.classList.toggle(NAV_LOCKED_CLASS, open);
    const toggle = document.getElementById('sidebarToggle');
    if (toggle) {
        toggle.setAttribute('aria-expanded', String(open));
    }
}

function openNav() {
    setNavOpen(true);
}

function closeNav() {
    if (isNavOpen()) setNavOpen(false);
}

function toggleNav() {
    setNavOpen(!isNavOpen());
}

function initNavDrawer() {
    const toggle = document.getElementById('sidebarToggle');
    if (toggle) {
        toggle.addEventListener('click', toggleNav);
    }

    const backdrop = document.getElementById('nav-backdrop');
    if (backdrop) {
        backdrop.addEventListener('click', closeNav);
    }

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeNav();
    });

    // Crossing the breakpoint either way leaves the drawer closed: open is a
    // state of the narrow layout only, and carrying it into the wide one would
    // leave the backdrop covering a page that has its sidebar back.
    AppEventTarget.addEventListener(LAYOUT_CHANGED_EVENT, closeNav);
}

/* --------------------------------------------------------------------------
   Responsive tables.

   A section opts in by putting `responsive-table` on the table and nothing
   else: no media query of its own, no card markup, no per-section JS. What the
   helper does depends on how many columns the table has, because the two ways
   out of a narrow screen suit different tables. Up to --table-card-columns the
   row folds into a card, and each cell is labelled with its column heading --
   read from <thead>, so renaming a column renames the label instead of leaving
   a stale one hand-written in the row template. Wider than that, folding
   produces a card nobody can scan, so the table keeps its shape and scrolls
   inside its own container with the first column pinned.

   Sections rebuild their tables through innerHTML, and often, so this cannot be
   a one-off call at init: initResponsiveTables watches the module container and
   re-runs. Work is skipped where it has already been done, so a re-run over an
   unchanged table costs a query and nothing else.
   -------------------------------------------------------------------------- */
const TABLE_HELPER_CLASS = 'responsive-table';
const TABLE_CARD_COLUMNS_FALLBACK = 6;
const TABLE_ENHANCE_DELAY_MS = 50;

let tableEnhanceTimer = null;

function readTableCardColumns() {
    const declared = getComputedStyle(document.documentElement)
        .getPropertyValue('--table-card-columns');
    const value = parseInt(declared, 10);
    return Number.isFinite(value) && value > 0 ? value : TABLE_CARD_COLUMNS_FALLBACK;
}

function wrapForHorizontalScroll(table) {
    const parent = table.parentElement;
    if (!parent || parent.classList.contains('table-scroll')) return;
    // Several sections already wrapped their table in an overflow-x container
    // of their own. Adopt it rather than nesting a second scrolling box inside
    // it, which would leave two scrollbars for one table.
    if (parent.classList.contains('overflow-x-auto')) {
        parent.classList.add('table-scroll');
        return;
    }
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll';
    parent.insertBefore(wrapper, table);
    wrapper.appendChild(table);
}

function labelCardCells(table, headers) {
    table.querySelectorAll('tbody tr').forEach(row => {
        const cells = row.children;
        // A row that does not line up with the headings is not a data row --
        // an empty-state or a spanning summary row. Labelling it by position
        // would put the wrong heading in front of its text.
        if (cells.length !== headers.length) return;
        Array.from(cells).forEach((cell, index) => {
            if (cell.dataset.label === undefined && headers[index]) {
                cell.dataset.label = headers[index];
            }
        });
    });
}

function enhanceTable(table) {
    const headers = Array.from(table.querySelectorAll('thead th'))
        .map(th => th.textContent.trim());
    if (!headers.length) return;

    if (headers.length > readTableCardColumns()) {
        table.dataset.responsiveMode = 'scroll';
        wrapForHorizontalScroll(table);
        return;
    }

    table.dataset.responsiveMode = 'cards';
    labelCardCells(table, headers);
}

function enhanceTables(root) {
    const scope = (root && root.querySelectorAll) ? root : document;
    if (scope.matches && scope.matches(`table.${TABLE_HELPER_CLASS}`)) {
        enhanceTable(scope);
        return;
    }
    scope.querySelectorAll(`table.${TABLE_HELPER_CLASS}`).forEach(enhanceTable);
}

function scheduleTableEnhance() {
    if (tableEnhanceTimer) return;
    tableEnhanceTimer = setTimeout(() => {
        tableEnhanceTimer = null;
        enhanceTables(document);
    }, TABLE_ENHANCE_DELAY_MS);
}

function initResponsiveTables() {
    enhanceTables(document);

    const container = document.getElementById('modules-container');
    if (!container) return;

    // childList only: the helper's own writes are attributes, so it does not
    // wake itself. Wrapping a wide table is a child change and costs exactly
    // one extra pass, which then finds the wrapper already there.
    new MutationObserver(scheduleTableEnhance)
        .observe(container, { childList: true, subtree: true });
}

// The one surface sections are meant to use. Everything else about the layout
// is CSS matching on .is-narrow.
window.AdminLayout = {
    NARROW_CLASS: LAYOUT_NARROW_CLASS,
    LAYOUT_CHANGED: LAYOUT_CHANGED_EVENT,
    isNarrow: isNarrowLayout,
    breakpoint: readLayoutBreakpoint,
    openNav,
    closeNav,
    toggleNav,
    enhanceTables
};




function toggleSidebar() {
    // In the narrow layout the panel is a drawer, so the chevron closes it
    // rather than collapsing it. Collapsing there would both do nothing visible
    // and overwrite the wide layout's saved state from a screen that has no
    // wide layout.
    if (isNarrowLayout()) {
        closeNav();
        return;
    }

    const sidebar = document.getElementById('sidebar');

    sidebar.classList.toggle('sidebar-collapsed');
    const isCollapsed = sidebar.classList.contains('sidebar-collapsed');
    localStorage.setItem('sidebarCollapsed', isCollapsed);

    // The collapsed menu shows icons only and the search field goes with the
    // text, so a filter left on would look like sections that went missing.
    if (isCollapsed) clearModuleSearch();
}

    // Restore menu state on page load
    document.addEventListener('DOMContentLoaded', function() {
        initLayoutBreakpoint();
        applyThemeBranding();
        const sidebar = document.getElementById('sidebar');
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';

    if (isCollapsed) {
        sidebar.classList.add('sidebar-collapsed');
    }
        // Add a handler for the menu toggle button
        const toggleButton = document.getElementById('toggleSidebar');
        if (toggleButton) {
            toggleButton.addEventListener('click', toggleSidebar);
        }

        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    });

async function applyThemeBranding() {
    // The markup already carries the name baked in at build time by
    // replace_placeholder.sh. A theme without branding leaves it alone, and so
    // does a failed request -- the corner is never left empty.
    let brand;
    try {
        const response = await fetch('/api/theme/brand');
        if (!response.ok) return;
        brand = await response.json();
    } catch (error) {
        console.warn('Failed to fetch theme brand:', error);
        return;
    }
    if (!brand || (!brand.brand_name && !brand.logo_url)) return;

    const name = brand.brand_name || document.title;
    if (brand.brand_name) {
        document.title = brand.brand_name;
    }

    document.querySelectorAll('.logo-text').forEach(element => {
        if (brand.logo_url) {
            const logo = document.createElement('img');
            logo.src = brand.logo_url;
            logo.alt = name;
            logo.className = 'logo-image';
            element.replaceChildren(logo);
        } else {
            element.textContent = brand.brand_name;
        }
    });
}

function refreshEntireUI() {
    if (window.goodsModule) {
        window.goodsModule.refreshEntireUI();
    }
    updateUIAfterAuth();
    feather.replace();
}

function handleNavigation(e) {
    e.preventDefault();

    if (!checkAuth()) return;

    document.querySelectorAll('.nav-item').forEach(navItem => {
        navItem.classList.remove('active');
    });
    this.classList.add('active');

    const targetId = this.dataset.target;

    showSection(targetId);
    const sectionName = this.querySelector('span').textContent;
    document.getElementById('current-section-title').textContent = sectionName;

    // IMPORTANT: use appSections to get the route
    const routePath = appSections.getRoutePath(targetId);
    if (routePath) {
        appRouter.navigate(routePath);
    }

    const sectionInfo = appSections.getSection(targetId);
    if (sectionInfo && sectionInfo.handler) {
        sectionInfo.handler();
    }

    closeNav();
}

// Central routing system
const appRouter = {
    routes: new Map(),
    currentRoute: null,
    isNavigating: false, // Flag to prevent recursion

    registerRoute(path, handler) {
        console.log(`Registering route: ${path}`);
        this.routes.set(path, handler);
    },

    unregisterRoute(path) {
        this.routes.delete(path);
    },

    handleUrlChange() {
    if (this.isNavigating) return;

    const currentPath = window.location.pathname;
    console.log('Router: URL changed to', currentPath);
    console.log('Available routes:', Array.from(this.routes.keys()));

    if (this.routes.has(currentPath)) {
        const handler = this.routes.get(currentPath);
        console.log('Router: Found exact match, calling handler');
        this.isNavigating = true;
        try {
            handler();
            this.currentRoute = currentPath;
        } finally {
            setTimeout(() => {
                this.isNavigating = false;
            }, 100);
        }
        return;
    }

    let matched = false;
    for (const [route, handler] of this.routes) {
        if (currentPath.startsWith(route)) {
            console.log('Router: Found partial match', route);
            this.isNavigating = true;
            try {
                handler();
                this.currentRoute = currentPath;
                matched = true;
            } finally {
                setTimeout(() => {
                    this.isNavigating = false;
                }, 100);
            }
            break;
        }
    }

    if (!matched) {
        console.log('Router: No route found for', currentPath, 'redirecting to /selfcare');
        if (currentPath.includes('/modules/')) {
            console.log('Router: Module route not yet registered, waiting...');
            setTimeout(() => {
                this.handleUrlChange();
            }, 1000);
        } else {
            this.navigate('/selfcare');
        }
    }
},

    navigate(path) {
        if (this.isNavigating) {
            console.log('Router: Navigation already in progress, skipping');
            return;
        }

        if (window.location.pathname === path) {
            console.log('Router: Already on target path, skipping');
            return;
        }

        this.isNavigating = true;
        try {
            window.history.pushState({}, '', path);
            this.handleUrlChange();
        } finally {
            setTimeout(() => {
                this.isNavigating = false;
            }, 100);
        }
    },

    getCurrentRoute() {
        return this.currentRoute;
    },

    getRoutes() {
        return Array.from(this.routes.keys());
    }
};

// Make it globally accessible for modules
window.appRouter = appRouter;

function setupRouter() {
    appRouter.registerRoute('/selfcare/modules/goods', () => {
        console.log('Router: Handling goods route');
        showGoodsSection();
    });

    appRouter.registerRoute('/selfcare', () => {
        console.log('Router: Handling main selfcare route');
        const defaultSection = document.querySelector('.nav-item');
        if (defaultSection) {
            defaultSection.click();
        }
    });

    appRouter.registerRoute('/', () => {
        appRouter.navigate('/selfcare');
    });

    appRouter.registerRoute('/selfcare/modules/', () => {
        // This route will be overridden by modules
    });

    window.addEventListener('popstate', function(event) {
        appRouter.handleUrlChange();
    });

    document.addEventListener('click', function(e) {
        const link = e.target.closest('a');
        if (link && link.href) {
            const url = new URL(link.href);
            const path = url.pathname;

            if (path.startsWith('/selfcare') || path === '/') {
                e.preventDefault();
                appRouter.navigate(path);
            }
        }
    });
}

function initializeApp() {
    const contentSections = document.querySelectorAll('.content-section');
    contentSections.forEach(section => {
        section.classList.remove('active');
    });

//    loadFilterState();

    initNavDrawer();
    initResponsiveTables();

    feather.replace();

    if (typeof AOS !== 'undefined') {
        AOS.init();
    }

    setupRouter();
    registerAppSections();
    setupNavigation();
    setupModuleSearch();
    setupAuthForms();
    installSessionKeepAlive();

    // A token left from before the cookie is traded for one and forgotten; with
    // no token at all the cookie may still hold a session, so it is asked too.
    adoptSession().then(async isValid => {
        if (isValid && !(await ensureAgreements())) {
            return;
        }
        if (isValid) {
            loadModules().then(() => {
                updateVersionPanelVisibility();
                appRouter.handleUrlChange();

                setTimeout(() => {
                    const activeSection = document.querySelector('.content-section.active');
                    if (!activeSection) {
                        const defaultNav = document.querySelector('.nav-item');
                        if (defaultNav) {
                            defaultNav.click();
                        }
                    }
                }, 200);
            });
        } else {
            showAuthModal();
        }
    });
}

/* ==========================================================================
   Platform documents.

   The panel does not open until the person has accepted the current version of
   every document required of them; the server refuses the sections' requests
   meanwhile (403 agreements_required). Texts and versions come from the server,
   and what is sent back is exactly the versions that were shown.
   ========================================================================== */
function pendingDocuments(overview) {
    const pending = new Set((overview && overview.pending) || []);
    return ((overview && overview.documents) || []).filter(doc => pending.has(doc.id));
}

function agreementAcceptance(documents) {
    return { documents: (documents || []).map(doc => ({ id: doc.id, version: doc.version })) };
}
// end of platform documents helpers

async function documentsPending() {
    // The framework does not ship documents and declares no routes for them:
    // those belong to whichever application actually has the documents. What
    // the framework does say is whether this person has anything to accept;
    // without that check the shell used to call /api/agreements for everyone,
    // and in an application with no documents that was a refusal on every
    // sign-in.
    try {
        const response = await fetch('/api/auth/me');
        if (!response.ok) {
            return false;
        }
        return Boolean((await response.json()).documents_pending);
    } catch (error) {
        console.warn('Could not tell whether documents are pending:', error);
        return false;
    }
}

async function ensureAgreements() {
    if (!(await documentsPending())) {
        return true;
    }
    let overview;
    try {
        const response = await fetch('/api/agreements');
        if (!response.ok) {
            return true;
        }
        overview = await response.json();
    } catch (error) {
        console.warn('Could not read the platform documents:', error);
        return true;
    }
    const documents = pendingDocuments(overview);
    if (!documents.length) {
        return true;
    }
    return showAgreementsDialog(documents);
}

function showAgreementsDialog(documents) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'fixed inset-0 z-50 flex items-center justify-center p-4 bg-black bg-opacity-60';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');

        const panel = document.createElement('div');
        panel.className = 'bg-white text-gray-900 rounded-lg shadow-xl w-full max-w-3xl flex flex-col';
        panel.style.maxHeight = '90vh';

        const header = document.createElement('div');
        header.className = 'p-4 border-b';
        const title = document.createElement('h2');
        title.className = 'text-lg font-semibold';
        title.textContent = 'Before you continue';
        const lead = document.createElement('p');
        lead.className = 'text-sm text-gray-600 mt-1';
        lead.textContent = 'Please read and accept the documents below. You accept these versions; '
            + 'a new version will be shown to you again.';
        header.append(title, lead);

        const body = document.createElement('div');
        body.className = 'p-4 overflow-y-auto';
        documents.forEach(doc => {
            const heading = document.createElement('h3');
            heading.className = 'font-semibold mt-2';
            heading.textContent = doc.title + ' (version ' + doc.version + ')';
            const text = document.createElement('pre');
            text.className = 'whitespace-pre-wrap text-sm bg-gray-50 border rounded p-3 mt-2 mb-4';
            text.style.fontFamily = 'inherit';
            text.textContent = doc.text || '';
            body.append(heading, text);
        });

        const footer = document.createElement('div');
        footer.className = 'p-4 border-t flex flex-col gap-3';
        const agree = document.createElement('label');
        agree.className = 'flex items-start gap-2 text-sm';
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.className = 'mt-1';
        const agreeText = document.createElement('span');
        agreeText.textContent = 'I have read and accept ' + documents.map(doc => doc.title).join(', ') + '.';
        agree.append(box, agreeText);
        const error = document.createElement('p');
        error.className = 'text-sm text-red-600';
        error.hidden = true;
        const buttons = document.createElement('div');
        buttons.className = 'flex flex-col md:flex-row gap-2 md:justify-end';
        const leave = document.createElement('button');
        leave.type = 'button';
        leave.className = 'px-4 py-2 rounded border';
        leave.textContent = 'Sign out';
        const accept = document.createElement('button');
        accept.type = 'button';
        accept.className = 'px-4 py-2 rounded bg-blue-600 text-white disabled:opacity-50';
        accept.textContent = 'Accept and continue';
        accept.disabled = true;
        box.addEventListener('change', () => { accept.disabled = !box.checked; });
        buttons.append(leave, accept);
        footer.append(agree, error, buttons);

        panel.append(header, body, footer);
        overlay.appendChild(panel);
        document.body.appendChild(overlay);

        leave.addEventListener('click', async () => {
            overlay.remove();
            await logout();
            resolve(false);
        });
        accept.addEventListener('click', async () => {
            accept.disabled = true;
            error.hidden = true;
            try {
                const response = await fetch('/api/agreements/accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(agreementAcceptance(documents))
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    // 409: a document changed while the dialog was open; show the new text.
                    error.textContent = data.detail || ('Request failed (' + response.status + ')');
                    error.hidden = false;
                    if (response.status === 409) {
                        overlay.remove();
                        resolve(await ensureAgreements());
                        return;
                    }
                    accept.disabled = !box.checked;
                    return;
                }
                overlay.remove();
                resolve(true);
            } catch (failure) {
                error.textContent = 'Could not reach the server: ' + failure.message;
                error.hidden = false;
                accept.disabled = !box.checked;
            }
        });
    });
}

async function adoptSession() {
    const stored = localStorage.getItem('authToken');
    if (stored && stored !== SESSION_MARKER) {
        try {
            await fetch('/api/auth/session', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${stored}` }
            });
        } catch (error) {
            console.warn('Could not move the session into a cookie:', error);
        }
        localStorage.removeItem('authToken');
    }
    let valid = await verifyToken();
    if (!valid) {
        // The token in the cookie is gone -- the browser was closed, or the
        // machine slept past it. A session asked to stay signed in still has
        // its renewal credential and renews here without asking for a
        // password; one that was not keeps the refusal (keepup-177).
        valid = await refreshSession(false);
    }
    return valid;
}

function showSection(sectionId) {
    const contentSections = document.querySelectorAll('.content-section');
    contentSections.forEach(section => {
        section.classList.remove('active');
    });

    const targetSection = document.getElementById(sectionId);
    if (targetSection) {
        targetSection.classList.add('active');
    }
}

function openGoodsModule() {
    appRouter.navigate('/selfcare/modules/goods');
}

window.openGoodsModule = openGoodsModule;

async function loadModules() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/modules', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            await initializeModules(data.modules);
            return data.modules; // Return the modules
        }
        return [];
    } catch (error) {
        return [];
    }
}

const AppEventTarget = new EventTarget();
const MODULES_LOADED_EVENT = 'app:modules:loaded';

window.AppEventTarget = AppEventTarget;
window.AppEvents = {
    MODULES_LOADED: MODULES_LOADED_EVENT,
    AUTH_CHANGED: 'app:auth:changed',
    ROUTE_CHANGED: 'app:route:changed'
};

async function initializeModules(modules) {
    if (!modules || modules.length === 0) {
        console.log('No modules available to load');
        return;
    }

    loadedModules = modules;

    console.log('Loading modules:', modules);

    try {
        // First load the CSS
        modules.forEach(module => {
            if (module.css) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = moduleAssetUrl(module.css, module.version);
                document.head.appendChild(link);
            }
        });

        // Then load the modules' JS sequentially
        for (const module of modules) {
            if (module.js) {
                try {
                    await loadScript(moduleAssetUrl(module.js, module.version));
                    console.log('Module loaded:', module.js);
                } catch (error) {
                    console.error('Failed to load JS module:', module.js, error);
                }
            }
        }

        // Initialize modules and register their routes
        modules.forEach(module => {
            if (module.initFunction && typeof window[module.initFunction] === 'function') {
                try {
                    console.log('Initializing module:', module.initFunction);
                    window[module.initFunction]();

                    // Register the module's route if specified
                    if (module.routePath && module.sectionId) {
                        console.log(`Registering module route: ${module.routePath} -> ${module.sectionId}`);
                        appRouter.registerRoute(module.routePath, () => {
                            console.log(`Router: Handling module route ${module.routePath}`);
                            if (window[module.handlerFunction]) {
                                window[module.handlerFunction]();
                            }
                        });
                    }
                } catch (error) {
                    console.error('Failed to initialize module:', module.initFunction, error);
                }
            } else {
                console.warn('Initialization function not found:', module.initFunction);
            }
        });
        const event = new CustomEvent(MODULES_LOADED_EVENT, {
            detail: {
                modules: loadedModules,
                count: loadedModules.length,
                timestamp: new Date().toISOString()
            },
            bubbles: false,
            cancelable: true
        });

        AppEventTarget.dispatchEvent(event);
        window.frontendModulesloaded = true;
    } catch (error) {
        console.error('Failed to initialize modules:', error);
    }
}

function handleRouteAfterModulesLoaded() {
    const currentPath = window.location.pathname;

    setTimeout(() => {
        appRouter.handleUrlChange();
    }, 100);
}

// Module version in its file addresses.
//
// The browser caches module.css and module.js under the usual rules, and
// after editing a module the user kept seeing the old version until a hard
// reload: CSS from one version with a script from another produced, for
// example, buttons under the canvas. The version from the module catalogue
// makes the address new as soon as the module is updated.
function moduleAssetUrl(path, version) {
    if (!path || !version) return path;
    return path + (path.includes('?') ? '&' : '?') + 'v=' + encodeURIComponent(version);
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

function updateNavigationWithModules() {
    const container = document.getElementById('modules-nav-container');
    if (!container) {
        return;
    }

    container.innerHTML = '';

    loadedModules.forEach(module => {
        const navItem = document.createElement('a');
        navItem.href = '#';
        navItem.className = 'nav-item';
        navItem.setAttribute('data-target', `${module.id}-section`);

        navItem.innerHTML = `
            <i data-feather="users" class="w-5 h-5"></i>
            <span class="nav-text">${module.name}</span>
        `;

        container.appendChild(navItem);
    });

    setupNavigation();
    feather.replace();
}

// Authentication check
function checkAuth() {
    const token = localStorage.getItem('authToken');
    if (!token) {
        showAuthModal();
        return false;
    }
    verifyToken();
    return true;
}

function showAuthModal() {
    const authBlock = document.getElementById('authBlock');
    authBlock.classList.remove('hidden');

    document.getElementById('loginTabContent').classList.add('active');
    document.getElementById('registerTabContent').classList.remove('active');
    document.getElementById('loginTab').classList.add('border-blue-600', 'text-blue-600');
    document.getElementById('registerTab').classList.remove('border-blue-600', 'text-blue-600');

    applyPublicConfig();
}

// What this deployment offers before anybody has signed in: whether accounts
// can be created here at all, and the terms a person is asked to agree to.
// (The promise itself is declared with the CSRF wrapper above, because the
// cookie names a change must send arrive in the same answer.)
function loadPublicConfig() {
    if (!publicConfigPromise) {
        publicConfigPromise = fetch('/api/public/config')
            .then(response => (response.ok ? response.json() : null))
            .catch(() => null);
    }
    return publicConfigPromise;
}

async function applyPublicConfig() {
    const config = await loadPublicConfig();
    const registerTab = document.getElementById('registerTab');

    // Registration closed is the default, so the tab stays out of sight unless
    // the server says otherwise: a form that creates accounts nobody activates
    // contradicts the sign-in screen, which says registration is by invitation.
    if (registerTab) {
        registerTab.hidden = !(config && config.self_registration);
    }

    const terms = document.getElementById('termsContent');
    if (terms && config && config.terms) {
        terms.textContent = config.terms;
        terms.classList.add('terms-plain');
    }
}

function closeAuthModal() {
    const authBlock = document.getElementById('authBlock');
    authBlock.classList.add('hidden');
}

function setupNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.removeEventListener('click', handleNavigation);
    });

    navItems.forEach(item => {
        item.addEventListener('click', handleNavigation);
    });

    document.addEventListener('click', function(e) {
        if (e.target.closest('.nav-item')) {
            handleNavigation.call(e.target.closest('.nav-item'), e);
        }
    });
}

/* ==========================================================================
   The search of the sidebar (keepup-179).

   The menu of a full panel is longer than a screen, and the person looking for
   one section reads it top to bottom. The field above the menu narrows the menu
   to the entries whose text contains what was typed -- a substring, in any
   case, front-end only: nothing is asked of the server and nothing is stored.

   A submenu is drawn from the same elements, so the search has to keep the
   shape as well as the text. An entry is a submodule when it sits inside
   another entry (a `.nav-item` inside a `.nav-item`) or inside a container the
   parent keeps for its children (`data-nav-children`); its depth is the deeper
   of the two counts. A match that comes without its parent is indented by that
   depth and carries a leading dot, so a result never reads as a top-level
   section.

   The parent of a match stays on screen even when its own text does not match:
   hiding it would hide the child that lives inside it. A parent whose children
   sit in a sibling container can be hidden, and then the child stands alone --
   which is the case the indentation exists for.

   A result also says where the match is: the substring that matched is marked
   inside the label, in the label's own case, and ArrowDown/ArrowUp walk the
   matches on screen so Enter can open the one that was aimed at rather than
   whichever came first.
   ========================================================================== */
// --- the search field of the sidebar
const NAV_SEARCH_ROOT_ID = 'modules-nav-container';
const NAV_SEARCH_INPUT_ID = 'nav-search-input';
const NAV_SEARCH_CLEAR_ID = 'nav-search-clear';
const NAV_SEARCH_EMPTY_ID = 'nav-search-empty';
const NAV_SEARCH_ITEM = '.nav-item';
const NAV_SEARCH_CHILDREN = '[data-nav-children]';
const NAV_SEARCH_HIDDEN = 'nav-search-hidden';
const NAV_SEARCH_MATCH = 'nav-search-match';
const NAV_SEARCH_NESTED = 'nav-search-nested';
const NAV_SEARCH_HIT = 'nav-search-hit';
const NAV_SEARCH_CURRENT = 'nav-search-current';

//: The element a menu entry keeps its text in.
function navSearchLabelElement(item) {
    return item.querySelector ? item.querySelector('.nav-text') : null;
}

//: What a menu entry says, without the icon beside it.
function navSearchLabel(item) {
    const text = navSearchLabelElement(item);
    const label = (text && text.textContent) || item.textContent || '';
    return String(label).trim();
}

//: The children a label had before the search first marked it. The nodes are
//: kept, not their markup: putting them back restores a label that carries more
//: than text, and the next search marks a clean label rather than its own marks.
const navSearchOriginal = new WeakMap();

function navSearchRestoreLabel(label) {
    const original = label && navSearchOriginal.get(label);
    if (!original) return;
    if (label.replaceChildren) label.replaceChildren(...original);
}

//: Mark every occurrence of `query` in the label, keeping the label's own case.
function navSearchMark(label, query) {
    if (!label || !query) return;
    if (!navSearchOriginal.has(label)) {
        navSearchOriginal.set(label, Array.from(label.childNodes || label.children || []));
    } else {
        navSearchRestoreLabel(label);
    }
    const lower = query.toLowerCase();
    const marked = [];
    navSearchOriginal.get(label).forEach(node => {
        const value = node.nodeValue;
        // An icon or a badge beside the words is put back untouched: only the
        // label's own text says where the match is.
        if (typeof value !== 'string') { marked.push(node); return; }
        const hay = value.toLowerCase();
        let at = 0;
        let from = 0;
        while (true) {
            const found = hay.indexOf(lower, from);
            if (found === -1) break;
            if (found > at) marked.push(document.createTextNode(value.slice(at, found)));
            const hit = document.createElement('mark');
            hit.className = NAV_SEARCH_HIT;
            hit.textContent = value.slice(found, found + query.length);
            marked.push(hit);
            at = found + query.length;
            from = at;
        }
        if (at === 0) { marked.push(node); return; }
        if (at < value.length) marked.push(document.createTextNode(value.slice(at)));
    });
    if (label.replaceChildren) label.replaceChildren(...marked);
}

//: The typed text, comparable: padded text is not a different search.
function navSearchQuery(raw) {
    return String(raw == null ? '' : raw).trim().toLowerCase();
}

//: How many menu levels down a match sits; 0 is a top-level section.
function navSearchDepth(item, root) {
    let items = 0;
    let containers = 0;
    let node = item.parentElement;
    while (node && node !== root) {
        if (node.matches) {
            if (node.matches(NAV_SEARCH_ITEM)) items += 1;
            else if (node.matches(NAV_SEARCH_CHILDREN)) containers += 1;
        }
        node = node.parentElement;
    }
    // A submenu keeps its children in a container beside the parent's link, so
    // one level counts either the parent entry above the container or the
    // container itself -- never both.
    return Math.max(items, containers);
}

function navSearchContains(parent, child) {
    for (let node = child.parentElement; node; node = node.parentElement) {
        if (node === parent) return true;
    }
    return false;
}

function navSearchSetHidden(element, hidden) {
    if (element.classList) element.classList.toggle(NAV_SEARCH_HIDDEN, Boolean(hidden));
}

//: The match the arrow keys are on; it is what Enter opens.
let navSearchCurrent = null;

//: The query the menu is showing, so a changed one drops the arrow's place.
let navSearchApplied = '';

function navSearchMatches(root) {
    return root && root.querySelectorAll
        ? Array.from(root.querySelectorAll('.' + NAV_SEARCH_MATCH))
        : [];
}

//: Put the keyboard's place on one match, and keep it in view.
function navSearchSelect(element) {
    if (navSearchCurrent && navSearchCurrent.classList) {
        navSearchCurrent.classList.remove(NAV_SEARCH_CURRENT);
    }
    navSearchCurrent = element || null;
    if (!navSearchCurrent) return null;
    if (navSearchCurrent.classList) navSearchCurrent.classList.add(NAV_SEARCH_CURRENT);
    if (navSearchCurrent.scrollIntoView) {
        navSearchCurrent.scrollIntoView({block: 'nearest'});
    }
    return navSearchCurrent;
}

//: Step through the matches on screen, wrapping at either end.
function navSearchStep(step) {
    const items = navSearchMatches(document.getElementById(NAV_SEARCH_ROOT_ID));
    if (!items.length) return null;
    const at = items.indexOf(navSearchCurrent);
    const next = at === -1
        ? (step > 0 ? 0 : items.length - 1)
        : (at + step + items.length) % items.length;
    return navSearchSelect(items[next]);
}

/**
 * Narrow the sidebar to the entries whose text contains `rawQuery`.
 *
 * Returns the query as it was understood and how many entries matched, which is
 * what the status line and the tests read.
 */
function applyModuleSearch(rawQuery) {
    const root = document.getElementById(NAV_SEARCH_ROOT_ID);
    const query = navSearchQuery(rawQuery);
    const clear = document.getElementById(NAV_SEARCH_CLEAR_ID);
    const empty = document.getElementById(NAV_SEARCH_EMPTY_ID);
    if (clear) clear.hidden = query === '';
    if (!root || !root.querySelectorAll) {
        if (empty) empty.hidden = true;
        return {query: query, matches: 0};
    }

    // Every label goes back to what it was before this search: the marks of the
    // query typed a moment ago must not survive into this one.
    const elements = Array.from(root.querySelectorAll(NAV_SEARCH_ITEM));
    elements.forEach(element => navSearchRestoreLabel(navSearchLabelElement(element)));

    // A new query is a new set of results: the row the arrows were on belongs
    // to the old one, and Enter must not open a section nobody aimed at.
    if (query !== navSearchApplied) {
        navSearchApplied = query;
        navSearchSelect(null);
    }

    const items = elements.map(element => {
        const label = navSearchLabel(element);
        return {
            element: element,
            label: label,
            depth: navSearchDepth(element, root),
            matches: query !== '' && label.toLowerCase().indexOf(query) !== -1,
        };
    });
    const matches = items.filter(item => item.matches).length;
    items.forEach(item => {
        // An entry survives a search when it matches, or when a match lives
        // inside it and hiding it would take the match away.
        const keepsPath = items.some(other => other.matches && navSearchContains(item.element, other.element));
        const visible = query === '' || item.matches || keepsPath;
        navSearchSetHidden(item.element, !visible);
        if (item.element.classList) {
            item.element.classList.toggle(NAV_SEARCH_MATCH, query !== '' && item.matches);
            item.element.classList.toggle(NAV_SEARCH_NESTED,
                query !== '' && item.matches && item.depth > 0);
        }
        if (item.element.style && item.element.style.setProperty) {
            item.element.style.setProperty('--nav-search-depth', String(item.depth));
        }
        // The result says where it matched: only the substring is marked, in
        // the case the label was written in, and the rest of the label stays.
        if (query !== '' && item.matches) {
            navSearchMark(navSearchLabelElement(item.element), query);
        }
    });

    // A match that this search took away cannot stay where the arrows were.
    if (navSearchCurrent && !items.some(item => item.element === navSearchCurrent && item.matches)) {
        navSearchSelect(null);
    }

    if (empty) {
        empty.hidden = !(query !== '' && matches === 0);
        if (!empty.hidden) empty.textContent = 'No modules match "' + query + '"';
    }
    return {query: query, matches: matches};
}

//: The clear button and Escape put the menu back the way it was.
function clearModuleSearch() {
    const input = document.getElementById(NAV_SEARCH_INPUT_ID);
    if (input) input.value = '';
    return applyModuleSearch('');
}

function setupModuleSearch() {
    const input = document.getElementById(NAV_SEARCH_INPUT_ID);
    if (!input) return;

    const update = () => applyModuleSearch(input.value);
    input.addEventListener('input', update);
    // The browser's own clear button of a type="search" field does not fire
    // "input" everywhere; "search" is the event it does fire.
    input.addEventListener('search', update);
    input.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            if (input.value === '') return;
            event.preventDefault();
            clearModuleSearch();
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            const matches = navSearchMatches(document.getElementById(NAV_SEARCH_ROOT_ID));
            if (!matches.length) return;
            // The arrows belong to the results while the field is focused: the
            // caret stays where it was, and the menu takes the key instead.
            event.preventDefault();
            navSearchStep(event.key === 'ArrowDown' ? 1 : -1);
            return;
        }
        if (event.key === 'Enter') {
            const chosen = navSearchCurrent
                || navSearchMatches(document.getElementById(NAV_SEARCH_ROOT_ID))[0];
            if (chosen && chosen.click) {
                event.preventDefault();
                chosen.click();
            }
        }
    });

    const clear = document.getElementById(NAV_SEARCH_CLEAR_ID);
    if (clear) {
        clear.addEventListener('click', () => {
            clearModuleSearch();
            input.focus();
        });
    }

    // Sections add their entries as they initialise, which can happen after the
    // field is on screen; a search typed meanwhile has to reach them too.
    const root = document.getElementById(NAV_SEARCH_ROOT_ID);
    if (root && typeof MutationObserver !== 'undefined') {
        new MutationObserver(() => {
            if (input.value.trim() !== '') applyModuleSearch(input.value);
        }).observe(root, {childList: true, subtree: true});
    }

    update();
}

// What a section -- or a test -- may ask of the field without owning it.
window.AppNavSearch = {
    apply: applyModuleSearch,
    clear: clearModuleSearch,
    label: navSearchLabel,
    depth: navSearchDepth,
};
// --- end of the search field of the sidebar

const appSections = {
    sections: new Map(),

    registerSection(targetId, routePath, handler) {
        this.sections.set(targetId, { routePath, handler });

        if (window.appRouter) {
            window.appRouter.registerRoute(routePath, handler);
        }
    },

    getSection(targetId) {
        return this.sections.get(targetId);
    },

    getRoutePath(targetId) {
        const section = this.sections.get(targetId);
        return section ? section.routePath : '/selfcare';
    },

    getSections() {
        return Array.from(this.sections.keys());
    }
};

window.appSections = appSections;

function setupAuthForms() {
    const loginTab = document.getElementById('loginTab');
    const registerTab = document.getElementById('registerTab');
    const loginTabContent = document.getElementById('loginTabContent');
    const registerTabContent = document.getElementById('registerTabContent');

    if (loginTab && registerTab) {
        loginTab.addEventListener('click', function() {
            loginTab.classList.add('border-blue-600', 'text-blue-600');
            registerTab.classList.remove('border-blue-600', 'text-blue-600');
            registerTab.classList.add('text-gray-500');

            loginTabContent.classList.add('active');
            registerTabContent.classList.remove('active');
        });

        registerTab.addEventListener('click', function() {
            registerTab.classList.add('border-blue-600', 'text-blue-600');
            loginTab.classList.remove('border-blue-600', 'text-blue-600');
            loginTab.classList.add('text-gray-500');

            registerTabContent.classList.add('active');
            loginTabContent.classList.remove('active');
        });
    }

    // Forgot password: the address, when the deployment has one, comes from
    // the server. The framework does not know where a password is reset, and
    // hardcoding somebody's page into the shell would keep two truths. Without
    // an answer the line stays hidden rather than showing a broken link.
    applyForgotPasswordLink();

    // Updated sign-in form handler
    document.getElementById('loginForm').addEventListener('submit', function (e) {
        e.preventDefault();
        // The password earned a challenge, not a session: the same form now
        // carries the code (keepup-176).
        if (window.MfaPanel && MfaPanel.challengeToken()) {
            const code = document.getElementById('loginCode');
            MfaPanel.verify(code ? code.value : '').then(function (result) {
                if (result.ok) {
                    finishSignIn(result.data);
                } else {
                    showAuthMessage(result.data.detail || 'That code is not right', 'error');
                }
            }).catch(function (error) {
                showAuthMessage('Network error: ' + error, 'error');
            });
            return;
        }
        const username = document.getElementById('loginUsername').value;
        const password = document.getElementById('loginPassword').value;
        login(username, password);
    });

    document.getElementById('registerForm').addEventListener('submit', function (e) {
        e.preventDefault();
        const username = document.getElementById('registerUsername').value;
        const password = document.getElementById('registerPassword').value;
        const passwordConfirm = document.getElementById('registerPasswordConfirm').value;
        const email = document.getElementById('registerEmail').value;
        const phone = document.getElementById('registerPhone').value;
        const fullName = document.getElementById('registerFullName').value;
        const agreeTerms = document.getElementById('registerAgreeTerms').checked;

        if (password !== passwordConfirm) {
            showAuthMessage('Passwords do not match', 'error');
            return;
        }

        if (!agreeTerms) {
            showAuthMessage('You must agree to the terms of use', 'error');
            return;
        }

        register(username, password, email, phone, fullName, agreeTerms);
    });
}

function registerAppSections() {

    appSections.registerSection(
        'upload-section',
        '/selfcare/upload',
        () => {
            showSection('upload-section');
            loadXmlSources();
        }
    );

    appSections.registerSection(
        'dashboard-section',
        '/selfcare',
        () => {
            showSection('dashboard-section');
        }
    );
}

// The access token lives an hour and expires silently: without this the panel
// simply starts getting refusals in the middle of the work. The server says how
// long the token has at sign-in and on every renewal, and the next one is
// bought five minutes before the end -- while the token is still valid, which
// is the only time the exchange is allowed. A tab that was asleep for longer
// (a laptop that closed its lid) is picked up by the wake handlers below: a
// session that was asked to stay signed in renews from its own credential even
// after the token has gone (keepup-177).
const SESSION_REFRESH_LEAD_SECONDS = 300;
const SESSION_RETRY_SECONDS = 60;
let sessionRefreshTimer = null;
let sessionExpiresAt = null;
let sessionRefreshing = false;

function noteSessionLifetime(expiresInSeconds) {
    const seconds = Number(expiresInSeconds);
    sessionExpiresAt = seconds > 0 ? Date.now() + seconds * 1000 : null;
}

function scheduleSessionRefresh(expiresInSeconds) {
    if (sessionRefreshTimer) {
        clearTimeout(sessionRefreshTimer);
        sessionRefreshTimer = null;
    }
    if (!expiresInSeconds) {
        return;
    }
    noteSessionLifetime(expiresInSeconds);
    // Never sooner than a minute: a token that short is about to be replaced
    // by a sign-in anyway, and a tight timer would spin.
    const delay = Math.max(60, Number(expiresInSeconds) - SESSION_REFRESH_LEAD_SECONDS);
    sessionRefreshTimer = setTimeout(function () { refreshSession(); }, delay * 1000);
}

function forgetSession() {
    localStorage.removeItem('authToken');
    localStorage.removeItem('auth_token');
    if (window.MfaPanel) MfaPanel.clearChallenge();
    if (sessionRefreshTimer) {
        clearTimeout(sessionRefreshTimer);
        sessionRefreshTimer = null;
    }
    sessionExpiresAt = null;
    currentUser = null;
    headerBadge = null;
    updateUIAfterAuth();
}

async function refreshSession(showSignIn = true) {
    if (sessionRefreshing) {
        return false;
    }
    sessionRefreshing = true;
    try {
        const response = await fetch('/api/auth/refresh', { method: 'POST' });
        if (response.ok) {
            const data = await response.json();
            if (data.access_token) {
                // The renewed session is already in the cookie; the body's
                // token is for agents and is not kept.
                localStorage.setItem('authToken', SESSION_MARKER);
                scheduleSessionRefresh(data.expires_in);
                if (!currentUser) {
                    // A renewal that came back after a retry (a server that was
                    // down while the page loaded) still has to show the panel.
                    return await verifyToken();
                }
                return true;
            }
        }
        if (response.status === 401) {
            // There is nothing left to renew, not even the credential a
            // remembered session carries: asking for the sign-in is honest.
            forgetSession();
            if (showSignIn) showAuthModal();
            return false;
        }
        // A server that is down, or a proxy answering for it, is not the end of
        // the session: try again rather than letting the token run out.
        scheduleSessionRefresh(SESSION_RETRY_SECONDS);
        return false;
    } catch (error) {
        console.warn('Failed to refresh session:', error);
        scheduleSessionRefresh(SESSION_RETRY_SECONDS);
        return false;
    } finally {
        sessionRefreshing = false;
    }
}

// A tab whose timers were frozen (a closed lid, a phone that slept) wakes up
// after the token has already expired. The timer cannot have fired in time, so
// the wake itself asks for the next token: the cookie is sent, and a session
// that was asked to stay signed in renews without a password (keepup-177).
function installSessionKeepAlive() {
    const wake = function () {
        if (!localStorage.getItem('authToken')) {
            return;
        }
        if (sessionExpiresAt === null ||
            Date.now() >= sessionExpiresAt - SESSION_REFRESH_LEAD_SECONDS * 1000) {
            refreshSession();
        }
    };
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) wake();
    });
    window.addEventListener('focus', wake);
    window.addEventListener('online', wake);
}
// --- end of the panel session renewal ---------------------------------------

async function login(username, password) {
    const rememberBox = document.getElementById('loginRemember');
    const remember = !!(rememberBox && rememberBox.checked);
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password, remember })
        });

        const data = await response.json();

        if (response.ok) {
            // A confirmed second factor answers here with a challenge, not a
            // session: no marker, no refresh timer, no /me without a cookie
            // (keepup-176). Before this, the panel called that a success and
            // then showed "Failed to get user data".
            if (data.mfa_required) {
                if (window.MfaPanel) {
                    MfaPanel.showChallenge(data.mfa_token);
                    showAuthMessage('Enter the code from your authenticator app.', 'info');
                } else {
                    showAuthMessage('This panel cannot ask for the second factor.', 'error');
                }
                return;
            }
            await finishSignIn(data);
        } else {
            showAuthMessage(data.detail || 'Sign-in failed', 'error');
        }
    } catch (error) {
        showAuthMessage('Network error: ' + error, 'error');
    }
}

async function finishSignIn(data) {
    // The one place a sign-in becomes a session in this browser, whichever
    // door it came through: password, or password and a code (keepup-176).
    if (!data || !data.access_token) {
        showAuthMessage('Sign-in failed: the server answered without a session.', 'error');
        return;
    }
    localStorage.setItem('authToken', SESSION_MARKER);
    scheduleSessionRefresh(data.expires_in);
    const userResponse = await fetch('/api/auth/me');

    if (userResponse.ok) {
        const userData = await userResponse.json();
        currentUser = userData;
        updateUIAfterAuth();
        closeAuthModal();
        if (!(await ensureAgreements())) {
            return;
        }
        await loadModules();
        refreshEntireUI();

        closeAuthModal();
        showAuthMessage('Signed in successfully!', 'success');
    } else {
        showAuthMessage('Failed to get user data', 'error');
    }
}

function togglePasswordVisibility(inputId) {
    const passwordInput = document.getElementById(inputId);
    const toggleButton = document.getElementById(inputId + 'Toggle');

    if (passwordInput.type === 'password') {
        passwordInput.type = 'text';
        toggleButton.innerHTML = '<i data-feather="eye-off" class="w-4 h-4"></i>';
    } else {
        passwordInput.type = 'password';
        toggleButton.innerHTML = '<i data-feather="eye" class="w-4 h-4"></i>';
    }
    feather.replace();
}


async function register(username, password, email = null, phone = null, fullName = null, agreeTerms = false) {
    try {
        const registerData = {
            username: username.trim(),
            password: password,
            agree_terms: agreeTerms
        };

        // Add optional fields only if present
        if (email && email.trim()) {
            registerData.email = email.trim();
        }

        if (phone && phone.trim()) {
            // Strip formatting from the phone number
            const cleanedPhone = phone.replace(/\D/g, '');
            if (cleanedPhone) {
                registerData.phone = cleanedPhone;
            }
        }

        if (fullName && fullName.trim()) {
            registerData.full_name = fullName.trim();
        }

        console.log('Registration data being sent:', registerData);

        const response = await fetch('/api/auth/register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(registerData)
        });

        const responseText = await response.text();
        console.log('Server response (text):', responseText);

        let data;
        try {
            data = JSON.parse(responseText);
        } catch (e) {
            console.error('Failed to parse JSON response:', e);
            showAuthMessage('Server error: invalid response format', 'error');
            return;
        }

        if (response.status === 422) {
            // Handle validation errors
            const errorMessage = handleValidationError(data);
            showAuthMessage(`Error: ${errorMessage}. Please check the entered data.`, 'error');
            return;
        }

        if (response.ok && data.success) {
            showAuthMessage('Registration successful! Wait for administrator activation.', 'success');

            // Reset the registration form
            resetRegistrationForm();

            // Switch to the sign-in tab after 2 seconds
            setTimeout(() => {
                document.getElementById('loginTab').click();
            }, 2000);
        } else {
            // Generic handling of other errors
            const errorMsg = data.detail || data.message || `Registration error (status: ${response.status})`;
            showAuthMessage(errorMsg, 'error');
        }
    } catch (error) {
        console.error('Registration error:', error);
        showAuthMessage('Network error: ' + error.message, 'error');
    }
}

function handleValidationError(data) {
    if (!data || !data.detail) {
        return 'Unknown validation error';
    }

    const detail = data.detail;

    // If this is an array of errors (FastAPI's standard 422 format)
    if (Array.isArray(detail)) {
        const messages = [];

        detail.forEach(err => {
            let fieldName = 'unknown field';
            let errorMsg = err.msg || 'Error';

            // Extract the field name from loc
            if (err.loc && err.loc.length > 1) {
                fieldName = err.loc[err.loc.length - 1];

                // Convert field names into a readable form
                const fieldMap = {
                    'username': 'Username',
                    'password': 'Password',
                    'email': 'Email',
                    'phone': 'Phone',
                    'full_name': 'Full name',
                    'agree_terms': 'Agreement to the terms'
                };

                fieldName = fieldMap[fieldName] || fieldName;
            }

            // Convert error messages into a readable form
            const errorMap = {
                'field required': 'required field',
                'Invalid email format': 'invalid email format',
                'Phone number must be at least 10 digits': 'the number must contain at least 10 digits',
                'You must agree to the terms of use': 'you must agree to the terms'
            };

            errorMsg = errorMap[errorMsg] || errorMsg;

            messages.push(`${fieldName}: ${errorMsg}`);
        });

        return messages.join('. ');
    }

    // If this is a string
    if (typeof detail === 'string') {
        return detail;
    }

    // If this is an object
    if (typeof detail === 'object') {
        const messages = [];
        for (const [field, msg] of Object.entries(detail)) {
            messages.push(`${field}: ${msg}`);
        }
        return messages.join('. ');
    }

    return 'Data validation error';
}


// --- The application's badge in the header -----------------------------------
//
// The top right corner shows the user's role unless the application puts a badge
// of its own there -- a client's balance, a plan, a quota. The application calls
// AppHeader.setBadge({text, title, tone}) and AppHeader.clearBadge(); the text
// is only ever text, and the tone one of a closed list the theme styles. The
// badge belongs to the session: signing out takes it away, so the next person on
// the same computer does not see the previous one's figure.

const HEADER_BADGE_TONES = ['neutral', 'positive', 'warning'];
let headerBadge = null;

/** What the corner shows for this user and this badge -- pure, tested under node. */
function headerBadgeView(user, badge) {
    if (badge && badge.text) {
        const tone = HEADER_BADGE_TONES.includes(badge.tone) ? badge.tone : 'neutral';
        return { text: String(badge.text), title: badge.title ? String(badge.title) : '',
                 className: `ml-2 role-badge header-badge header-badge-${tone}` };
    }
    // The corner answers the same question it always did -- is this an
    // administrator -- and names the whole set in the tooltip, where a second
    // role fits and two words in the corner would not.
    const roles = userRolesOf(user);
    const admin = roles.indexOf(ROLE_ADMIN) !== -1;
    return { text: admin ? 'Admin' : 'Client',
             title: roles.length > 1 ? roles.join(', ') : '',
             className: `ml-2 role-badge ${admin ? 'role-admin' : 'role-client'}` };
}

function renderHeaderBadge() {
    const corner = document.getElementById('userRole');
    if (!corner) return;
    const view = headerBadgeView(currentUser, headerBadge);
    corner.textContent = view.text;
    corner.title = view.title;
    corner.className = view.className;
}

window.AppHeader = {
    setBadge(badge) {
        headerBadge = badge && badge.text ? { text: badge.text, title: badge.title, tone: badge.tone } : null;
        renderHeaderBadge();
    },
    clearBadge() {
        headerBadge = null;
        renderHeaderBadge();
    },
};

// --- end of the application's badge in the header ------------------------------

async function logout() {
    // The server ends the session first: forgetting it only in this browser
    // would leave a copied token working -- and a remembered session's renewal
    // credential working with it (keepup-177).
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (error) {
        console.warn('Could not end the session on the server:', error);
    }
    forgetSession();
    // The corner belongs to whoever is signed in; the helper clears it for an
    // expired session too, and signing out is its own way there.
    headerBadge = null;
    showAuthModal();
    hideVersionPanel();
}

async function verifyToken() {
    try {
        const response = await fetch('/api/auth/verify', { method: 'GET' });

        const data = await response.json();

        if (data.success) {
            localStorage.setItem('authToken', SESSION_MARKER);
            currentUser = data.user;
            updateUIAfterAuth();
            // The server says how long the token still has, so a panel opened
            // on an hour-old session schedules its renewal at the right moment
            // instead of guessing (keepup-177).
            if (typeof data.expires_in === 'number') {
                scheduleSessionRefresh(data.expires_in);
            }
            return true;
        } else {
            localStorage.removeItem('authToken');
            currentUser = null;
            updateUIAfterAuth();
            return false;
        }
    } catch (error) {
        localStorage.removeItem('authToken');
        currentUser = null;
        updateUIAfterAuth();
        return false;
    }
}

function updateUIAfterAuth() {
    const authBlock = document.getElementById('authBlock');

    if (currentUser) {
        authBlock.classList.add('hidden');

        document.getElementById('usernameDisplay').textContent = currentUser.username;

        // Update the display of optional fields if present
        if (currentUser.email) {
            const emailElement = document.getElementById('userEmail');
            if (emailElement) {
                emailElement.textContent = currentUser.email;
                emailElement.parentElement.style.display = 'block';
            }
        }

        if (currentUser.phone) {
            const phoneElement = document.getElementById('userPhone');
            if (phoneElement) {
                phoneElement.textContent = currentUser.phone;
                phoneElement.parentElement.style.display = 'block';
            }
        }

        if (currentUser.full_name) {
            const fullNameElement = document.getElementById('userFullName');
            if (fullNameElement) {
                fullNameElement.textContent = currentUser.full_name;
                fullNameElement.parentElement.style.display = 'block';
            }
        }

        const userStatus = document.getElementById('userStatus');
        userStatus.textContent = currentUser.status === 'active' ? 'Active' : 'Blocked';
        userStatus.className = `ml-2 status-badge ${currentUser.status === 'active' ? 'status-active' : 'status-blocked'}`;

        // The role, or the application's badge in its place (keepup-37).
        renderHeaderBadge();

        document.getElementById('userInfo').style.display = 'block';

        const adminItems = document.querySelectorAll('.admin-only');
        adminItems.forEach(item => {
            if (currentUserIsAdmin()) {
                item.classList.remove('hidden');
            } else {
                item.classList.add('hidden');
            }
        });
        updateVersionPanelVisibility();
    } else {
        authBlock.classList.remove('hidden');
        document.getElementById('userInfo').style.display = 'none';
        hideVersionPanel();
    }
}

function resetRegistrationForm() {
    const form = document.getElementById('registerForm');
    if (form) {
        form.reset();
    }
}

function showAuthMessage(text, type) {
    const messageDiv = document.getElementById('authMessage');
    messageDiv.textContent = text;
    messageDiv.className = 'mt-4 p-3 rounded-lg text-sm break-words';

    if (type === 'success') {
        messageDiv.classList.add('bg-green-50', 'text-green-800', 'border', 'border-green-200');
    } else {
        messageDiv.classList.add('bg-red-50', 'text-red-800', 'border', 'border-red-200');
    }

    messageDiv.classList.remove('hidden');

    // Auto-hide after 10 seconds for errors, 5 for success
    const timeout = type === 'success' ? 5000 : 10000;
    setTimeout(() => {
        if (messageDiv.parentElement) {
            messageDiv.classList.add('hidden');
        }
    }, timeout);
}

function findHeaderByText(headerRow, text) {
    const headers = headerRow.querySelectorAll('th');
    for (let header of headers) {
        if (header.textContent.trim() === text) {
            return header;
        }
    }
    return null;
}

// Variable tracking the collapsed/expanded state
let isGlobalDeductionExpanded = true;

// Initialize on load
document.addEventListener('DOMContentLoaded', function() {
    feather.replace();
});


// Initialize the phone mask on load
document.addEventListener('DOMContentLoaded', function() {
    const phoneInput = document.getElementById('registerPhone');
    if (phoneInput) {

        phoneInput.addEventListener('keydown', function(e) {
            // Allow: backspace, delete, tab, escape, enter
            if ([46, 8, 9, 27, 13].indexOf(e.keyCode) !== -1 ||
                // Allow: Ctrl+A
                (e.keyCode === 65 && e.ctrlKey === true) ||
                // Allow: home, end, left, right
                (e.keyCode >= 35 && e.keyCode <= 39)) {
                return;
            }

            // Block everything except digits
            if ((e.keyCode < 48 || e.keyCode > 57) && (e.keyCode < 96 || e.keyCode > 105)) {
                e.preventDefault();
            }
        });
    }
});

// Pagination handlers
const prevPageBtn = document.getElementById('prevPage');
const nextPageBtn = document.getElementById('nextPage');

if (prevPageBtn) {
    prevPageBtn.addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            displayProducts(filteredProducts);

            // Add a small animation
            prevPageBtn.classList.add('transform', 'scale-95');
            setTimeout(() => {
                prevPageBtn.classList.remove('transform', 'scale-95');
            }, 150);
        }
    });
}

if (nextPageBtn) {
    nextPageBtn.addEventListener('click', () => {
        const totalPages = Math.ceil(filteredProducts.length / itemsPerPage);
        if (currentPage < totalPages) {
            currentPage++;
            displayProducts(filteredProducts);

            // Add a small animation
            nextPageBtn.classList.add('transform', 'scale-95');
            setTimeout(() => {
                nextPageBtn.classList.remove('transform', 'scale-95');
            }, 150);
        }
    });
}

// Helper function for showing notifications
// --- Escaping data for section markup (keepup-62) ---
// Sections build their tables from template strings. A value that came from data
// -- a name, an id, a message -- goes through keepupEscapeHtml in markup, and
// through keepupJsArg when it has to be an argument of an inline handler: there
// it is a JavaScript string inside an HTML attribute, where escaping for HTML
// alone is not enough.
function keepupEscapeHtml(value) {
    return String(value === undefined || value === null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function keepupJsArg(value) {
    return keepupEscapeHtml(JSON.stringify(String(value === undefined || value === null ? '' : value)));
}
window.keepupEscapeHtml = keepupEscapeHtml;
window.keepupJsArg = keepupJsArg;
// --- end of escaping data for section markup ---

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    // The box the dismiss button removes, named as data rather than found by
    // walking up from the button: the markup is the section's, the relationship
    // is the shell's (keepup-93).
    notification.dataset.notification = '';
    notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${type === 'success' ? 'bg-green-100 text-green-800 border border-green-200' :
        type === 'error' ? 'bg-red-100 text-red-800 border border-red-200' :
            'bg-blue-100 text-blue-800 border border-blue-200'
        }`;
    notification.innerHTML = `
                <div class="flex items-center">
                    <i data-feather="${type === 'success' ? 'check-circle' : type === 'error' ? 'alert-circle' : 'info'}"
                       class="w-5 h-5 mr-2"></i>
                    <span data-notification-text></span>
                    <button type="button" data-action="shell.dismiss-notification"
                            class="ml-4 text-gray-500 hover:text-gray-700">
                        <i data-feather="x" class="w-4 h-4"></i>
                    </button>
                </div>
            `;
    // The message is text, never markup: it often carries a name that came from
    // data -- a user's, a section's -- and markup there would run (keepup-62).
    notification.querySelector('[data-notification-text]').textContent =
        message === null || message === undefined ? '' : String(message);
    document.body.appendChild(notification);

    // Refresh feather icons
    feather.replace();

    // Auto-hide after 5 seconds
    setTimeout(() => {
        if (notification.parentElement) {
            notification.remove();
        }
    }, 5000);
}

// The showNotification() notification style is the only thing the shell
// declares here. The button rules of one application's section that used to
// stand nearby left together with the code that set them: a section carries
// its own styles (keepup-27).
const additionalStyles = `
            .notification {
                transition: all 0.3s ease;
            }
        `;

// Add the styles to the document
const styleSheet = document.createElement('style');
styleSheet.textContent = additionalStyles;
document.head.appendChild(styleSheet);


document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
    if (window.MfaPanel) {
        MfaPanel.install();
    }
    checkAuth();
});


// Messages from other windows (keepup-77). Only this page's own origin is
// heard: any site the user has open can post to this window, and the handler
// used to act on whatever arrived. What is heard is handed on as a
// `keepup:message` event, which a section listens to for the messages that are
// its business -- the shell has no product logic of its own to run.
function keepupHandleWindowMessage(event) {
    if (event.origin !== window.location.origin) {
        return;
    }
    const data = event.data;
    if (!data || typeof data !== 'object' || typeof data.type !== 'string') {
        return;
    }
    window.dispatchEvent(new CustomEvent('keepup:message', { detail: data }));

    // Deprecated, kept for one release: the payment window of an application
    // still posts this and expects the shell to answer. An application listens
    // to `keepup:message` instead; this branch goes in the next minor release.
    if (data.type === 'payment_success') {
        showNotification('Payment successful! Plan activated.', 'success');
        if (typeof window.loadTariffsData === 'function') {
            setTimeout(() => window.loadTariffsData(), 1000);
        }
    }
}
window.addEventListener('message', keepupHandleWindowMessage);

/**
 * Event system for bulk markup application
 */
const markupEventSystem = {
    listeners: new Map(),
    addListener(shopType, callback) {
        if (!this.listeners.has(shopType)) {
            this.listeners.set(shopType, []);
        }
        this.listeners.get(shopType).push(callback);
    },
    removeListener(shopType, callback) {
        if (this.listeners.has(shopType)) {
            const listeners = this.listeners.get(shopType);
            const index = listeners.indexOf(callback);
            if (index > -1) {
                listeners.splice(index, 1);
            }
        }
    },
    async dispatchEvent(shopType, eventData) {
        if (this.listeners.has(shopType)) {
            const listeners = this.listeners.get(shopType);
            const results = [];

            for (const listener of listeners) {
                try {
                    const result = await listener(eventData);
                    results.push(result);
                } catch (error) {
                    console.error(`Error in ${shopType} markup listener:`, error);
                    results.push({ success: false, error: error.message });
                }
            }

            return results;
        }
        return [];
    },
    getRegisteredShopTypes() {
        return Array.from(this.listeners.keys());
    }
};

// Make it globally accessible for modules
window.markupEventSystem = markupEventSystem;

// Periodic payment status check while the tariffs section is open
function startPaymentStatusPolling() {
    setInterval(async () => {
        const tariffsSection = document.getElementById('tariffs-section');
        if (tariffsSection && tariffsSection.classList.contains('active')) {
            // Logic for checking pending payments could be added here
        }
    }, 30000);
}

window.tableEvents = {
    listeners: [],

    addListener(callback) {
        this.listeners.push(callback);
    },

    notifyTableRendered(articles) {
        this.listeners.forEach(listener => {
            try {
                listener(articles);
            } catch (error) {
                console.error('Error in table event listener:', error);
            }
        });
    }
};

// region application version handling

// Function for loading version information
async function loadVersionInfo() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/version', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const versionData = await response.json();
            return versionData;
        }
        return null;
    } catch (error) {
        console.error('Failed to load version information:', error);
        return null;
    }
}

// Function for displaying the version panel
async function showVersionPanel() {
    const versionPanel = document.getElementById('versionPanel');
    if (!versionPanel) return;

    // Check the user's roles
    if (currentUserIsAdmin()) {
        const versionData = await loadVersionInfo();
        if (versionData) {
            // Fill in the data
            document.getElementById('versionProject').textContent = versionData.project || '-';
            document.getElementById('versionRelease').textContent = versionData.version || '-';
            document.getElementById('versionBuildNumber').textContent = versionData.build_number || '-';
            document.getElementById('versionBuildDate').textContent = versionData.build_date || '-';

            // Show the panel
            versionPanel.classList.remove('hidden');

            // Refresh Feather icons
            feather.replace();
        }
    }
}

// Function for hiding the version panel
function hideVersionPanel() {
    const versionPanel = document.getElementById('versionPanel');
    if (versionPanel) {
        versionPanel.classList.add('hidden');
    }
}

// Function for checking and updating the version panel's visibility
function updateVersionPanelVisibility() {
    const versionPanel = document.getElementById('versionPanel');
    if (!versionPanel) return;

    if (currentUserIsAdmin()) {
        showVersionPanel();
    } else {
        hideVersionPanel();
    }
}

// endregion

// Function for opening the terms-of-use modal
function openTermsModal() {
    const modal = document.getElementById('termsModal');
    applyPublicConfig();
    modal.classList.remove('hidden');
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';

    // Initialize feather icons
    if (typeof feather !== 'undefined') {
        feather.replace();
    }
}

// Function for closing the terms-of-use modal
function closeTermsModal() {
    const modal = document.getElementById('termsModal');
    modal.classList.remove('active');

    // Small delay before hiding, for a smooth animation
    setTimeout(() => {
        modal.classList.add('hidden');
        document.body.style.overflow = '';
    }, 300);
}

// The actions of the shell's own markup: the sidebar, the sign-in block, the
// version panel and the terms modal carry data-action instead of an inline
// handler, and the names below are the whole list the shell registers
// (keepup-93). A section registers its own under its own prefix.
KeepupActions.register({
    'shell.logout': () => logout(),
    'shell.toggle-password': (element) => togglePasswordVisibility(element.dataset.input),
    'shell.open-terms': () => openTermsModal(),
    'shell.close-terms': () => closeTermsModal(),
    'shell.hide-version-panel': () => hideVersionPanel(),
    'shell.dismiss-notification': (element) => {
        const notification = element.closest('[data-notification]');
        if (notification) notification.remove();
    },
});

// Close on click outside the content
document.addEventListener('DOMContentLoaded', function() {
    const modal = document.getElementById('termsModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === this) {
                closeTermsModal();
            }
        });
    }

    // Close on Escape
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            const modal = document.getElementById('termsModal');
            if (modal && !modal.classList.contains('hidden')) {
                closeTermsModal();
            }
        }
    });
});

/**
 * The "forgot password" link, when the deployment has somewhere to send people.
 *
 * The address is the application's: the framework knows nothing about where a
 * password is reset, and named no page of its own. Without one the line stays
 * out of sight rather than offering a link that goes nowhere (keepup-178).
 */
function applyForgotPasswordLink() {
    const link = document.getElementById('forgotPasswordLink');
    const hint = document.getElementById('forgotPasswordHint');
    if (!link) return;
    loadPublicConfig().then(config => {
        const url = config && config.password_reset_url;
        if (!url) return;
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        if (hint) hint.classList.remove('hidden');
    }).catch(() => { /* without an address the sign-in page works as before */ });
}
