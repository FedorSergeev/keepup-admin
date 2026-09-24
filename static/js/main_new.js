
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
const CSRF_COOKIE = 'ss_csrf';
const CSRF_HEADER = 'X-CSRF-Token';
const CSRF_SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

function readCookie(name) {
    const prefix = name + '=';
    const found = (document.cookie || '').split(';')
        .map(part => part.trim())
        .find(part => part.startsWith(prefix));
    return found ? decodeURIComponent(found.slice(prefix.length)) : null;
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
    const wrapped = function (input, init) {
        const request = typeof Request !== 'undefined' && input instanceof Request ? input : null;
        const method = String((init && init.method) || (request && request.method) || 'GET').toUpperCase();
        const url = request ? request.url : String(input);
        const csrf = readCookie(CSRF_COOKIE);
        if (csrf && !CSRF_SAFE_METHODS.includes(method) && isSameOriginUrl(url)) {
            const headers = new Headers((init && init.headers) || (request && request.headers) || undefined);
            if (!headers.has(CSRF_HEADER)) {
                headers.set(CSRF_HEADER, csrf);
            }
            init = Object.assign({}, init || {}, { headers });
        }
        return originalFetch.call(target, input, init);
    };
    wrapped.csrfAware = true;
    target.fetch = wrapped;
}

installCsrfFetch(window);

const ROLE_ADMIN = 'ADMIN';
const ROLE_CLIENT = 'CLIENT';


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
    setupAuthForms();

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
    return verifyToken();
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
let publicConfigPromise = null;

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
    // contradicts the landing page, which sends people to the bot.
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

    // Forgot password (task 272): the link leads to the bot, and the reset is
    // issued by it. The bot's name comes from the server -- it differs between
    // the stand and production, and hardcoding it into the markup would mean
    // keeping two truths. If the server doesn't answer, the link stays a hint
    // with no destination, rather than a broken address.
    applyForgotPasswordLink();

    // Updated sign-in form handler
    document.getElementById('loginForm').addEventListener('submit', function (e) {
        e.preventDefault();
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

// The token lives a day and expires silently: without this the panel simply
// starts getting refusals in the middle of the work. The server tells how long
// it has at login, and the session is exchanged an hour before the end -- while
// the token is still valid, which is the only time the exchange is allowed.
const SESSION_REFRESH_LEAD_SECONDS = 3600;
let sessionRefreshTimer = null;

function scheduleSessionRefresh(expiresInSeconds) {
    if (sessionRefreshTimer) {
        clearTimeout(sessionRefreshTimer);
        sessionRefreshTimer = null;
    }
    if (!expiresInSeconds) {
        return;
    }
    // Never sooner than a minute: a token that short is about to be replaced
    // by a login anyway, and a tight timer would spin.
    const delay = Math.max(60, expiresInSeconds - SESSION_REFRESH_LEAD_SECONDS);
    sessionRefreshTimer = setTimeout(refreshSession, delay * 1000);
}

async function refreshSession() {
    if (!localStorage.getItem('authToken')) {
        return;
    }
    try {
        const response = await fetch('/api/auth/refresh', { method: 'POST' });
        if (!response.ok) {
            // Nothing to do about it here: the next request will be refused and
            // the panel asks for a login as before.
            console.warn('Failed to refresh session:', response.status);
            return;
        }
        const data = await response.json();
        if (data.access_token) {
            // The renewed session is already in the cookie; the body's token
            // is for agents and is not kept.
            scheduleSessionRefresh(data.expires_in);
        }
    } catch (error) {
        console.warn('Failed to refresh session:', error);
    }
}

async function login(username, password) {
    try {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });

        const data = await response.json();

        if (response.ok) {
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
        } else {
            showAuthMessage(data.detail || 'Sign-in failed', 'error');
        }
    } catch (error) {
        showAuthMessage('Network error: ' + error, 'error');
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


async function logout() {
    // The server ends the session first: forgetting it only in this browser
    // would leave a copied token working.
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (error) {
        console.warn('Could not end the session on the server:', error);
    }
    localStorage.removeItem('authToken');
    localStorage.removeItem('auth_token');
    currentUser = null;
    updateUIAfterAuth();
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

        const userRole = document.getElementById('userRole');
        userRole.textContent = currentUser.role === ROLE_ADMIN ? 'Admin' : 'Client';
        userRole.className = `ml-2 role-badge ${currentUser.role === ROLE_ADMIN ? 'role-admin' : 'role-client'}`;

        document.getElementById('userInfo').style.display = 'block';

        const adminItems = document.querySelectorAll('.admin-only');
        adminItems.forEach(item => {
            if (currentUser.role === ROLE_ADMIN) {
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
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${type === 'success' ? 'bg-green-100 text-green-800 border border-green-200' :
        type === 'error' ? 'bg-red-100 text-red-800 border border-red-200' :
            'bg-blue-100 text-blue-800 border border-blue-200'
        }`;
    notification.innerHTML = `
                <div class="flex items-center">
                    <i data-feather="${type === 'success' ? 'check-circle' : type === 'error' ? 'alert-circle' : 'info'}"
                       class="w-5 h-5 mr-2"></i>
                    <span>${message}</span>
                    <button onclick="this.parentElement.parentElement.remove()"
                            class="ml-4 text-gray-500 hover:text-gray-700">
                        <i data-feather="x" class="w-4 h-4"></i>
                    </button>
                </div>
            `;
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
    checkAuth();
});


// Handler for messages from the payment window
window.addEventListener('message', function(event) {
    if (event.data.type === 'payment_success') {
        showNotification('Payment successful! Plan activated.', 'success');
        // Reload the plans data
        setTimeout(() => {
            loadTariffsData();
        }, 1000);
    }
});

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

    // Check the user's role
    if (currentUser && currentUser.role === ROLE_ADMIN) {
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

    if (currentUser && currentUser.role === ROLE_ADMIN) {
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
 * The "forgot password" link leads to the bot: the reset is issued by it,
 * not this page (task 272). The bot's name is known to the server; without
 * a response the link stays plain text.
 */
function applyForgotPasswordLink() {
    const link = document.getElementById('forgotPasswordLink');
    if (!link) return;
    loadPublicConfig().then(config => {
        // The whole address comes from the server: building it here would mean
        // hardcoding someone else's domain into a panel that loads nothing of
        // its own. Where it comes from is the application's business: for one
        // it's a Telegram bot, for another its own page, for a third there's
        // no reset at all, and then the link stays plain text. Before task
        // keepup-27 the shell asked for the address directly at the bot's
        // route, meaning the framework knew about the bot (keepup-27).
        const url = config && config.password_reset_url;
        if (!url) return;
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
    }).catch(() => { /* without an address the sign-in page works as before */ });
}
