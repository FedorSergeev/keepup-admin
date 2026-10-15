// "Gateway" section (ADMIN role): the routes of the gateway, the state of each
// address in the replica that answered, and a card for every application behind
// a route marked "application" -- its plugins, whether they run, its health and
// its metrics. Served by keepup-gateway itself (doc/gateway.md).
//
// Every value shown came from somebody else's service, so it goes through
// gatewayEscape; the markup carries no handler -- buttons name an action that
// KeepupActions runs, which is what the panel's Content-Security-Policy allows.

var GATEWAY_ROUTE = '/selfcare/modules/gateway';
var GATEWAY_REFRESH_MS = 10000;
var gatewayState = { timer: null, loading: false };

function gatewayEscape(value) {
    return String(value === undefined || value === null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function gatewayBadge(text, tone) {
    var tones = {
        good: 'bg-green-100 text-green-800',
        warn: 'bg-yellow-100 text-yellow-800',
        bad: 'bg-red-100 text-red-800',
        none: 'bg-gray-100 text-gray-700'
    };
    return '<span class="px-2 py-0.5 rounded text-xs ' + (tones[tone] || tones.none) + '">'
        + gatewayEscape(text) + '</span>';
}

function gatewayOutcomeTone(plugin) {
    if (plugin.initialized) return 'good';
    if (plugin.outcome === 'disabled' || plugin.enabled === false) return 'none';
    return 'bad';
}

// The table: one row per route, its addresses with their state.
function gatewayRenderRoutes(data) {
    var routes = (data && data.routes) || [];
    if (!routes.length) {
        return '<p class="text-gray-500">The gateway has no routes. They are the "routes" of '
            + 'the gateway plugin\'s config.</p>';
    }
    var rows = routes.map(function (route) {
        var addresses = route.source === 'local'
            ? '<div class="text-gray-500">in process</div>'
            : (route.upstreams || []).map(function (item) {
            var tone = item.state === 'live' ? 'good' : 'warn';
            var note = item.state === 'cooling'
                ? ' ' + gatewayEscape(item.cooling_seconds) + ' s' : '';
            return '<div>' + gatewayEscape(item.address) + ' ' + gatewayBadge(item.state, tone)
                + note + '</div>';
        }).join('');
        var access = route.permission ? 'right ' + route.permission
            : (route.auth === 'session' ? 'signed in' : 'open');
        return '<tr>'
            + '<td class="px-3 py-2 font-mono">' + gatewayEscape(route.prefix) + '</td>'
            + '<td class="px-3 py-2">' + gatewayEscape(route.id) + '</td>'
            + '<td class="px-3 py-2">' + gatewayEscape(route.source || 'config') + '</td>'
            + '<td class="px-3 py-2">' + addresses + '</td>'
            + '<td class="px-3 py-2">' + gatewayEscape(access) + '</td>'
            + '<td class="px-3 py-2">' + gatewayEscape((route.methods || []).join(', ')) + '</td>'
            + '</tr>';
    }).join('');
    return '<table class="responsive-table min-w-full text-sm"><thead><tr>'
        + '<th class="px-3 py-2 text-left">Prefix</th><th class="px-3 py-2 text-left">Route</th>'
        + '<th class="px-3 py-2 text-left">Source</th>'
        + '<th class="px-3 py-2 text-left">Addresses</th><th class="px-3 py-2 text-left">Access</th>'
        + '<th class="px-3 py-2 text-left">Methods</th></tr></thead><tbody>' + rows
        + '</tbody></table>';
}

// One card per address of an application route.
function gatewayRenderApplications(data) {
    var applications = (data && data.applications) || [];
    if (!applications.length) {
        return '<p class="text-gray-500">No application stands behind the gateway: no route is '
            + 'marked "application": true and no service has registered itself.</p>';
    }
    return applications.map(function (application) {
        var cards = (application.addresses || []).map(gatewayRenderCard).join('');
        return '<div class="mb-6"><h3 class="text-lg font-semibold mb-2">'
            + gatewayEscape(application.route) + ' <span class="font-mono text-sm text-gray-500">'
            + gatewayEscape(application.prefix) + '</span></h3>'
            + '<div class="grid grid-cols-1 md:grid-cols-2 gap-4">' + cards + '</div></div>';
    }).join('');
}

function gatewayRenderCard(card) {
    var own = card.self || {};
    var health = card.health || {};
    var metrics = card.metrics || {};
    var title = own.application ? own.application : card.address;
    var head = '<div class="flex flex-wrap justify-between gap-2 mb-2">'
        + '<div><div class="font-semibold">' + gatewayEscape(title) + '</div>'
        + '<div class="text-xs text-gray-500 font-mono">' + gatewayEscape(card.address) + '</div>'
        + (own.instance ? '<div class="text-xs text-gray-500">replica '
            + gatewayEscape(own.instance) + ', keepup ' + gatewayEscape(own.keepup) + '</div>' : '')
        + '</div><div>'
        + gatewayBadge(card.reachable ? 'reachable' : 'not answering', card.reachable ? 'good' : 'bad')
        + ' ' + gatewayBadge(card.state || 'live', card.state === 'cooling' ? 'warn' : 'good')
        + '</div></div>';
    var healthLine = '<div class="text-sm">Health: '
        + (health.status === null || health.status === undefined
            ? gatewayBadge(health.error || 'no answer', 'bad')
            : gatewayBadge((health.state || '') + ' ' + health.status,
                health.status === 200 ? 'good' : 'bad'))
        + '</div>';
    var metricsLine = '<div class="text-sm">Metrics: '
        + (metrics.status === 200
            ? gatewayBadge(metrics.series + ' samples', 'good')
            : gatewayBadge(metrics.status ? 'answered ' + metrics.status
                : (metrics.error || 'no answer'), 'warn'))
        + '</div>';
    var plugins;
    if (own.error) {
        plugins = '<p class="text-sm text-red-700">' + gatewayEscape(own.error) + '</p>';
    } else {
        plugins = '<ul class="text-sm mt-2">' + (own.plugins || []).map(function (plugin) {
            return '<li class="flex flex-wrap gap-2 items-center">'
                + gatewayBadge(plugin.initialized ? 'running' : (plugin.outcome || 'not running'),
                    gatewayOutcomeTone(plugin))
                + '<span class="font-mono">' + gatewayEscape(plugin.id) + '</span>'
                + '<span class="text-gray-500">' + gatewayEscape(plugin.distribution || plugin.kind) + '</span>'
                + (plugin.reason ? '<span class="text-red-700">' + gatewayEscape(plugin.reason) + '</span>' : '')
                + '</li>';
        }).join('') + '</ul>';
    }
    return '<div class="border rounded-lg p-4 gateway-card">' + head + healthLine + metricsLine
        + plugins + '</div>';
}

async function gatewayFetch(path) {
    var token = localStorage.getItem('authToken');
    return fetch(path, { headers: { 'Authorization': 'Bearer ' + token } });
}

async function loadGateway() {
    if (gatewayState.loading) return;
    gatewayState.loading = true;
    var routesBox = document.getElementById('gateway-routes');
    var appsBox = document.getElementById('gateway-applications');
    try {
        var answers = await Promise.all([
            gatewayFetch('/api/admin/gateway/routes'),
            gatewayFetch('/api/admin/gateway/applications')
        ]);
        if (answers[0].ok) {
            routesBox.innerHTML = gatewayRenderRoutes(await answers[0].json());
        } else {
            routesBox.textContent = 'The routes could not be read (' + answers[0].status + ').';
        }
        if (answers[1].ok) {
            appsBox.innerHTML = gatewayRenderApplications(await answers[1].json());
        } else {
            appsBox.textContent = 'The applications could not be read (' + answers[1].status + ').';
        }
    } catch (error) {
        console.error('Gateway section failed to load:', error);
        routesBox.textContent = 'The gateway did not answer.';
    } finally {
        gatewayState.loading = false;
    }
}

function createGatewaySection() {
    var main = document.querySelector('main');
    if (!main || document.getElementById('gateway-section')) return;
    var section = document.createElement('div');
    section.id = 'gateway-section';
    section.className = 'content-section';
    section.innerHTML = ''
        + '<div class="bg-white rounded-lg shadow-sm p-6">'
        + '<div class="flex flex-wrap justify-between items-center gap-4 mb-4">'
        + '<h2 class="text-2xl font-bold text-gray-800">Gateway</h2>'
        + '<button data-action="gateway.refresh" class="px-4 py-2 bg-gray-200 rounded-lg hover:bg-gray-300">'
        + 'Refresh</button></div>'
        + '<p class="text-sm text-gray-500 mb-4">The state of an address is what the replica that '
        + 'answered knows about it; each replica finds out for itself.</p>'
        + '<h3 class="text-lg font-semibold mb-2">Routes</h3><div id="gateway-routes" class="mb-6">'
        + 'Loading...</div>'
        + '<h3 class="text-lg font-semibold mb-2">Applications</h3>'
        + '<div id="gateway-applications">Loading...</div></div>';
    main.appendChild(section);
}

function addGatewayNavigation() {
    var nav = document.getElementById('main-nav');
    if (!nav || document.querySelector('[data-target="gateway-section"]')) return;
    var container = document.getElementById('modules-nav-container') || nav;
    var item = document.createElement('a');
    item.href = '#';
    item.className = 'nav-item';
    item.setAttribute('data-target', 'gateway-section');
    item.setAttribute('data-action', 'gateway.open');
    var icon = document.createElement('i');
    icon.setAttribute('data-feather', 'share-2');
    icon.className = 'w-5 h-5 mr-3';
    var text = document.createElement('span');
    text.className = 'nav-text';
    text.textContent = 'Gateway';
    item.appendChild(icon);
    item.appendChild(text);
    container.appendChild(item);
}

function showGatewaySection() {
    if (window.appRouter) window.appRouter.navigate(GATEWAY_ROUTE);
    document.querySelectorAll('.content-section').forEach(function (s) { s.classList.remove('active'); });
    var section = document.getElementById('gateway-section');
    if (section) section.classList.add('active');
    var title = document.getElementById('current-section-title');
    if (title) title.textContent = 'Gateway';
    loadGateway();
    if (gatewayState.timer) clearInterval(gatewayState.timer);
    gatewayState.timer = setInterval(function () {
        var open = document.getElementById('gateway-section');
        if (!open || !open.classList.contains('active') || document.hidden) {
            clearInterval(gatewayState.timer);
            gatewayState.timer = null;
            return;
        }
        loadGateway();
    }, GATEWAY_REFRESH_MS);
}

function initGatewayModule() {
    if (window.appRouter) window.appRouter.registerRoute(GATEWAY_ROUTE, showGatewaySection);
    if (window.appSections) {
        window.appSections.registerSection('gateway-section', GATEWAY_ROUTE, showGatewaySection);
    }
    addGatewayNavigation();
    createGatewaySection();
    if (window.feather) window.feather.replace();
    if (window.location.pathname === GATEWAY_ROUTE) setTimeout(showGatewaySection, 100);
}

if (typeof KeepupActions !== 'undefined') {
    KeepupActions.register({
        'gateway.refresh': function () { loadGateway(); },
        'gateway.open': function (element, event) {
            if (event) event.preventDefault();
            document.querySelectorAll('.nav-item').forEach(function (item) { item.classList.remove('active'); });
            element.classList.add('active');
            showGatewaySection();
        }
    });
}

if (typeof window !== 'undefined') {
    window.initGatewayModule = initGatewayModule;
}
if (typeof module !== 'undefined') {
    module.exports = { gatewayEscape: gatewayEscape, gatewayRenderRoutes: gatewayRenderRoutes,
                       gatewayRenderApplications: gatewayRenderApplications };
}
