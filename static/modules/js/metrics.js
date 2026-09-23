// Where to load our own libraries from, resolved at module level.
// document.currentScript is only readable while the script is executing: inside
// an async function it is already null, and previously the fallback path ran
// instead of this one.
// The path is taken from the file itself rather than hardcoded, because the
// shell's mount point is an application setting (KeepupSettings.shell_mount).
const METRICS_ASSET_BASE = (function () {
    const src = document.currentScript && document.currentScript.src;
    return src ? src.substring(0, src.lastIndexOf('/')) : '';
})();

if (typeof window.metricsGlobals === 'undefined') {
    window.metricsGlobals = {
        cpuChart: null,
        ramChart: null,
        metricsAutoRefreshInterval: null,
        isAutoRefreshEnabled: false,
        isMetricsInitialized: false,
        chartJsLoaded: false
    };
}

async function renderMetrics() {
    // Guard against re-initialization
    if (window.metricsGlobals.isMetricsInitialized) {
        console.log('Metrics module already initialized');
        return;
    }

    console.log('Initializing metrics module...');

    // First load Chart.js and the adapter
    try {
        await loadChartJsDependencies();
    } catch (error) {
        console.error('Failed to load Chart.js:', error);
        showNotification('Failed to load chart library', 'error');
        return;
    }

    addMetricsStyles();
    addMetricsNavigation();
    addMetricsSection();
    setupMetricsPolling();

    window.metricsGlobals.isMetricsInitialized = true;
    console.log('Metrics module initialized');
}

async function loadChartJsDependencies() {
    if (window.metricsGlobals.chartJsLoaded) {
        console.log('Chart.js already loaded');
        return;
    }

    if (typeof Chart !== 'undefined') {
        console.log('Chart.js already available globally');
        window.metricsGlobals.chartJsLoaded = true;
        return;
    }

    console.log('Loading Chart.js and the adapter...');

    // Both files ship with the package next to this section: the section
    // belongs to the framework and cannot rely on an application's static
    // assets -- there is more than one application.
    await loadScript(`${METRICS_ASSET_BASE}/chart.js`);
    await loadScript(`${METRICS_ASSET_BASE}/chartjs-adapter-date-fns.bundle.min.js`);

    window.metricsGlobals.chartJsLoaded = true;
    console.log('Chart.js and the adapter loaded successfully');
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        // Check whether the script is already loaded
        if (document.querySelector(`script[src="${src}"]`)) {
            console.log(`Script already loaded: ${src}`);
            resolve();
            return;
        }

        const script = document.createElement('script');
        script.src = src;
        script.onload = () => {
            console.log(`Script loaded: ${src}`);
            resolve();
        };
        script.onerror = () => {
            console.error(`Failed to load script: ${src}`);
            reject(new Error(`Could not load script: ${src}`));
        };
        document.head.appendChild(script);
    });
}

function addMetricsNavigation() {
    const mainNav = document.getElementById('main-nav');
    if (!mainNav) return;

    // Check whether the nav item is already added
    if (document.querySelector('[data-target="metrics-section"]')) {
        return;
    }

    const metricsNavItem = document.createElement('a');
    metricsNavItem.href = '#';
    metricsNavItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600 admin-only hidden';
    metricsNavItem.setAttribute('data-target', 'metrics-section');
    metricsNavItem.innerHTML = `
        <i data-feather="bar-chart-2" class="w-5 h-5 mr-3"></i>
        <span class="nav-text">System Metrics</span>
    `;

    metricsNavItem.addEventListener('click', function(e) {
        e.preventDefault();
        handleMetricsNavigation.call(this, e);
    });

    const modulesNavContainer = document.getElementById('modules-nav-container');
    if (modulesNavContainer) {
        modulesNavContainer.appendChild(metricsNavItem);
    }

    // Show only for admins
    if (currentUser && currentUser.role === ROLE_ADMIN) {
        metricsNavItem.classList.remove('hidden');
    }

    feather.replace();
}

/**
 * Metrics navigation handler
 */
function handleMetricsNavigation(e) {
    e.preventDefault();

    if (!checkAuth()) return;
    if (currentUser.role !== ROLE_ADMIN) {
        showNotification('Access denied. Administrator rights are required.', 'error');
        return;
    }

    const navItems = document.querySelectorAll('.nav-item');

    navItems.forEach(navItem => {
        navItem.classList.remove('text-white', 'bg-blue-800');
        navItem.classList.add('text-blue-100', 'hover:bg-blue-600');
    });

    this.classList.remove('text-blue-100', 'hover:bg-blue-600');
    this.classList.add('text-white', 'bg-blue-800');

    const contentSections = document.querySelectorAll('.content-section');
    contentSections.forEach(section => {
        section.classList.remove('active');
    });

    const targetId = this.dataset.target;
    const targetSection = document.getElementById(targetId);

    if (targetSection) {
        targetSection.classList.add('active');
        document.getElementById('current-section-title').textContent = 'System Metrics';

        // Load data when switching to the section
        loadMetricsData();
    }
}

// Add the metrics section
function addMetricsSection() {
    // Check whether the section already exists
    if (document.getElementById('metrics-section')) {
        return;
    }

    const metricsSection = document.createElement('div');
    metricsSection.id = 'metrics-section';
    metricsSection.className = 'content-section';

    metricsSection.innerHTML = `
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-gray-800">System Monitoring</h2>
                <div class="flex space-x-2">
                    <button id="refreshMetricsBtn" onclick="loadMetricsData()"
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                        Refresh
                    </button>
                    <button id="autoRefreshToggle" onclick="toggleAutoRefresh()"
                            class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center">
                        <i data-feather="play" class="w-4 h-4 mr-2"></i>
                        Auto-refresh
                    </button>
                </div>
            </div>

            <!-- Instance status -->
            <div class="mb-6">
                <h3 class="text-lg font-semibold mb-4">Instance Status</h3>
                <div id="instancesStatus" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    <div class="text-center py-4">
                        <i data-feather="loader" class="w-6 h-6 animate-spin mx-auto mb-2 text-gray-400"></i>
                        <p class="text-gray-500">Loading instance status...</p>
                    </div>
                </div>
            </div>

            <!-- Metric charts -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
                <!-- CPU chart -->
                <div class="bg-gray-50 rounded-lg p-4">
                    <div class="flex justify-between items-center mb-4">
                        <h3 class="text-lg font-semibold text-gray-800">CPU Load (%)</h3>
                        <div class="flex space-x-2">
                            <select id="cpuTimeRange" onchange="updateMetricsCharts()"
                                    class="text-sm border rounded px-2 py-1">
                                <option value="1">1 hour</option>
                                <option value="6">6 hours</option>
                                <option value="24" selected>24 hours</option>
                                <option value="168">7 days</option>
                            </select>
                        </div>
                    </div>
                    <div id="cpuChartContainer" class="h-64">
                        <canvas id="cpuChart"></canvas>
                    </div>
                </div>

                <!-- RAM chart -->
                <div class="bg-gray-50 rounded-lg p-4">
                    <div class="flex justify-between items-center mb-4">
                        <h3 class="text-lg font-semibold text-gray-800">RAM Usage (MB)</h3>
                        <div class="flex space-x-2">
                            <select id="ramTimeRange" onchange="updateMetricsCharts()"
                                    class="text-sm border rounded px-2 py-1">
                                <option value="1">1 hour</option>
                                <option value="6">6 hours</option>
                                <option value="24" selected>24 hours</option>
                                <option value="168">7 days</option>
                            </select>
                        </div>
                    </div>
                    <div id="ramChartContainer" class="h-64">
                        <canvas id="ramChart"></canvas>
                    </div>
                </div>
            </div>

            <!-- Detailed metrics table -->
            <div class="bg-white rounded-lg shadow-sm overflow-hidden">
                <div class="px-6 py-4 border-b border-gray-200">
                    <h3 class="text-lg font-semibold text-gray-800">Detailed Metrics</h3>
                </div>
                <div class="overflow-x-auto">
                    <table class="responsive-table min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Instance
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    CPU (%)
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    RAM (MB)
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Updated At
                                </th>
                                <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    Status
                                </th>
                            </tr>
                        </thead>
                        <tbody id="metricsTableBody" class="bg-white divide-y divide-gray-200">
                            <tr>
                                <td colspan="5" class="px-6 py-4 text-center text-gray-500">
                                    Loading metrics...
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    const mainContent = document.querySelector('main');
    if (mainContent) {
        mainContent.appendChild(metricsSection);
    }

    // Initialize the charts
    initializeMetricsCharts();
}

// Global variables for the charts
let cpuChart = null;
let ramChart = null;
let metricsAutoRefreshInterval = null;
let isAutoRefreshEnabled = false;

// Initialize the charts
function initializeMetricsCharts() {
    // Check whether the charts are already initialized
    if (window.metricsGlobals.cpuChart || window.metricsGlobals.ramChart) {
        console.log('Charts already initialized');
        return;
    }

    // Check whether Chart.js is loaded
    if (typeof Chart === 'undefined') {
        console.error('Chart.js not loaded');
        showNotification('Chart library not loaded', 'error');
        return;
    }

    const cpuCtx = document.getElementById('cpuChart')?.getContext('2d');
    const ramCtx = document.getElementById('ramChart')?.getContext('2d');

    if (!cpuCtx || !ramCtx) {
        console.warn('Canvas elements for the charts not found');
        return;
    }

    try {
        // CPU chart
        window.metricsGlobals.cpuChart = new Chart(cpuCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: []
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: {
                            display: true,
                            text: 'Usage percentage (%)'
                        }
                    },
                    x: {
                        type: 'time',
                        time: {
                            unit: 'minute',
                            displayFormats: {
                                minute: 'HH:mm',
                                hour: 'HH:mm'
                            }
                        },
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false
                    }
                }
            }
        });

        // RAM chart
        window.metricsGlobals.ramChart = new Chart(ramCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: []
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Memory (MB)'
                        }
                    },
                    x: {
                        type: 'time',
                        time: {
                            unit: 'minute',
                            displayFormats: {
                                minute: 'HH:mm',
                                hour: 'HH:mm'
                            }
                        },
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false
                    }
                }
            }
        });

        console.log('Metric charts initialized successfully');
    } catch (error) {
        console.error('Failed to initialize charts:', error);
        showNotification('Failed to create charts', 'error');
    }
}

// Load metrics data
async function loadMetricsData() {
    if (currentUser.role !== ROLE_ADMIN) return;

    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/admin/metrics/system', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const metricsData = await response.json();
            updateMetricsUI(metricsData);
        } else {
            throw new Error('Failed to load metrics');
        }
    } catch (error) {
        console.error('Failed to load metrics:', error);
        showNotification('Failed to load system metrics', 'error');
    }
}

// Load historical data for the charts
async function loadHistoricalMetrics() {
    if (currentUser.role !== ROLE_ADMIN) return;

    try {
        const cpuTimeRange = document.getElementById('cpuTimeRange').value;
        const ramTimeRange = document.getElementById('ramTimeRange').value;

        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/metrics/history?cpu_hours=${cpuTimeRange}&ram_hours=${ramTimeRange}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const historicalData = await response.json();
            updateMetricsCharts(historicalData);
        }
    } catch (error) {
        console.error('Failed to load historical metrics:', error);
    }
}

// Update the metrics UI
function updateMetricsUI(metricsData) {
    updateInstancesStatus(metricsData.instances);
    updateMetricsTable(metricsData.instances);
    updateMetricsCharts(metricsData.historical);
}

// Update instance status
function updateInstancesStatus(instances) {
    const container = document.getElementById('instancesStatus');
    if (!container) return;

    if (!instances || instances.length === 0) {
        container.innerHTML = `
            <div class="col-span-full text-center py-4 text-gray-500">
                <i data-feather="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>
                <p>No instance data</p>
            </div>
        `;
        feather.replace();
        return;
    }

    container.innerHTML = instances.map(instance => {
        const statusClass = instance.is_online ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800';
        const statusText = instance.is_online ? 'Online' : 'Offline';

        const cpuUsage = instance.current_metrics?.cpu_percent || 0;
        const ramUsage = instance.current_metrics?.ram_mb || 0;

        const cpuClass = cpuUsage > 80 ? 'text-red-600' : cpuUsage > 60 ? 'text-yellow-600' : 'text-green-600';
        const ramClass = ramUsage > 800 ? 'text-red-600' : ramUsage > 500 ? 'text-yellow-600' : 'text-green-600';

        return `
            <div class="bg-white border rounded-lg p-4 hover:shadow-md transition-shadow">
                <div class="flex justify-between items-start mb-3">
                    <h4 class="font-semibold text-gray-800">${instance.instance_id}</h4>
                    <span class="px-2 py-1 text-xs rounded-full ${statusClass}">
                        ${statusText}
                    </span>
                </div>

                <div class="space-y-2 text-sm">
                    <div class="flex justify-between">
                        <span class="text-gray-600">CPU:</span>
                        <span class="font-medium ${cpuClass}">${cpuUsage.toFixed(1)}%</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-gray-600">RAM:</span>
                        <span class="font-medium ${ramClass}">${ramUsage.toFixed(0)} MB</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-gray-600">Updated:</span>
                        <span class="text-gray-500 text-xs">${formatRelativeTime(instance.last_update)}</span>
                    </div>
                </div>

                ${instance.is_online ? `
                    <div class="mt-3 pt-3 border-t border-gray-200">
                        <div class="flex space-x-2">
                            <button onclick="restartInstance('${instance.instance_id}')"
                                    class="flex-1 px-2 py-1 bg-blue-100 text-blue-700 text-xs rounded hover:bg-blue-200">
                                Restart
                            </button>
                            <button onclick="showInstanceDetails('${instance.instance_id}')"
                                    class="flex-1 px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded hover:bg-gray-200">
                                Details
                            </button>
                        </div>
                    </div>
                ` : ''}
            </div>
        `;
    }).join('');

    feather.replace();
}

// Update the metrics table
function updateMetricsTable(instances) {
    const tbody = document.getElementById('metricsTableBody');
    if (!tbody) return;

    if (!instances || instances.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="px-6 py-4 text-center text-gray-500">
                    No metrics data
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = instances.map(instance => {
        const cpuUsage = instance.current_metrics?.cpu_percent || 0;
        const ramUsage = instance.current_metrics?.ram_mb || 0;
        const lastUpdate = instance.last_update ? new Date(instance.last_update).toLocaleString() : 'Unknown';

        const statusClass = instance.is_online ? 'text-green-600' : 'text-red-600';
        const statusText = instance.is_online ? 'Active' : 'Inactive';

        const cpuClass = cpuUsage > 80 ? 'text-red-600 font-semibold' : cpuUsage > 60 ? 'text-yellow-600' : 'text-green-600';
        const ramClass = ramUsage > 800 ? 'text-red-600 font-semibold' : ramUsage > 500 ? 'text-yellow-600' : 'text-green-600';

        return `
            <tr class="hover:bg-gray-50">
                <td class="px-6 py-4 whitespace-nowrap">
                    <div class="text-sm font-medium text-gray-900">${instance.instance_id}</div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="text-sm ${cpuClass}">${cpuUsage.toFixed(1)}%</span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="text-sm ${ramClass}">${ramUsage.toFixed(0)} MB</span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    ${lastUpdate}
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="text-sm ${statusClass}">${statusText}</span>
                </td>
            </tr>
        `;
    }).join('');
}

// Update the charts
function updateMetricsCharts(historicalData) {
    if (!historicalData || !cpuChart || !ramChart) return;

    // Update the CPU chart
    updateChartData(cpuChart, historicalData.cpu, 'CPU Usage (%)');

    // Update the RAM chart
    updateChartData(ramChart, historicalData.ram, 'RAM Usage (MB)');
}

// Update chart data
function updateChartData(chart, data, labelPrefix) {
    if (!data || !chart) return;

    const datasets = [];
    const instanceColors = {
        'instance-1': 'rgb(59, 130, 246)',
        'instance-2': 'rgb(16, 185, 129)',
        'instance-3': 'rgb(245, 158, 11)'
    };

    // Group data by instance
    const instances = [...new Set(data.map(item => item.instance_id))];

    instances.forEach(instanceId => {
        const instanceData = data
            .filter(item => item.instance_id === instanceId)
            .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

        const color = instanceColors[instanceId] || generateRandomColor();

        datasets.push({
            label: `${labelPrefix} - ${instanceId}`,
            data: instanceData.map(item => ({
                x: new Date(item.timestamp),
                y: item.metric_value
            })),
            borderColor: color,
            backgroundColor: color + '20',
            tension: 0.4,
            fill: false
        });
    });

    chart.data.datasets = datasets;
    chart.update();
}

// Generate a random color
function generateRandomColor() {
    const r = Math.floor(Math.random() * 255);
    const g = Math.floor(Math.random() * 255);
    const b = Math.floor(Math.random() * 255);
    return `rgb(${r}, ${g}, ${b})`;
}

// Format relative time
function formatRelativeTime(timestamp) {
    if (!timestamp) return 'Unknown';

    const now = new Date();
    const time = new Date(timestamp);
    const diffMs = now - time;
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} min ago`;

    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours} h ago`;

    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays} d ago`;
}

// Toggle auto-refresh
function toggleAutoRefresh() {
    const button = document.getElementById('autoRefreshToggle');

    if (window.metricsGlobals.isAutoRefreshEnabled) {
        // Turn off auto-refresh
        clearInterval(window.metricsGlobals.metricsAutoRefreshInterval);
        window.metricsGlobals.isAutoRefreshEnabled = false;
        button.innerHTML = '<i data-feather="play" class="w-4 h-4 mr-2"></i>Auto-refresh';
        button.className = 'px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center';
        showNotification('Auto-refresh disabled', 'info');
    } else {
        // Turn on auto-refresh
        window.metricsGlobals.isAutoRefreshEnabled = true;
        window.metricsGlobals.metricsAutoRefreshInterval = setInterval(loadMetricsData, 30000);
        button.innerHTML = '<i data-feather="pause" class="w-4 h-4 mr-2"></i>Stop';
        button.className = 'px-4 py-2 bg-yellow-600 text-white rounded-lg hover:bg-yellow-700 flex items-center';
        showNotification('Auto-refresh enabled (30 sec)', 'success');
        loadMetricsData();
    }

    feather.replace();
}

// Restart instance
async function restartInstance(instanceId) {
    if (!confirm(`Are you sure you want to restart instance ${instanceId}?`)) return;

    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/instances/${instanceId}/restart`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            showNotification(`Instance ${instanceId} is restarting...`, 'success');
            // Refresh data after 5 seconds
            setTimeout(loadMetricsData, 5000);
        } else {
            throw new Error('Restart failed');
        }
    } catch (error) {
        console.error('Failed to restart instance:', error);
        showNotification('Failed to restart instance', 'error');
    }
}

// Show instance details
async function showInstanceDetails(instanceId) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/instances/${instanceId}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const instanceDetails = await response.json();
            showInstanceDetailsModal(instanceDetails);
        } else {
            throw new Error('Failed to load details');
        }
    } catch (error) {
        console.error('Failed to load instance details:', error);
        showNotification('Failed to load instance details', 'error');
    }
}

// Modal window with instance details
function showInstanceDetailsModal(instance) {
    const modal = document.createElement('div');
    modal.id = 'instanceDetailsModal';
    modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4';

    modal.innerHTML = `
        <div class="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div class="flex justify-between items-center p-6 border-b">
                <h3 class="text-xl font-bold text-gray-800">Instance Details: ${instance.instance_id}</h3>
                <button onclick="closeInstanceDetailsModal()" class="text-gray-500 hover:text-gray-700 p-2">
                    <i data-feather="x" class="w-6 h-6"></i>
                </button>
            </div>

            <div class="p-6">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
                    <div class="bg-gray-50 rounded-lg p-4">
                        <h4 class="font-semibold text-gray-700 mb-3">System Metrics</h4>
                        <div class="space-y-2 text-sm">
                            <div class="flex justify-between">
                                <span>CPU Usage:</span>
                                <span class="font-medium">${instance.current_metrics?.cpu_percent?.toFixed(1) || 0}%</span>
                            </div>
                            <div class="flex justify-between">
                                <span>RAM Usage:</span>
                                <span class="font-medium">${instance.current_metrics?.ram_mb?.toFixed(0) || 0} MB</span>
                            </div>
                            <div class="flex justify-between">
                                <span>Disk Space:</span>
                                <span class="font-medium">${instance.current_metrics?.disk_usage?.toFixed(1) || 0}%</span>
                            </div>
                        </div>
                    </div>

                    <div class="bg-gray-50 rounded-lg p-4">
                        <h4 class="font-semibold text-gray-700 mb-3">Status</h4>
                        <div class="space-y-2 text-sm">
                            <div class="flex justify-between">
                                <span>Status:</span>
                                <span class="font-medium ${instance.is_online ? 'text-green-600' : 'text-red-600'}">
                                    ${instance.is_online ? 'Online' : 'Offline'}
                                </span>
                            </div>
                            <div class="flex justify-between">
                                <span>Uptime:</span>
                                <span class="font-medium">${instance.uptime || 'Unknown'}</span>
                            </div>
                            <div class="flex justify-between">
                                <span>Last Updated:</span>
                                <span class="font-medium">${instance.last_update ? new Date(instance.last_update).toLocaleString() : 'Unknown'}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="bg-gray-50 rounded-lg p-4">
                    <h4 class="font-semibold text-gray-700 mb-3">Recent Events</h4>
                    <div class="text-sm text-gray-600">
                        ${instance.recent_events && instance.recent_events.length > 0 ?
                            instance.recent_events.map(event => `
                                <div class="flex justify-between py-1 border-b border-gray-200 last:border-b-0">
                                    <span>${event.message}</span>
                                    <span class="text-gray-500">${new Date(event.timestamp).toLocaleString()}</span>
                                </div>
                            `).join('') :
                            '<p class="text-gray-500 text-center py-2">No events</p>'
                        }
                    </div>
                </div>
            </div>

            <div class="flex justify-end p-6 border-t bg-gray-50">
                <button onclick="closeInstanceDetailsModal()" class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                    Close
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    feather.replace();
}

// Close the details modal
function closeInstanceDetailsModal() {
    const modal = document.getElementById('instanceDetailsModal');
    if (modal) {
        modal.remove();
    }
}

// Set up periodic polling
function setupMetricsPolling() {
    // Automatically refresh data every 60 seconds while the section is active
    setInterval(() => {
        const metricsSection = document.getElementById('metrics-section');
        if (metricsSection && metricsSection.classList.contains('active') && isAutoRefreshEnabled) {
            loadMetricsData();
        }
    }, 60000);
}

/**
 * Update charts when the time range changes
 */
function updateMetricsCharts(historicalData) {
    if (!historicalData || !window.metricsGlobals.cpuChart || !window.metricsGlobals.ramChart) {
        console.warn('Charts not initialized or no data');
        return;
    }
    updateChartData(window.metricsGlobals.cpuChart, historicalData.cpu, 'CPU Usage (%)');
    updateChartData(window.metricsGlobals.ramChart, historicalData.ram, 'RAM Usage (MB)');
}

const metricsStyles = `
.metrics-instance-card {
    transition: all 0.3s ease;
}

.metrics-instance-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.metrics-chart-container {
    position: relative;
    height: 256px;
}

.metrics-status-online {
    background: linear-gradient(135deg, #10b981, #059669);
}

.metrics-status-offline {
    background: linear-gradient(135deg, #ef4444, #dc2626);
}

.metrics-status-warning {
    background: linear-gradient(135deg, #f59e0b, #d97706);
}
`;

function addMetricsStyles() {
    if (document.getElementById('metrics-styles')) return;

    const styleSheet = document.createElement('style');
    styleSheet.id = 'metrics-styles';
    styleSheet.textContent = metricsStyles;
    document.head.appendChild(styleSheet);
}

document.addEventListener('DOMContentLoaded', function() {
    addMetricsStyles();
});