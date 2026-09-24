var currentEditingModule = null;
let modulesDataLoaded = false;
let isModulesDataLoading = false;
let isModulesRouteActive = false;

async function loadModulesData() {
    // Already loaded or currently loading -- nothing to do
    if (modulesDataLoaded || isModulesDataLoading) {
        console.log('Modules data already loaded or loading, skipping...');
        return;
    }

    isModulesDataLoading = true;

    try {
        const token = localStorage.getItem('authToken');

        console.log('Loading modules data...');

        // Load the modules
        const modulesResponse = await fetch('/api/admin/modules', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        // Load the role-module associations
        const rolesResponse = await fetch('/api/admin/role-modules', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (modulesResponse.ok && rolesResponse.ok) {
            const modules = await modulesResponse.json();
            const rolesData = await rolesResponse.json();

            displayModules(modules.modules, rolesData);
            updateStats(modules.modules, rolesData);

            modulesDataLoaded = true;
            console.log('Modules data loaded successfully');
        } else {
            throw new Error('Error loading data');
        }
    } catch (error) {
        console.error('Error loading modules:', error);
        showNotification('Error loading module data', 'error');
        modulesDataLoaded = false;
    } finally {
        isModulesDataLoading = false;
    }
}

function reloadModulesData() {
    modulesDataLoaded = false;
    loadModulesData();
}

function renderModules() {
    addModulesNavigation();
    addModulesSection();
    setupModulesEventListeners();
    setupGlobalRouter();
    setTimeout(() => {
        handleUrlChange();
    }, 100);
}

function addModulesNavigation() {
    const mainNav = document.getElementById('main-nav');
    if (!mainNav) return;

    if (document.querySelector('[data-target="modules-management-section"]')) return;

    const modulesNavItem = document.createElement('a');
    modulesNavItem.href = '#';
    modulesNavItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600 admin-only';
    modulesNavItem.setAttribute('data-target', 'modules-management-section');
    modulesNavItem.innerHTML = `
        <i data-feather="grid" class="w-5 h-5 mr-3"></i>
        <span class="nav-text">Module management</span>
    `;

    modulesNavItem.addEventListener('click', function(e) {
        e.preventDefault();
        handleModulesNavigation.call(this, e);
    });

    const adminItems = document.querySelectorAll('.admin-only');
    const lastAdminItem = adminItems[adminItems.length - 1];

    if (lastAdminItem) {
        lastAdminItem.parentNode.insertBefore(modulesNavItem, lastAdminItem.nextSibling);
    } else {
        mainNav.appendChild(modulesNavItem);
    }

    // Update the state for the collapsed menu
    const sidebar = document.getElementById('sidebar');
    if (sidebar && sidebar.classList.contains('sidebar-collapsed')) {
        updateNavItemForCollapsedState(modulesNavItem, true);
    }

    feather.replace();
}

/**
 * Adds a function that was missing.
 */
function updateNavItemForCollapsedState(navItem, isCollapsed) {
    const navText = navItem.querySelector('.nav-text');
    const icon = navItem.querySelector('i');

    if (isCollapsed) {
        if (navText) {
            navText.style.display = 'none';
        }
        navItem.classList.add('justify-center', 'px-3');
        if (icon) {
            icon.classList.remove('mr-3');
        }
    } else {
        if (navText) {
            navText.style.display = 'inline';
        }
        navItem.classList.remove('justify-center', 'px-3');
        if (icon) {
            icon.classList.add('mr-3');
        }
    }
}

function handleModulesNavigation(e) {
    e.preventDefault();
    window.history.pushState({}, '', '/selfcare/modules/modules');
    showModulesManagementSection();
}

function showModulesManagementSection() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(navItem => {
        navItem.classList.remove('text-white', 'bg-blue-800');
        navItem.classList.add('text-blue-100', 'hover:bg-blue-600');
    });

    const modulesNav = document.querySelector('[data-target="modules-management-section"]');
    if (modulesNav) {
        modulesNav.classList.remove('text-blue-100', 'hover:bg-blue-600');
        modulesNav.classList.add('text-white', 'bg-blue-800');
    }

    // Hide all content sections
    const contentSections = document.querySelectorAll('.content-section');
    contentSections.forEach(section => {
        section.classList.remove('active');
    });

    // Show the module management section
    const targetSection = document.getElementById('modules-management-section');
    if (targetSection) {
        targetSection.classList.add('active');
        document.getElementById('current-section-title').textContent = 'Module management';

        // Load data if needed
        if (!modulesDataLoaded && !isModulesDataLoading) {
            loadModulesData();
        }
        // Plugin state -- always reload: it changes with a server restart.
        loadBackendPlugins();
    }

    isModulesRouteActive = true;
    feather.replace();
}

function addModulesSection() {
    if (document.getElementById('modules-management-section')) return;

    const modulesSection = document.createElement('div');
    modulesSection.id = 'modules-management-section';
    modulesSection.className = 'content-section';
    modulesSection.innerHTML = `
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-gray-800">Module management</h2>
                <div class="flex space-x-2">
                    <button onclick="showImportExportModal()"
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="download" class="w-4 h-4 mr-2"></i>
                        Import/Export
                    </button>
                    <button onclick="showAddModuleModal()"
                            class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center">
                        <i data-feather="plus" class="w-4 h-4 mr-2"></i>
                        Add module
                    </button>
                </div>
            </div>

            <!-- Stats -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6" id="modules-stats">
                <div class="bg-gradient-to-r from-blue-50 to-blue-100 border border-blue-200 rounded-lg p-4 text-center">
                    <div class="text-2xl font-bold text-blue-600" id="total-modules">0</div>
                    <div class="text-sm text-blue-800">Total modules</div>
                </div>
                <div class="bg-gradient-to-r from-green-50 to-green-100 border border-green-200 rounded-lg p-4 text-center">
                    <div class="text-2xl font-bold text-green-600" id="active-modules">0</div>
                    <div class="text-sm text-green-800">Active</div>
                </div>
                <div class="bg-gradient-to-r from-purple-50 to-purple-100 border border-purple-200 rounded-lg p-4 text-center">
                    <div class="text-2xl font-bold text-purple-600" id="admin-role-modules">0</div>
                    <div class="text-sm text-purple-800">For admins</div>
                </div>
                <div class="bg-gradient-to-r from-orange-50 to-orange-100 border border-orange-200 rounded-lg p-4 text-center">
                    <div class="text-2xl font-bold text-orange-600" id="client-role-modules">0</div>
                    <div class="text-sm text-orange-800">For clients</div>
                </div>
            </div>

            <!-- Modules table -->
            <div class="bg-white rounded-lg border border-gray-200 overflow-hidden">
                <div class="overflow-x-auto">
                    <table class="responsive-table min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Module
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    ID
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Version
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Roles
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Status
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Actions
                                </th>
                            </tr>
                        </thead>
                        <tbody id="modules-table-body" class="bg-white divide-y divide-gray-200">
                            <tr>
                                <td colspan="6" class="px-6 py-4 text-center text-gray-500">
                                    Loading modules...
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Backend plugins: read-only, the decision takes effect from the next restart -->
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-4">
                <div>
                    <h2 class="text-xl font-bold text-gray-800">Backend plugins</h2>
                    <p class="text-sm text-gray-500 mt-1">
                        Enabled by the <code>enabled</code> flag in <code>config/modules.json</code> or by the
                        <code>PLUGINS_ENABLE</code> / <code>PLUGINS_DISABLE</code> variables; roles decide only visibility.
                        A change takes effect after a restart.
                    </p>
                </div>
                <button onclick="loadBackendPlugins()"
                        class="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 flex items-center">
                    <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                    Refresh
                </button>
            </div>
            <div class="bg-white rounded-lg border border-gray-200 overflow-hidden">
                <div class="overflow-x-auto">
                    <table class="responsive-table min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Plugin</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">ID</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Priority</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Decision</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">State</th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Toggle</th>
                            </tr>
                        </thead>
                        <tbody id="backend-plugins-table-body" class="bg-white divide-y divide-gray-200">
                            <tr><td colspan="6" class="px-6 py-4 text-center text-gray-500">Loading plugins...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Modals will be added here -->
    `;

    const mainContent = document.querySelector('main');
    if (mainContent) {
        mainContent.appendChild(modulesSection);
    }

    // Add the modals
    addModulesModals();

    feather.replace();
}

function addModulesModals() {
    // Modal for adding/editing a module
    const moduleModal = document.createElement('div');
    moduleModal.id = 'module-modal';
    moduleModal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4 hidden';
    moduleModal.innerHTML = `
        <div class="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
            <div class="flex justify-between items-center p-6 border-b">
                <h3 class="text-xl font-bold text-gray-800" id="module-modal-title">Add module</h3>
                <button onclick="closeModuleModal()" class="text-gray-500 hover:text-gray-700 p-2">
                    <i data-feather="x" class="w-6 h-6"></i>
                </button>
            </div>

            <div class="flex-1 overflow-auto p-6">
                <form id="module-form" class="space-y-4">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Module ID *</label>
                            <input type="text" id="module-id" required
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                   placeholder="Unique identifier">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                            <input type="text" id="module-name" required
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                   placeholder="Display name">
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Description</label>
                        <textarea id="module-description" rows="2"
                                  class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                  placeholder="Module description"></textarea>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">JS path</label>
                            <input type="text" id="module-js-path"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                   placeholder="/static/modules/js/...">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">CSS path</label>
                            <input type="text" id="module-css-path"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                   placeholder="/static/modules/css/...">
                        </div>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Init function</label>
                            <input type="text" id="module-init-function"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                                   placeholder="renderModule">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">Version</label>
                            <input type="text" id="module-version" value="1.0.0"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        </div>
                    </div>

                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Configuration (JSON)</label>
                        <textarea id="module-config" rows="4"
                                  class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm"
                                  placeholder='{"key": "value"}'></textarea>
                    </div>

                    <div>
                        <label class="flex items-center">
                            <input type="checkbox" id="module-active" class="rounded text-blue-600" checked>
                            <span class="ml-2 text-sm text-gray-700">Active module</span>
                        </label>
                    </div>
                </form>
            </div>

            <div class="flex justify-end space-x-2 p-6 border-t bg-gray-50">
                <button onclick="closeModuleModal()"
                        class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                    Cancel
                </button>
                <button onclick="saveModule()"
                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                    <i data-feather="save" class="w-4 h-4 mr-2"></i>
                    Save
                </button>
            </div>
        </div>
    `;

    // Modal for managing roles
    const rolesModal = document.createElement('div');
    rolesModal.id = 'roles-modal';
    rolesModal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4 hidden';
    rolesModal.innerHTML = `
        <div class="bg-white rounded-lg shadow-xl w-full max-w-md max-h-[90vh] overflow-hidden flex flex-col">
            <div class="flex justify-between items-center p-6 border-b">
                <h3 class="text-xl font-bold text-gray-800" id="roles-modal-title">Role settings</h3>
                <button onclick="closeRolesModal()" class="text-gray-500 hover:text-gray-700 p-2">
                    <i data-feather="x" class="w-6 h-6"></i>
                </button>
            </div>

            <div class="flex-1 overflow-auto p-6">
                <div id="roles-list" class="space-y-3">
                    <!-- Role list goes here -->
                </div>
            </div>

            <div class="flex justify-end space-x-2 p-6 border-t bg-gray-50">
                <button onclick="closeRolesModal()"
                        class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                    Cancel
                </button>
                <button onclick="saveRoleModules()"
                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                    <i data-feather="save" class="w-4 h-4 mr-2"></i>
                    Save
                </button>
            </div>
        </div>
    `;

    // Modal for import/export
    const importExportModal = document.createElement('div');
    importExportModal.id = 'import-export-modal';
    importExportModal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4 hidden';
    importExportModal.innerHTML = `
        <div class="bg-white rounded-lg shadow-xl w-full max-w-md max-h-[90vh] overflow-hidden flex flex-col">
            <div class="flex justify-between items-center p-6 border-b">
                <h3 class="text-xl font-bold text-gray-800">Import/Export modules</h3>
                <button onclick="closeImportExportModal()" class="text-gray-500 hover:text-gray-700 p-2">
                    <i data-feather="x" class="w-6 h-6"></i>
                </button>
            </div>

            <div class="p-6 space-y-4">
                <div class="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <h4 class="font-semibold text-blue-800 mb-2">Export to JSON</h4>
                    <p class="text-blue-700 text-sm mb-3">Export the current module configuration to a file</p>
                    <button onclick="exportModulesToJson()"
                            class="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center justify-center">
                        <i data-feather="download" class="w-4 h-4 mr-2"></i>
                        Export to JSON
                    </button>
                </div>

                <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                    <h4 class="font-semibold text-green-800 mb-2">Import from JSON</h4>
                    <p class="text-green-700 text-sm mb-3">Import configuration from a JSON file</p>
                    <button onclick="importModulesFromJson()"
                            class="w-full px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center justify-center">
                        <i data-feather="upload" class="w-4 h-4 mr-2"></i>
                        Import from JSON
                    </button>
                </div>

                <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                    <h4 class="font-semibold text-yellow-800 mb-2">Import from file</h4>
                    <p class="text-yellow-700 text-sm mb-3">If the DB has no data, config/modules.json is used as a fallback</p>
                    <div class="text-xs text-yellow-600">
                        Automatic fallback when loading from the DB fails
                    </div>
                </div>
            </div>

            <div class="flex justify-end p-6 border-t bg-gray-50">
                <button onclick="closeImportExportModal()"
                        class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                    Close
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(moduleModal);
    document.body.appendChild(rolesModal);
    document.body.appendChild(importExportModal);

    feather.replace();
}

// Functions for working with data
async function loadModulesData() {
    try {
        const token = localStorage.getItem('authToken');

        // Load the modules
        const modulesResponse = await fetch('/api/admin/modules', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        // Load the role-module associations
        const rolesResponse = await fetch('/api/admin/role-modules', {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (modulesResponse.ok && rolesResponse.ok) {
            const modules = await modulesResponse.json();
            const rolesData = await rolesResponse.json();

            displayModules(modules.modules, rolesData);
            updateStats(modules.modules, rolesData);
        } else {
            throw new Error('Error loading data');
        }
    } catch (error) {
        console.error('Error loading modules:', error);
        showNotification('Error loading module data', 'error');
    }
}

/**
 * Table rows for backend plugins, from the /api/admin/plugins response.
 *
 * Pure function: takes status rows, returns HTML. The three outcomes are
 * told apart by wording, not only by color: "Not enabled" is a decision,
 * not a failure; "Failed to start" means it is enabled but did not load or
 * initialize, and this is the only place that failure is visible without
 * reading the log.
 *
 * @param {Array<Object>} rows - Plugin status rows
 * @returns {string} HTML for the <tr> rows
 */
function renderBackendPlugins(rows) {
    if (!rows || rows.length === 0) {
        return `<tr><td colspan="6" class="px-6 py-8 text-center text-gray-500">No plugins: none are declared in config/modules.json</td></tr>`;
    }
    const sourceText = { config: 'file', env: 'environment', panel: 'panel', default: 'default' };
    return rows.map(row => {
        const decision = row.enabled
            ? `<span class="px-2 py-1 bg-green-100 text-green-800 rounded text-xs">Enabled</span>`
            : `<span class="px-2 py-1 bg-gray-100 text-gray-800 rounded text-xs">Not enabled</span>`;
        const source = `<span class="ml-2 text-xs text-gray-500">${sourceText[row.source] || row.source || ''}</span>`;
        let state;
        if (row.pending_restart) {
            // The decision has already changed but the old one is still running: a
            // plugin's routes are built at startup and never torn down. We say so
            // plainly, or the row would show "disabled" next to a plugin still running.
            const willBe = row.desired_enabled ? 'will enable' : 'will disable';
            state = `<span class="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs">${willBe} after restart</span>
                     <span class="ml-2 text-xs text-gray-500">currently ${row.initialized ? 'running' : 'not running'}</span>`;
        } else if (!row.enabled) {
            state = `<span class="text-gray-500 text-sm">—</span>`;
        } else if (row.initialized) {
            state = `<span class="px-2 py-1 bg-green-100 text-green-800 rounded text-xs">Running</span>`;
        } else {
            const why = row.loaded ? 'initialization returned a failure' : 'failed to load: no file or class';
            state = `<span class="px-2 py-1 bg-red-100 text-red-800 rounded text-xs">Failed to start</span>
                     <span class="ml-2 text-xs text-gray-500">${why}</span>`;
        }
        return `
            <tr class="hover:bg-gray-50">
                <td class="px-6 py-4"><div class="text-sm font-medium text-gray-900">${row.name || row.id}</div></td>
                <td class="px-6 py-4 whitespace-nowrap"><div class="text-sm text-gray-900 font-mono">${row.id}</div></td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-700">${row.priority}</td>
                <td class="px-6 py-4 whitespace-nowrap">${decision}${source}</td>
                <td class="px-6 py-4 whitespace-nowrap">${state}</td>
                <td class="px-6 py-4 whitespace-nowrap">${pluginSwitch(row)}</td>
            </tr>`;
    }).join('');
}

/**
 * The plugin toggle in a table row.
 *
 * Pure function. For a plugin named in PLUGINS_ENABLE/PLUGINS_DISABLE, the
 * deployment decides -- there is no button, only the reason: the server would
 * reject such a request anyway, and the button would promise what will not happen.
 *
 * @param {Object} row - Plugin status row
 * @returns {string} HTML for the cell
 */
function pluginSwitch(row) {
    if (row.decided_by_environment) {
        return `<span class="text-xs text-gray-500">decided by deployment: PLUGINS_ENABLE / PLUGINS_DISABLE</span>`;
    }
    const target = !row.desired_enabled;
    const label = target ? 'Enable' : 'Disable';
    const colour = target ? 'bg-green-600 hover:bg-green-700' : 'bg-gray-600 hover:bg-gray-700';
    const reset = row.desired_source === 'panel'
        ? `<button onclick="clearPluginOverride('${row.id}')"
                   class="ml-2 px-2 py-1 text-xs text-gray-600 underline">from file</button>`
        : '';
    return `<button onclick="setPluginEnabled('${row.id}', ${target})"
                    class="px-3 py-1 ${colour} text-white rounded text-xs">${label}</button>${reset}`;
}

/**
 * The restart hint. There is deliberately no restart button: an application
 * restarting itself on its own request behaves unpredictably with several
 * replicas and in a container, where a restart is the supervisor's job.
 *
 * @returns {string} Hint text
 */
function pluginRestartHint() {
    return 'The change takes effect after a server restart: docker compose restart app '
         + '(or restarting the service on this stand).';
}

async function setPluginEnabled(pluginId, enabled) {
    await callPluginSwitch(`/api/admin/plugins/${encodeURIComponent(pluginId)}/enabled`, 'POST',
                           JSON.stringify({ enabled }));
}

async function clearPluginOverride(pluginId) {
    await callPluginSwitch(`/api/admin/plugins/${encodeURIComponent(pluginId)}/enabled`, 'DELETE', null);
}

async function callPluginSwitch(url, method, body) {
    try {
        const token = localStorage.getItem('authToken');
        const headers = { 'Authorization': `Bearer ${token}` };
        if (body) headers['Content-Type'] = 'application/json';
        const response = await fetch(url, { method, headers, body });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            // The failure is shown as-is: the server's reason is specific --
            // the environment decides for this plugin, or it is not in config/modules.json.
            showNotification(data.detail || `Could not change the plugin's state (HTTP ${response.status})`, 'error');
            return;
        }
        showNotification(pluginRestartHint(), 'info');
        await loadBackendPlugins();
    } catch (error) {
        showNotification(`Could not change the plugin's state: ${error.message}`, 'error');
    }
}

async function loadBackendPlugins() {
    const tbody = document.getElementById('backend-plugins-table-body');
    if (!tbody) return;
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/admin/plugins', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        tbody.innerHTML = renderBackendPlugins(data.plugins);
    } catch (error) {
        console.error('Error loading plugin status:', error);
        tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-4 text-center text-red-500">Could not get plugin status: ${error.message}</td></tr>`;
    }
    feather.replace();
}

function displayModules(modules, rolesData) {
    const tbody = document.getElementById('modules-table-body');

    if (!modules || modules.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="px-6 py-8 text-center text-gray-500">
                    <i data-feather="package" class="w-12 h-12 mx-auto mb-3 text-gray-400"></i>
                    <p>No modules found</p>
                    <button onclick="showAddModuleModal()"
                            class="mt-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm">
                        Add the first module
                    </button>
                </td>
            </tr>
        `;
        feather.replace();
        return;
    }

    tbody.innerHTML = modules.map(module => {
        // Determine the roles for this module
        const moduleRoles = [];
        for (const [roleName, roleModules] of Object.entries(rolesData)) {
            if (roleModules.some(m => m.module_id === module.module_id)) {
                moduleRoles.push(roleName);
            }
        }

        const rolesText = moduleRoles.length > 0 ?
            moduleRoles.map(role => `<span class="px-2 py-1 bg-blue-100 text-blue-800 rounded text-xs">${role}</span>`).join(' ') :
            '<span class="text-gray-500 text-sm">Not assigned</span>';

        return `
            <tr class="hover:bg-gray-50">
                <td class="px-6 py-4">
                    <div class="flex items-center">
                        <div class="flex-shrink-0 h-10 w-10 bg-blue-100 rounded-lg flex items-center justify-center">
                            <i data-feather="grid" class="w-5 h-5 text-blue-600"></i>
                        </div>
                        <div class="ml-4">
                            <div class="text-sm font-medium text-gray-900">${module.name}</div>
                            <div class="text-sm text-gray-500">${module.description || 'No description'}</div>
                        </div>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <div class="text-sm text-gray-900 font-mono">${module.module_id}</div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="px-2 py-1 bg-gray-100 text-gray-800 rounded text-xs">${module.version}</span>
                </td>
                <td class="px-6 py-4">
                    <div class="flex flex-wrap gap-1">${rolesText}</div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="px-2 py-1 ${module.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'} rounded text-xs">
                        ${module.is_active ? 'Active' : 'Inactive'}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium">
                    <div class="flex space-x-2">
                        <button onclick="editModule('${module.module_id}')"
                                class="text-blue-600 hover:text-blue-900 bg-blue-100 px-3 py-1 rounded text-sm flex items-center">
                            <i data-feather="edit" class="w-3 h-3 mr-1"></i>
                            Edit
                        </button>
                        <button onclick="manageModuleRoles('${module.module_id}')"
                                class="text-purple-600 hover:text-purple-900 bg-purple-100 px-3 py-1 rounded text-sm flex items-center">
                            <i data-feather="users" class="w-3 h-3 mr-1"></i>
                            Roles
                        </button>
                        <button onclick="deleteModule('${module.module_id}')"
                                class="text-red-600 hover:text-red-900 bg-red-100 px-3 py-1 rounded text-sm flex items-center">
                            <i data-feather="trash-2" class="w-3 h-3 mr-1"></i>
                            Delete
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');

    feather.replace();
}

function updateStats(modules, rolesData) {
    const totalModules = modules.length;
    const activeModules = modules.filter(m => m.is_active).length;

    let adminModules = 0;
    let clientModules = 0;

    if (rolesData.ADMIN) adminModules = rolesData.ADMIN.length;
    if (rolesData.CLIENT) clientModules = rolesData.CLIENT.length;

    document.getElementById('total-modules').textContent = totalModules;
    document.getElementById('active-modules').textContent = activeModules;
    document.getElementById('admin-role-modules').textContent = adminModules;
    document.getElementById('client-role-modules').textContent = clientModules;
}

// Modal functions
function showAddModuleModal() {
    currentEditingModule = null;
    document.getElementById('module-modal-title').textContent = 'Add module';
    document.getElementById('module-form').reset();
    document.getElementById('module-id').disabled = false;
    document.getElementById('module-modal').classList.remove('hidden');
}

function showEditModuleModal(module) {
    currentEditingModule = module;
    document.getElementById('module-modal-title').textContent = 'Edit module';

    document.getElementById('module-id').value = module.module_id;
    document.getElementById('module-id').disabled = true;
    document.getElementById('module-name').value = module.name;
    document.getElementById('module-description').value = module.description || '';
    document.getElementById('module-js-path').value = module.js_path || '';
    document.getElementById('module-css-path').value = module.css_path || '';
    document.getElementById('module-init-function').value = module.init_function || '';
    document.getElementById('module-version').value = module.version;
    document.getElementById('module-config').value = module.config ? JSON.stringify(module.config, null, 2) : '';
    document.getElementById('module-active').checked = module.is_active;

    document.getElementById('module-modal').classList.remove('hidden');
}

function closeModuleModal() {
    document.getElementById('module-modal').classList.add('hidden');
    currentEditingModule = null;
}

async function saveModule() {
    const formData = {
        module_id: document.getElementById('module-id').value,
        name: document.getElementById('module-name').value,
        description: document.getElementById('module-description').value,
        js_path: document.getElementById('module-js-path').value,
        css_path: document.getElementById('module-css-path').value,
        init_function: document.getElementById('module-init-function').value,
        version: document.getElementById('module-version').value,
        is_active: document.getElementById('module-active').checked
    };

    // Parse the JSON configuration
    const configText = document.getElementById('module-config').value.trim();
    if (configText) {
        try {
            formData.config = JSON.parse(configText);
        } catch (e) {
            showNotification('Invalid JSON configuration format', 'error');
            return;
        }
    }

    try {
        const token = localStorage.getItem('authToken');
        const url = currentEditingModule ?
            `/api/admin/modules/${currentEditingModule.module_id}` :
            '/api/admin/modules';

        const method = currentEditingModule ? 'PUT' : 'POST';

        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(formData)
        });

        if (response.ok) {
            showNotification(
                currentEditingModule ? 'Module updated' : 'Module created',
                'success'
            );
            closeModuleModal();
            loadModulesData();
        } else {
            const error = await response.json();
            showNotification(error.detail || 'Error saving', 'error');
        }
    } catch (error) {
        showNotification('Network error: ' + error.message, 'error');
    }
}

async function editModule(moduleId) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/modules/${moduleId}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            const module = await response.json();
            showEditModuleModal(module);
        } else {
            throw new Error('Module not found');
        }
    } catch (error) {
        showNotification('Error loading module', 'error');
    }
}

async function deleteModule(moduleId) {
    if (!confirm(`Are you sure you want to delete module ${moduleId}?`)) return;

    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/modules/${moduleId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            showNotification('Module deleted', 'success');
            loadModulesData();
        } else {
            throw new Error('Error deleting');
        }
    } catch (error) {
        showNotification('Error deleting module', 'error');
    }
}

// Role management
let currentManagingModule = null;

async function manageModuleRoles(moduleId) {
    currentManagingModule = moduleId;

    try {
        const token = localStorage.getItem('authToken');
        const [moduleResponse, rolesResponse] = await Promise.all([
            fetch(`/api/admin/modules/${moduleId}`, {
                headers: { 'Authorization': `Bearer ${token}` }
            }),
            fetch('/api/admin/role-modules', {
                headers: { 'Authorization': `Bearer ${token}` }
            })
        ]);

        if (moduleResponse.ok && rolesResponse.ok) {
            const module = await moduleResponse.json();
            const rolesData = await rolesResponse.json();

            displayRolesForModule(module, rolesData);
            document.getElementById('roles-modal-title').textContent = `Roles for: ${module.name}`;
            document.getElementById('roles-modal').classList.remove('hidden');
        } else {
            throw new Error('Error loading data');
        }
    } catch (error) {
        showNotification('Error loading role data', 'error');
    }
}

function displayRolesForModule(module, rolesData) {
    const rolesList = document.getElementById('roles-list');
    const availableRoles = ['ADMIN', 'CLIENT'];

    rolesList.innerHTML = availableRoles.map(role => {
        const isAssigned = rolesData[role] &&
            rolesData[role].some(m => m.module_id === module.module_id);

        return `
            <div class="flex items-center justify-between p-3 border border-gray-200 rounded-lg">
                <div class="flex items-center">
                    <input type="checkbox" id="role-${role}" ${isAssigned ? 'checked' : ''}
                           class="w-4 h-4 text-blue-600 rounded focus:ring-blue-500">
                    <label for="role-${role}" class="ml-3 text-sm font-medium text-gray-700">
                        ${role === 'ADMIN' ? 'Administrator' : 'Client'}
                    </label>
                </div>
                <span class="text-xs text-gray-500 px-2 py-1 bg-gray-100 rounded">
                    ${role}
                </span>
            </div>
        `;
    }).join('');
}

function closeRolesModal() {
    document.getElementById('roles-modal').classList.add('hidden');
    currentManagingModule = null;
}

async function saveRoleModules() {
    if (!currentManagingModule) return;

    const selectedRoles = [];
    if (document.getElementById('role-ADMIN').checked) selectedRoles.push('ADMIN');
    if (document.getElementById('role-CLIENT').checked) selectedRoles.push('CLIENT');

    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/admin/role-modules', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                role_name: 'ADMIN', // Update separately for each role
                module_ids: selectedRoles.includes('ADMIN') ? [currentManagingModule] : []
            })
        });

        // Also update for the CLIENT role
        const clientResponse = await fetch('/api/admin/role-modules', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                role_name: 'CLIENT',
                module_ids: selectedRoles.includes('CLIENT') ? [currentManagingModule] : []
            })
        });

        if (response.ok && clientResponse.ok) {
            showNotification('Role settings saved', 'success');
            closeRolesModal();
            loadModulesData();
        } else {
            throw new Error('Error saving');
        }
    } catch (error) {
        showNotification('Error saving role settings', 'error');
    }
}

// Import/Export
function showImportExportModal() {
    document.getElementById('import-export-modal').classList.remove('hidden');
}

function closeImportExportModal() {
    document.getElementById('import-export-modal').classList.add('hidden');
}

async function exportModulesToJson() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/admin/modules/export-to-json', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            showNotification('Modules exported to a JSON file', 'success');
            closeImportExportModal();
        } else {
            throw new Error('Error exporting');
        }
    } catch (error) {
        showNotification('Error exporting modules', 'error');
    }
}

async function importModulesFromJson() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/admin/modules/import-from-json', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            showNotification('Modules imported from a JSON file', 'success');
            closeImportExportModal();
            loadModulesData();
        } else {
            throw new Error('Error importing');
        }
    } catch (error) {
        showNotification('Error importing modules', 'error');
    }
}

// URL change handler
function setupModulesEventListeners() {
    // Listen for URL changes
    window.addEventListener('popstate', function(event) {
        handleUrlChange();
    });
    setTimeout(handleUrlChange, 100);
}

function handleUrlChange() {
    const currentPath = window.location.pathname;
    if (currentPath === '/selfcare/modules/modules' || currentPath.startsWith('/selfcare/modules/')) {
        if (!isModulesRouteActive) {
            showModulesManagementSection();
        }
    } else {
        isModulesRouteActive = false;
    }
}

function setupGlobalRouter() {
    // History navigation handler
    window.addEventListener('popstate', function(event) {
        handleUrlChange();
    });

    // Link click handler
    document.addEventListener('click', function(e) {
        const link = e.target.closest('a');
        if (link && link.href && link.href.startsWith(window.location.origin + '/modules')) {
            e.preventDefault();
            window.history.pushState({}, '', link.getAttribute('href'));
            handleUrlChange();
        }
    });
}

// Initialize on load
document.addEventListener('DOMContentLoaded', function() {
    handleUrlChange();
});