// static/modules/js/integration_logs.js
class IntegrationLogsPlugin {
    constructor() {
this.name = "Integration Log";
        this.currentPage = 1;
        this.limit = 50;
        this.filters = {
            user_id: '',
            username: '',
            host: '',
            start_date: '',
            end_date: ''
        };
        this.initialized = false;
        this.contentCreated = false;
    }

    async initialize() {
        // Skip if the plugin is already initialized
        if (this.initialized) {
            console.log('Integration logs plugin already initialized');
            return true;
        }

        try {
            this.addNavigation();
            this.createContentSection();
            this.loadHostOptions();
            this.initialized = true;
            console.log('Integration logs plugin initialized successfully');
            return true;
        } catch (error) {
            console.error('Error initializing integration logs plugin:', error);
            return false;
        }
    }

    addNavigation() {
        // Skip if the nav item is already there
        if (document.querySelector('[data-target="integration_logs-section"]')) {
            return;
        }

        const mainNav = document.getElementById('main-nav');
        if (!mainNav) {
            console.error('Main navigation not found');
            return;
        }

        const navItem = document.createElement('a');
        navItem.href = '#';
        navItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600';
        navItem.setAttribute('data-target', 'integration_logs-section');
        navItem.innerHTML = `
            <i data-feather="activity" class="w-5 h-5 mr-3"></i>
            <span class="nav-text">Integration Log</span>
        `;

        const modulesNavContainer = document.getElementById('modules-nav-container');
        if (modulesNavContainer) {
            modulesNavContainer.appendChild(navItem);
        } else {
            // No modules container -- fall back to the main nav
            const uploadNav = mainNav.querySelector('[data-target="upload-section"]');
            if (uploadNav) {
                uploadNav.parentNode.insertBefore(navItem, uploadNav.nextSibling);
            }
        }

        // Sync collapsed-state styling
        this.updateNavItemForCollapsedState(navItem,
            document.getElementById('sidebar').classList.contains('sidebar-collapsed'));

        console.log('Navigation item "Integration Log" added');
    }

    createContentSection() {
        // Skip if the section already exists
        if (document.getElementById('integration_logs-section')) {
            this.contentCreated = true;
            return;
        }

        const mainContent = document.querySelector('main');
        if (!mainContent) {
            console.error('Main content not found');
            return;
        }

        const contentSection = document.createElement('div');
        contentSection.id = 'integration_logs-section';
        contentSection.className = 'content-section';
        contentSection.innerHTML = this.getContentHTML();

        mainContent.appendChild(contentSection);
        this.contentCreated = true;

        // Wire up listeners only for a section we just created
        this.setupEventListeners();
        this.setupSectionObserver();

        console.log('Content section "Integration Log" created');
    }

    getContentHTML() {
        return `
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-gray-800">Integration Log</h2>
                <div class="flex space-x-2">
                    <button onclick="window.integrationLogsPlugin.loadStats()"
                            class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center">
                        <i data-feather="bar-chart-2" class="w-4 h-4 mr-2"></i>
                        Statistics
                    </button>
                    <button onclick="window.integrationLogsPlugin.exportLogs()"
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="download" class="w-4 h-4 mr-2"></i>
                        Export
                    </button>
                    ${currentUser && currentUser.role === 'ADMIN' ? `
                    <button onclick="window.integrationLogsPlugin.cleanupLogs()"
                            class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 flex items-center">
                        <i data-feather="trash-2" class="w-4 h-4 mr-2"></i>
                        Cleanup
                    </button>
                    ` : ''}
                </div>
            </div>

            <!-- Filters -->
            <div class="bg-gray-50 rounded-lg p-4 mb-6">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">Filters</h3>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">User ID</label>
                        <input type="number" id="filter-user-id"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Username</label>
                        <input type="text" id="filter-username"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Host</label>
                        <select id="filter-host" class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <option value="">All hosts</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Limit</label>
                        <select id="filter-limit" class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <option value="50">50 records</option>
                            <option value="100">100 records</option>
                            <option value="200">200 records</option>
                        </select>
                    </div>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">Start date</label>
                        <input type="datetime-local" id="filter-start-date"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-1">End date</label>
                        <input type="datetime-local" id="filter-end-date"
                               class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                </div>
                <div class="flex justify-between">
                    <button onclick="window.integrationLogsPlugin.applyFilters()"
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="filter" class="w-4 h-4 mr-2"></i>
                        Apply filters
                    </button>
                    <button onclick="window.integrationLogsPlugin.resetFilters()"
                            class="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                        Reset
                    </button>
                </div>
            </div>

            <!-- Statistics -->
            <div id="stats-container" class="hidden mb-6">
                <!-- Dynamically loaded statistics -->
            </div>

            <!-- Log table -->
            <div class="bg-white border border-gray-200 rounded-lg overflow-hidden">
                <div id="logs-container">
                    <div class="text-center py-8">
                        <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-2"></div>
                        <p class="text-gray-500">Loading log...</p>
                    </div>
                </div>
            </div>

            <!-- Pagination -->
            <div id="pagination-container" class="mt-4 flex justify-between items-center">
                <!-- Dynamic pagination -->
            </div>
        </div>

        <!-- Detail modal -->
        <div id="log-detail-modal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden p-4">
            <div class="bg-white rounded-lg p-6 w-full max-w-4xl max-h-[90vh] overflow-y-auto">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-xl font-bold text-gray-800">Request details</h3>
                    <button onclick="window.integrationLogsPlugin.closeDetailModal()"
                            class="text-gray-500 hover:text-gray-700">
                        <i data-feather="x" class="w-6 h-6"></i>
                    </button>
                </div>
                <div id="log-detail-content">
                    <!-- Log details -->
                </div>
            </div>
        </div>
        `;
    }

    setupEventListeners() {
        // Set default date range
        const now = new Date();
        const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

        const startDateElem = document.getElementById('filter-start-date');
        const endDateElem = document.getElementById('filter-end-date');
        const limitElem = document.getElementById('filter-limit');

        if (startDateElem) startDateElem.value = weekAgo.toISOString().slice(0, 16);
        if (endDateElem) endDateElem.value = now.toISOString().slice(0, 16);
        if (limitElem) {
            limitElem.value = '50';
            this.limit = 50;
        }

        console.log('Integration logs event listeners setup');
    }

    setupSectionObserver() {
        const section = document.getElementById('integration_logs-section');
        if (!section) {
            console.error('Integration logs section not found for observer');
            return;
        }

        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                    if (section.classList.contains('active')) {
                        console.log('Integration logs section activated, loading logs...');
                        this.loadLogs();
                    }
                }
            });
        });

        observer.observe(section, {
            attributes: true,
            attributeFilter: ['class']
        });

        console.log('Integration logs section observer setup');
    }

    async loadLogs(page = 1) {
        console.log(`Loading integration logs, page: ${page}`);
        this.currentPage = page;
        const offset = (page - 1) * this.limit;

        try {
            const token = localStorage.getItem('authToken');
            const params = new URLSearchParams({
                limit: this.limit.toString(),
                offset: offset.toString(),
                ...this.filters
            });

            // Drop empty params
            for (const [key, value] of Object.entries(this.filters)) {
                if (value) {
                    params.append(key, value);
                }
            }

            const response = await fetch(`/api/integration-logs?${params}`, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                this.displayLogs(data);
                console.log(`Integration logs loaded successfully, total: ${data.total_count}`);
            } else {
                console.error('Error loading integration logs:', response.status);
                this.displayError('Error loading log');
            }
        } catch (error) {
            console.error('Error loading integration logs:', error);
            this.displayError('Network error');
        }
    }

    displayLogs(data) {
        const container = document.getElementById('logs-container');
        if (!container) {
            console.error('Logs container not found');
            return;
        }

        if (!data.success || !data.logs || data.logs.length === 0) {
            container.innerHTML = `
                <div class="text-center py-8 text-gray-500">
                    <i data-feather="inbox" class="w-12 h-12 mx-auto mb-2"></i>
                    <p>No records found</p>
                </div>
            `;
            if (typeof feather !== 'undefined') {
                feather.replace();
            }
            return;
        }

        container.innerHTML = `
            <table class="responsive-table min-w-full divide-y divide-gray-200">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Date</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Host</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Method</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Duration</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-200">
                    ${data.logs.map(log => `
                        <tr class="hover:bg-gray-50">
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                ${new Date(log.created_at).toLocaleString()}
                            </td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                <div>${log.username}</div>
                                <div class="text-gray-500 text-xs">ID: ${log.user_id}</div>
                            </td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">${log.host}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">${log.method}</td>
                            <td class="px-6 py-4 whitespace-nowrap">
                                <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                                    log.status_code >= 400 ? 'bg-red-100 text-red-800' :
                                    log.status_code >= 200 ? 'bg-green-100 text-green-800' :
                                    'bg-yellow-100 text-yellow-800'
                                }">
                                    ${log.status_code || 'N/A'}
                                </span>
                            </td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                ${log.duration_ms ? `${log.duration_ms}ms` : 'N/A'}
                            </td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm">
                                <button onclick="window.integrationLogsPlugin.showLogDetail(${log.id})"
                                        class="text-blue-600 hover:text-blue-900 bg-blue-100 px-3 py-1 rounded text-sm flex items-center">
                                    <i data-feather="eye" class="w-3 h-3 mr-1"></i>
                                    Details
                                </button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;

        this.updatePagination(data.total_count);
        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    }

    updatePagination(totalCount) {
        const container = document.getElementById('pagination-container');
        if (!container) return;

        const totalPages = Math.ceil(totalCount / this.limit);

        if (totalPages <= 1) {
            container.innerHTML = `
                <div class="text-sm text-gray-700">
                    Showing ${totalCount} records
                </div>
            `;
            return;
        }

        container.innerHTML = `
            <div class="text-sm text-gray-700">
                Showing ${((this.currentPage - 1) * this.limit) + 1}-${Math.min(this.currentPage * this.limit, totalCount)} of ${totalCount} records
            </div>
            <div class="flex space-x-2">
                <button onclick="window.integrationLogsPlugin.loadLogs(${this.currentPage - 1})"
                        ${this.currentPage <= 1 ? 'disabled' : ''}
                        class="px-3 py-1 border border-gray-300 rounded text-sm ${this.currentPage <= 1 ? 'opacity-50 cursor-not-allowed' : 'hover:bg-gray-50'}">
                    Back
                </button>
                <span class="px-3 py-1 text-sm text-gray-700">
                    Page ${this.currentPage} of ${totalPages}
                </span>
                <button onclick="window.integrationLogsPlugin.loadLogs(${this.currentPage + 1})"
                        ${this.currentPage >= totalPages ? 'disabled' : ''}
                        class="px-3 py-1 border border-gray-300 rounded text-sm ${this.currentPage >= totalPages ? 'opacity-50 cursor-not-allowed' : 'hover:bg-gray-50'}">
                    Next
                </button>
            </div>
        `;
    }

    async applyFilters() {
        console.log('Applying filters...');
        this.filters = {
            user_id: document.getElementById('filter-user-id')?.value || '',
            username: document.getElementById('filter-username')?.value || '',
            host: document.getElementById('filter-host')?.value || '',
            start_date: document.getElementById('filter-start-date')?.value || '',
            end_date: document.getElementById('filter-end-date')?.value || ''
        };

        const limitElem = document.getElementById('filter-limit');
        if (limitElem) {
            this.limit = parseInt(limitElem.value) || 50;
        }

        this.currentPage = 1;

        await this.loadLogs();
    }

    resetFilters() {
        console.log('Resetting filters...');
        const useridElem = document.getElementById('filter-user-id');
        const usernameElem = document.getElementById('filter-username');
        const hostElem = document.getElementById('filter-host');
        const startDateElem = document.getElementById('filter-start-date');
        const endDateElem = document.getElementById('filter-end-date');
        const limitElem = document.getElementById('filter-limit');

        if (useridElem) useridElem.value = '';
        if (usernameElem) usernameElem.value = '';
        if (hostElem) hostElem.value = '';

        const now = new Date();
        const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

        if (startDateElem) startDateElem.value = weekAgo.toISOString().slice(0, 16);
        if (endDateElem) endDateElem.value = now.toISOString().slice(0, 16);
        if (limitElem) limitElem.value = '50';

        this.filters = {};
        this.limit = 50;
        this.currentPage = 1;

        this.loadLogs();
    }

    async showLogDetail(logId) {
        console.log(`Showing log detail for ID: ${logId}`);
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch(`/api/integration-logs/${logId}`, {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    this.displayLogDetail(data.log);
                } else {
                    this.displayError(data.error);
                }
            } else {
                this.displayError('Error loading details');
            }
        } catch (error) {
            console.error('Error loading log detail:', error);
            this.displayError('Network error');
        }
    }

    displayLogDetail(log) {
        const modal = document.getElementById('log-detail-modal');
        const content = document.getElementById('log-detail-content');

        if (!modal || !content) {
            console.error('Log detail modal elements not found');
            return;
        }

        content.innerHTML = `
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                <div>
                    <strong>ID:</strong> ${log.id}
                </div>
                <div>
                    <strong>Date:</strong> ${new Date(log.created_at).toLocaleString()}
                </div>
                <div>
                    <strong>User:</strong> ${log.username} (ID: ${log.user_id})
                </div>
                <div>
                    <strong>Host:</strong> ${log.host}
                </div>
                <div>
                    <strong>Method:</strong> ${log.method}
                </div>
                <div>
                    <strong>Endpoint:</strong> ${log.endpoint}
                </div>
                <div>
                    <strong>Status:</strong>
                    <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                        log.status_code >= 400 ? 'bg-red-100 text-red-800' :
                        log.status_code >= 200 ? 'bg-green-100 text-green-800' :
                        'bg-yellow-100 text-yellow-800'
                    }">
                        ${log.status_code || 'N/A'}
                    </span>
                </div>
                <div>
                    <strong>Duration:</strong> ${log.duration_ms ? `${log.duration_ms}ms` : 'N/A'}
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div>
                    <h4 class="font-semibold mb-2">Request body:</h4>
                    <pre class="bg-gray-100 p-3 rounded text-sm overflow-auto max-h-60">${this.formatJson(log.request_body)}</pre>
                </div>
                <div>
                    <h4 class="font-semibold mb-2">Response body:</h4>
                    <pre class="bg-gray-100 p-3 rounded text-sm overflow-auto max-h-60">${this.formatJson(log.response_body)}</pre>
                </div>
            </div>
        `;

        modal.classList.remove('hidden');
        if (typeof feather !== 'undefined') {
            feather.replace();
        }
    }

    formatJson(jsonString) {
        if (!jsonString) return 'No data';

        try {
            const parsed = JSON.parse(jsonString);
            return JSON.stringify(parsed, null, 2);
        } catch {
            return jsonString;
        }
    }

    closeDetailModal() {
        const modal = document.getElementById('log-detail-modal');
        if (modal) {
            modal.classList.add('hidden');
        }
    }

    async loadHostOptions() {
        // The host list comes from the log, not hardcoded markup. It used to
        // list four integrations of one specific application -- elsewhere the
        // filter offered hosts that never show up in the log, and missed ones
        // that do. The framework does not know other integrations' names, and should not.
        const select = document.getElementById('filter-host');
        if (!select) return;
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch('/api/integration-logs/stats?days=30', {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            if (!response.ok) return;
            const data = await response.json();
            const hosts = [...new Set((data.host_stats || []).map(stat => stat.host).filter(Boolean))].sort();
            const chosen = select.value;
            for (const host of hosts) {
                const option = document.createElement('option');
                option.value = host;
                option.textContent = host;
                select.appendChild(option);
            }
            select.value = chosen;
        } catch (error) {
            // A filter with no host list still means "all hosts" -- the section still works.
            console.warn('Could not read the hosts for the filter:', error);
        }
    }

    async loadStats() {
        console.log('Loading statistics...');
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch('/api/integration-logs/stats?days=7', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                this.displayStats(data);
            } else {
                this.displayError('Error loading statistics');
            }
        } catch (error) {
            console.error('Error loading stats:', error);
            this.displayError('Network error');
        }
    }

    displayStats(data) {
        const container = document.getElementById('stats-container');
        if (!container) return;

        if (!data.success) {
            container.innerHTML = `<div class="text-red-600">${data.error}</div>`;
            container.classList.remove('hidden');
            return;
        }

        container.innerHTML = `
            <div class="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <h3 class="text-lg font-semibold text-blue-800 mb-4">Statistics for the last ${data.period_days} days</h3>

                <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                    <div class="text-center">
                        <div class="text-2xl font-bold text-blue-600">${data.total_requests}</div>
                        <div class="text-sm text-blue-800">Total requests</div>
                    </div>
                    <div class="text-center">
                        <div class="text-2xl font-bold text-green-600">${data.overall_avg_duration.toFixed(0)}ms</div>
                        <div class="text-sm text-green-800">Average time</div>
                    </div>
                    <div class="text-center">
                        <div class="text-2xl font-bold text-red-600">${data.total_errors}</div>
                        <div class="text-sm text-red-800">Errors</div>
                    </div>
                    <div class="text-center">
                        <div class="text-2xl font-bold ${data.error_rate > 5 ? 'text-red-600' : 'text-yellow-600'}">${data.error_rate.toFixed(1)}%</div>
Error rate
                    </div>
                </div>

                <h4 class="font-semibold text-blue-800 mb-2">Statistics by host:</h4>
                <div class="space-y-2">
                    ${(data.host_stats || []).map(stat => `
                        <div class="flex justify-between items-center bg-white p-2 rounded border">
                            <div>
                                <span class="font-medium">${stat.host}</span>
                                <span class="text-gray-500 text-sm ml-2">(${stat.method})</span>
                            </div>
                            <div class="flex space-x-4 text-sm">
                                <span>Requests: ${stat.request_count}</span>
                                <span>Time: ${stat.avg_duration ? stat.avg_duration.toFixed(0) + 'ms' : 'N/A'}</span>
                                <span class="${stat.error_count > 0 ? 'text-red-600' : 'text-green-600'}">
                                    Errors: ${stat.error_count}
                                </span>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;

        container.classList.remove('hidden');
    }

    async exportLogs() {
        console.log('Exporting logs...');
        this.displayMessage('Export is not implemented yet', 'info');
        // Export implementation to be added later
    }

    async cleanupLogs() {
        if (!confirm('Are you sure you want to clear logs older than 30 days?')) {
            return;
        }

        console.log('Cleaning up logs...');
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch('/api/integration-logs/cleanup', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                if (data.success) {
                    this.displayMessage(data.message, 'success');
                    this.loadLogs();
                } else {
                    this.displayError(data.error);
                }
            } else {
                this.displayError('Error clearing logs');
            }
        } catch (error) {
            console.error('Error cleaning up logs:', error);
            this.displayError('Network error');
        }
    }

    displayError(message) {
        if (typeof showNotification === 'function') {
            showNotification(message, 'error');
        } else {
            alert(`Error: ${message}`);
        }
    }

    displayMessage(message, type = 'info') {
        if (typeof showNotification === 'function') {
            showNotification(message, type);
        } else {
            alert(message);
        }
    }

    updateNavItemForCollapsedState(navItem, isCollapsed) {
        const navText = navItem.querySelector('.nav-text');
        const icon = navItem.querySelector('i');

        if (isCollapsed) {
            if (navText) navText.style.display = 'none';
            navItem.classList.add('justify-center', 'px-3');
            if (icon) icon.classList.remove('mr-3');
        } else {
            if (navText) navText.style.display = 'inline';
            navItem.classList.remove('justify-center', 'px-3');
            if (icon) icon.classList.add('mr-3');
        }
    }
}

// Initialize the plugin only if it is not already initialized
if (!window.integrationLogsPlugin) {
    window.integrationLogsPlugin = new IntegrationLogsPlugin();

    function initIntegrationLogs() {
        return window.integrationLogsPlugin.initialize();
    }
} else {
    console.log('Integration logs plugin already exists');

    function initIntegrationLogs() {
        return Promise.resolve(true);
    }
}