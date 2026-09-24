// "Cluster" module (ADMIN role): application replicas, their plugins and commands.
//
// Each replica writes itself into the shared registry and picks up the
// commands addressed to it on its own, so the section shows every replica,
// not just the one that answered the request, and a command reaches its
// target through any of them. Details -- doc/cluster_control.md.
//
// The chrome's styling (sidebar, menu items) belongs to the theme; the module
// styles only its own section -- see tests/frontend_style_boundary_tests.py.

// While the section is open it refreshes itself: a command executes within
// the polling interval, and its result should appear without pressing "Refresh".
var CLUSTER_REFRESH_MS = 5000;
var CLUSTER_ROUTE = '/selfcare/modules/cluster';

var clusterState = {
    data: null,
    error: '',
    notice: '',
    loading: false,
    sending: false,
    timer: null
};

var CLUSTER_STATE_LABELS = {
    serving: { text: 'serving', css: 'bg-green-100 text-green-800' },
    stopped: { text: 'stopped', css: 'bg-yellow-100 text-yellow-800' },
    gone: { text: 'gone', css: 'bg-red-100 text-red-800' }
};

var CLUSTER_ACTION_LABELS = { stop: 'Stop', start: 'Start', restart: 'Restart' };

var CLUSTER_COMMAND_STATUS = {
    pending: 'waiting for replica',
    executing: 'executing',
    done: 'done',
    failed: 'failed',
    expired: 'expired'
};

function initClusterModule() {
    registerClusterRoutes();
    registerClusterSections();
    addClusterNavigation();
    createClusterSection();

    if (window.location.pathname === CLUSTER_ROUTE) {
        setTimeout(() => showClusterSection(), 100);
    }
}

function registerClusterRoutes() {
    if (window.appRouter) {
        window.appRouter.registerRoute(CLUSTER_ROUTE, () => showClusterSection());
    }
}

function registerClusterSections() {
    if (window.appSections) {
        window.appSections.registerSection('cluster-section', CLUSTER_ROUTE, () => showClusterSection());
    }
}

function addClusterNavigation() {
    const mainNav = document.getElementById('main-nav');
    if (!mainNav) return;
    if (document.querySelector('[data-target="cluster-section"]')) return;

    const container = document.getElementById('modules-nav-container') || mainNav;
    const navItem = document.createElement('a');
    navItem.href = '#';
    navItem.className = 'nav-item';
    navItem.setAttribute('data-target', 'cluster-section');
    navItem.innerHTML = `
        <i data-feather="server" class="w-5 h-5 mr-3"></i>
        <span class="nav-text">Cluster</span>
    `;
    navItem.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        showClusterSection();
        document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
        this.classList.add('active');
    });
    container.appendChild(navItem);
    feather.replace();
}

function createClusterSection() {
    const mainContent = document.querySelector('main');
    if (!mainContent) return;
    if (document.getElementById('cluster-section')) return;

    const section = document.createElement('div');
    section.id = 'cluster-section';
    section.className = 'content-section';
    section.innerHTML = `
        <div class="bg-white rounded-lg shadow-sm p-6 cluster-admin">
            <div class="flex flex-wrap justify-between items-center gap-4 mb-4">
                <h2 class="text-2xl font-bold text-gray-800 flex items-center">
                    <i data-feather="server" class="w-6 h-6 mr-2 text-blue-600"></i>
                    Cluster
                </h2>
                <div class="flex flex-wrap items-center gap-3">
                    <span id="cluster-summary" class="text-sm text-gray-500"></span>
                    <button onclick="loadCluster()"
                            class="px-4 py-2 bg-gray-200 rounded-lg hover:bg-gray-300 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                        Refresh
                    </button>
                </div>
            </div>
            <p class="text-sm text-gray-500 mb-4">
                A stopped replica is alive but does not serve API or background jobs —
                traffic goes to the others. Restarting applies plugin toggles
                marked "after restart". A replica picks up a command within
                <span id="cluster-heartbeat">10</span> s.
            </p>
            <div id="cluster-notice"></div>
            <div id="cluster-list">
                <div class="text-center py-12 text-gray-500">Loading...</div>
            </div>
        </div>
    `;
    mainContent.appendChild(section);
    feather.replace();
}

function showClusterSection() {
    if (window.appRouter) {
        window.appRouter.navigate(CLUSTER_ROUTE);
    }
    document.querySelectorAll('.content-section').forEach(s => s.classList.remove('active'));
    const section = document.getElementById('cluster-section');
    if (section) {
        section.classList.add('active');
        const title = document.getElementById('current-section-title');
        if (title) title.textContent = 'Cluster';
    }
    loadCluster();
    startClusterRefresh();
    feather.replace();
}

function startClusterRefresh() {
    stopClusterRefresh();
    clusterState.timer = setInterval(function () {
        const section = document.getElementById('cluster-section');
        if (!section || !section.classList.contains('active') || document.hidden) {
            stopClusterRefresh();
            return;
        }
        loadCluster();
    }, CLUSTER_REFRESH_MS);
}

function stopClusterRefresh() {
    if (clusterState.timer) {
        clearInterval(clusterState.timer);
        clusterState.timer = null;
    }
}

function escapeClusterHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
}

function formatClusterTime(value) {
    if (!value) return '—';
    const date = new Date(String(value).replace(' ', 'T') + (String(value).endsWith('Z') ? '' : 'Z'));
    return isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

async function clusterFetch(path, options) {
    const token = localStorage.getItem('authToken');
    const headers = Object.assign(
        { 'Authorization': `Bearer ${token}` },
        (options && options.body) ? { 'Content-Type': 'application/json' } : {}
    );
    return fetch(path, Object.assign({ headers }, options || {}));
}

async function loadCluster() {
    if (clusterState.loading) return;
    clusterState.loading = true;
    try {
        const response = await clusterFetch('/api/admin/cluster');
        if (!response.ok) {
            clusterState.error = response.status === 403
                ? 'The cluster is visible to administrators only'
                : `Failed to get cluster state (${response.status})`;
            clusterState.data = null;
        } else {
            clusterState.error = '';
            clusterState.data = await response.json();
        }
    } catch (error) {
        console.error('Failed to load cluster state:', error);
        clusterState.error = 'Failed to load cluster state';
        clusterState.data = null;
    } finally {
        clusterState.loading = false;
        renderCluster();
    }
}

// The server makes the final call; the confirmation here is so the person
// understands that the last serving replica takes the whole product down with it.
function clusterIsLastServing(target) {
    const members = (clusterState.data && clusterState.data.members) || [];
    return target.display_state === 'serving' && !members.some(m =>
        m.instance_id !== target.instance_id && m.display_state === 'serving');
}

async function sendClusterCommand(instanceId, action) {
    if (clusterState.sending) return;
    const members = (clusterState.data && clusterState.data.members) || [];
    const target = members.find(m => m.instance_id === instanceId);
    if (!target) return;

    let force = false;
    const name = target.instance_name || target.instance_id;
    if (action === 'stop' && clusterIsLastServing(target)) {
        if (!confirm(`"${name}" is the last serving replica. After stopping, the product will stop responding to everything except panel sign-in and this section. Stop anyway?`)) return;
        force = true;
    } else if (!confirm(`${CLUSTER_ACTION_LABELS[action]} replica "${name}"?`)) {
        return;
    }

    clusterState.sending = true;
    try {
        const response = await clusterFetch(
            `/api/admin/cluster/${encodeURIComponent(instanceId)}/commands`,
            { method: 'POST', body: JSON.stringify({ action: action, force: force }) });
        const data = await response.json().catch(() => ({}));
        clusterState.notice = response.ok
            ? `Command "${CLUSTER_ACTION_LABELS[action]}" sent to replica "${name}".`
            : `Command rejected: ${data.detail || response.status}`;
    } catch (error) {
        console.error('Failed to send command to replica:', error);
        clusterState.notice = 'Failed to send command';
    } finally {
        clusterState.sending = false;
        await loadCluster();
    }
}

function clusterPlugins(member) {
    const pending = member.plugins_pending || [];
    const running = member.plugins_running || [];
    const parts = running.map(id => `
        <span class="px-2 py-0.5 text-xs bg-blue-50 text-blue-800 rounded-full">${escapeClusterHtml(id)}</span>`);
    pending.forEach(id => parts.push(`
        <span class="px-2 py-0.5 text-xs bg-yellow-50 text-yellow-800 rounded-full"
              title="Takes effect after the replica restarts">${escapeClusterHtml(id)} ↻</span>`));
    return parts.length ? parts.join(' ') : '<span class="text-gray-400">—</span>';
}

function clusterLastCommand(member) {
    const command = member.last_command;
    if (!command) return '<span class="text-gray-400">—</span>';
    const status = CLUSTER_COMMAND_STATUS[command.status] || command.status;
    const who = command.requested_by_name ? ` · ${escapeClusterHtml(command.requested_by_name)}` : '';
    const result = command.result
        ? `<div class="text-xs text-gray-500">${escapeClusterHtml(command.result)}</div>` : '';
    return `<div>${escapeClusterHtml(CLUSTER_ACTION_LABELS[command.action] || command.action)}: ${escapeClusterHtml(status)}${who}</div>
            <div class="text-xs text-gray-400">${escapeClusterHtml(formatClusterTime(command.requested_at))}</div>${result}`;
}

function clusterButton(member, action, enabled, reason) {
    const css = action === 'stop' ? 'bg-yellow-500 hover:bg-yellow-600 text-white'
        : action === 'start' ? 'bg-green-600 hover:bg-green-700 text-white'
        : 'bg-blue-600 hover:bg-blue-700 text-white';
    const id = escapeClusterHtml(member.instance_id);
    return `<button class="px-3 py-1 rounded text-sm ${enabled ? css : 'bg-gray-200 text-gray-400 cursor-not-allowed'}"
                ${enabled ? `onclick="sendClusterCommand('${id}', '${action}')"` : 'disabled'}
                ${reason ? `title="${escapeClusterHtml(reason)}"` : ''}>${CLUSTER_ACTION_LABELS[action]}</button>`;
}

function clusterActions(member) {
    if (!member.alive) return '<span class="text-xs text-gray-400">not accepting commands</span>';
    const busy = member.last_command && ['pending', 'executing'].includes(member.last_command.status);
    const buttons = [
        member.state === 'stopped' ? clusterButton(member, 'start', !busy) : clusterButton(member, 'stop', !busy),
        clusterButton(member, 'restart', !busy && member.can_restart,
            member.can_restart ? '' : member.restart_blocker)
    ];
    const blocker = member.can_restart ? ''
        : `<div class="text-xs text-gray-500 mt-1">${escapeClusterHtml(member.restart_blocker)}</div>`;
    return `<div class="flex flex-wrap gap-2">${buttons.join('')}</div>${blocker}`;
}

function renderCluster() {
    const list = document.getElementById('cluster-list');
    if (!list) return;
    const notice = document.getElementById('cluster-notice');
    if (notice) {
        notice.innerHTML = clusterState.notice
            ? `<div class="mb-4 p-3 rounded bg-blue-50 text-blue-800 text-sm">${escapeClusterHtml(clusterState.notice)}</div>` : '';
    }
    if (clusterState.error) {
        list.innerHTML = `<div class="text-center py-6 text-red-600">${escapeClusterHtml(clusterState.error)}</div>`;
        return;
    }
    const data = clusterState.data || {};
    const members = data.members || [];
    const heartbeat = document.getElementById('cluster-heartbeat');
    if (heartbeat && data.heartbeat_seconds) heartbeat.textContent = data.heartbeat_seconds;
    const summary = document.getElementById('cluster-summary');
    if (summary) {
        const count = state => members.filter(m => m.display_state === state).length;
        summary.textContent = `replicas: ${members.length} · serving: ${count('serving')}`
            + (count('stopped') ? ` · stopped: ${count('stopped')}` : '')
            + (count('gone') ? ` · gone: ${count('gone')}` : '');
    }
    if (!members.length) {
        list.innerHTML = '<div class="text-center py-6 text-gray-500">No replicas have registered yet</div>';
        return;
    }

    list.innerHTML = `
        <table class="responsive-table min-w-full text-sm">
            <thead>
                <tr class="text-left text-gray-600 border-b">
                    <th class="py-2 pr-4">Replica</th>
                    <th class="py-2 pr-4">Status</th>
                    <th class="py-2 pr-4">Build and start</th>
                    <th class="py-2 pr-4">Plugins</th>
                    <th class="py-2 pr-4">Last command</th>
                    <th class="py-2 pr-4">Actions</th>
                </tr>
            </thead>
            <tbody>
                ${members.map(member => {
                    const label = CLUSTER_STATE_LABELS[member.display_state] || CLUSTER_STATE_LABELS.gone;
                    const self = member.instance_id === data.answered_by
                        ? ' <span class="text-xs text-gray-400">(answering you)</span>' : '';
                    return `
                    <tr class="border-b">
                        <td class="py-2 pr-4">
                            <div class="font-medium text-gray-800">${escapeClusterHtml(member.instance_name || member.instance_id)}${self}</div>
                            <div class="text-xs text-gray-500">${escapeClusterHtml(member.host)} · pid ${escapeClusterHtml(member.pid)}</div>
                        </td>
                        <td class="py-2 pr-4">
                            <span class="px-2 py-1 text-xs rounded-full ${label.css}">${label.text}</span>
                            <div class="text-xs text-gray-400 mt-1">last seen ${escapeClusterHtml(formatClusterTime(member.last_seen))}</div>
                        </td>
                        <td class="py-2 pr-4">
                            <div>${escapeClusterHtml(member.build || '—')}</div>
                            <div class="text-xs text-gray-500">${escapeClusterHtml(formatClusterTime(member.started_at))}</div>
                        </td>
                        <td class="py-2 pr-4">${clusterPlugins(member)}</td>
                        <td class="py-2 pr-4">${clusterLastCommand(member)}</td>
                        <td class="py-2 pr-4">${clusterActions(member)}</td>
                    </tr>`;
                }).join('')}
            </tbody>
        </table>
    `;
    if (window.feather) feather.replace();
}
