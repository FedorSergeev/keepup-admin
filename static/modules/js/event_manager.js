/**
 * Admin audit event management module
 *
 * Provides an interface for viewing, filtering, and analyzing system events
 */

// ==================== GLOBAL VARIABLES ====================

// Event data
var auditEvents = window.auditEvents || [];
var currentEventId = null;
let isAuditRouteActive = false;

// Pagination
var auditCurrentPage = 1;
var auditTotalPages = 0;
var auditTotalEvents = 0;
var auditPageSize = 50;
var auditIsLoading = false;

// Filters
var auditFilters = {
    event_type: null,
    instance_id: null,
    instance_name: null,
    start_date: null,
    end_date: null
};

// Available event types and instances
var auditEventTypes = [];
var auditInstances = [];

// Statistics
var auditStats = null;

// Initialization flag
var auditInitialized = false;

// ==================== HELPER FUNCTIONS ====================

/**
 * Safe escaping of strings for use in HTML/JavaScript
 */
function escapeHtml(str) {
    if (typeof str !== 'string') return str || '';
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;')
        .replace(/\//g, '&#x2F;');
}

/**
 * Date formatting
 */
function formatDate(date, format = 'datetime') {
    if (!date) return '—';
    const d = new Date(date);
    if (isNaN(d.getTime())) return '—';

    const options = {
        date: { year: 'numeric', month: 'long', day: 'numeric' },
        datetime: { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false },
        time: { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }
    };

    return d.toLocaleString('en-GB', options[format] || options.datetime);
}

/**
 * JSON formatting for display
 */
function formatJSON(data) {
    if (!data) return '—';
    try {
        if (typeof data === 'string') {
            data = JSON.parse(data);
        }
        return JSON.stringify(data, null, 2);
    } catch (e) {
        return String(data);
    }
}

/**
 * Get color for event type
 */
function getEventTypeColor(eventType) {
    const colorMap = {
        'user_login': 'bg-green-100 text-green-800',
        'user_logout': 'bg-gray-100 text-gray-800',
        'payment_success': 'bg-blue-100 text-blue-800',
        'payment_failed': 'bg-red-100 text-red-800',
        'error': 'bg-red-100 text-red-800',
        'warning': 'bg-yellow-100 text-yellow-800',
        'info': 'bg-blue-100 text-blue-800',
        'config_change': 'bg-purple-100 text-purple-800',
        'api_call': 'bg-indigo-100 text-indigo-800'
    };
    return colorMap[eventType] || 'bg-gray-100 text-gray-800';
}

/**
 * Show notification
 */
function showNotification(message, type = 'info') {
    // Check whether a global notification function exists and isn't this same function
    if (window.showNotification && window.showNotification !== showNotification) {
        // Use the existing notification function from main_new.js
        window.showNotification(message, type);
    } else if (window.app && window.app.showNotification) {
        // Fallback option
        window.app.showNotification(message, type);
    } else {
        // Create a temporary notification if no global function exists
        console.log(`[${type.toUpperCase()}] ${message}`);

        // Create a simple notification on the page
        const notification = document.createElement('div');
        notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${
            type === 'success' ? 'bg-green-100 text-green-800 border border-green-200' :
            type === 'error' ? 'bg-red-100 text-red-800 border border-red-200' :
            'bg-blue-100 text-blue-800 border border-blue-200'
        }`;
        notification.innerHTML = `
            <div class="flex items-center">
                <span>${escapeHtml(message)}</span>
                <button onclick="this.parentElement.parentElement.remove()"
                        class="ml-4 text-gray-500 hover:text-gray-700">
                    ✕
                </button>
            </div>
        `;
        document.body.appendChild(notification);

        // Auto-hide after 5 seconds
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 5000);
    }
}

// ==================== DATA LOADING ====================

/**
 * Load the event list with filtering
 */
async function loadAuditEvents(page = 1, pageSize = 50, append = false) {
    if (auditIsLoading) return;

    try {
        auditIsLoading = true;
        const token = localStorage.getItem('authToken');

        // Build the URL with filter parameters
        let url = `/api/events?page=${page}&page_size=${pageSize}`;

        if (auditFilters.event_type) {
            url += `&event_type=${encodeURIComponent(auditFilters.event_type)}`;
        }
        if (auditFilters.instance_id) {
            url += `&instance_id=${encodeURIComponent(auditFilters.instance_id)}`;
        }
        if (auditFilters.instance_name) {
            url += `&instance_name=${encodeURIComponent(auditFilters.instance_name)}`;
        }
        if (auditFilters.start_date) {
            url += `&start_date=${encodeURIComponent(auditFilters.start_date)}`;
        }
        if (auditFilters.end_date) {
            url += `&end_date=${encodeURIComponent(auditFilters.end_date)}`;
        }

        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            auditCurrentPage = result.page;
            auditTotalPages = result.total_pages;
            auditTotalEvents = result.total;

            if (page === 1 && !append) {
                auditEvents = result.events;
            } else {
                auditEvents = [...auditEvents, ...result.events];
            }

            displayAuditEvents(auditEvents);
            updateAuditPagination();
            updateFilterInfo();

            if (result.events.length > 0) {
                showNotification(`Loaded ${result.events.length} events of ${result.total}`, 'success');
            }
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Error loading events');
        }

    } catch (error) {
        console.error('Load error:', error);
        if (error.message !== 'Failed to fetch') {
            showNotification('Load error: ' + error.message, 'error');
        }
        displayAuditError(error.message);
    } finally {
        auditIsLoading = false;
    }
}

/**
 * Load event types
 */
async function loadEventTypes() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/events/types', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            auditEventTypes = result.event_types || [];
            updateEventTypeFilter();
        }
    } catch (error) {
        console.error('Error loading event types:', error);
    }
}

/**
 * Load the instance list
 */
async function loadInstances() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/events/instances', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            auditInstances = result.instances || [];
            updateInstanceFilter();
        }
    } catch (error) {
        console.error('Error loading instances:', error);
    }
}

/**
 * Load statistics
 */
async function loadAuditStats() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/events/stats', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            auditStats = await response.json();
            displayAuditStats();
        }
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// ==================== DATA DISPLAY ====================

/**
 * Display the event list
 */
function displayAuditEvents(events) {
    const container = document.getElementById('audit-events-container');
    if (!container) return;

    if (!events || events.length === 0) {
        container.innerHTML = `
            <div class="text-center py-12">
                <i data-feather="inbox" class="w-12 h-12 text-gray-400 mx-auto mb-3"></i>
                <p class="text-gray-500">No events to display</p>
                <button onclick="resetAuditFilters()" class="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                    Reset filters
                </button>
            </div>
        `;
        if (typeof feather !== 'undefined') feather.replace();
        return;
    }

    container.innerHTML = '';

    events.forEach(event => {
        const eventElement = createAuditEventElement(event);
        container.appendChild(eventElement);
    });

    if (typeof feather !== 'undefined') feather.replace();
}

/**
 * Create an event element
 */
function createAuditEventElement(event) {
    const div = document.createElement('div');
    div.className = 'audit-event bg-white border rounded-lg p-4 hover:shadow-md transition-shadow mb-3';
    div.setAttribute('data-event-id', event.id);

    const eventTypeColor = getEventTypeColor(event.event_type);
    const eventData = event.event_data ? formatJSON(event.event_data) : null;

    div.innerHTML = `
        <div class="flex justify-between items-start mb-3">
            <div class="flex items-center space-x-2">
                <span class="px-2 py-1 text-xs rounded-full ${eventTypeColor}">
                    ${escapeHtml(event.event_type)}
                </span>
                <span class="text-xs text-gray-500">ID: ${event.id}</span>
            </div>
            <span class="text-xs text-gray-500">${formatDate(event.created_at, 'datetime')}</span>
        </div>

        <div class="mb-3">
            <p class="text-gray-800">${escapeHtml(event.event_text)}</p>
        </div>

        <div class="text-sm text-gray-600 mb-3">
            <div class="flex items-center space-x-4">
                <span><strong>Instance:</strong> ${escapeHtml(event.instance_name || event.instance_id)}</span>
                ${event.instance_id ? `<span class="text-xs text-gray-500">(${escapeHtml(event.instance_id)})</span>` : ''}
            </div>
        </div>

        ${eventData && eventData !== '—' ? `
        <div class="mt-3">
            <button onclick="toggleEventData(${event.id})"
                    class="text-sm text-blue-600 hover:text-blue-800 flex items-center">
                <i data-feather="chevron-down" class="w-4 h-4 mr-1" id="chevron-${event.id}"></i>
                Show data
            </button>
            <div id="event-data-${event.id}" class="hidden mt-2">
                <pre class="bg-gray-50 p-3 rounded text-xs overflow-x-auto">${escapeHtml(eventData)}</pre>
            </div>
        </div>
        ` : ''}
    `;

    return div;
}

/**
 * Toggle event data display
 */
function toggleEventData(eventId) {
    const dataDiv = document.getElementById(`event-data-${eventId}`);
    const chevron = document.getElementById(`chevron-${eventId}`);

    if (dataDiv) {
        if (dataDiv.classList.contains('hidden')) {
            dataDiv.classList.remove('hidden');
            if (chevron) chevron.setAttribute('data-feather', 'chevron-up');
        } else {
            dataDiv.classList.add('hidden');
            if (chevron) chevron.setAttribute('data-feather', 'chevron-down');
        }
        if (typeof feather !== 'undefined') feather.replace();
    }
}

/**
 * Display an error
 */
function displayAuditError(message) {
    const container = document.getElementById('audit-events-container');
    if (!container) return;

    container.innerHTML = `
        <div class="text-center py-12">
            <i data-feather="alert-triangle" class="w-12 h-12 text-red-400 mx-auto mb-3"></i>
            <p class="text-red-500 mb-4">${escapeHtml(message)}</p>
            <button onclick="refreshAuditEvents()"
                    class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                Try again
            </button>
        </div>
    `;
    if (typeof feather !== 'undefined') feather.replace();
}

/**
 * Display statistics
 */
function displayAuditStats() {
    const container = document.getElementById('audit-stats-container');
    if (!container || !auditStats) return;

    container.innerHTML = `
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
            <div class="bg-blue-50 rounded-lg p-4">
                <div class="text-sm text-blue-600 mb-1">Total events</div>
                <div class="text-2xl font-bold text-blue-900">${auditStats.total_events || 0}</div>
            </div>
            <div class="bg-green-50 rounded-lg p-4">
                <div class="text-sm text-green-600 mb-1">Event types</div>
                <div class="text-2xl font-bold text-green-900">${auditStats.unique_event_types || 0}</div>
            </div>
            <div class="bg-purple-50 rounded-lg p-4">
                <div class="text-sm text-purple-600 mb-1">Instances</div>
                <div class="text-2xl font-bold text-purple-900">${auditStats.unique_instances || 0}</div>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div class="bg-white rounded-lg p-4 border">
                <h4 class="font-semibold text-gray-800 mb-3">Top event types</h4>
                <div class="space-y-2">
                    ${auditStats.event_types?.slice(0, 5).map(type => `
                        <div class="flex justify-between items-center">
                            <span class="text-sm">${escapeHtml(type.event_type)}</span>
                            <span class="text-sm font-semibold">${type.count}</span>
                        </div>
                        <div class="w-full bg-gray-200 rounded-full h-2">
                            <div class="bg-blue-600 rounded-full h-2" style="width: ${(type.count / auditStats.total_events) * 100}%"></div>
                        </div>
                    `).join('') || '<p class="text-gray-500">No data</p>'}
                </div>
            </div>

            <div class="bg-white rounded-lg p-4 border">
                <h4 class="font-semibold text-gray-800 mb-3">Active instances</h4>
                <div class="space-y-2">
                    ${auditStats.instances?.slice(0, 5).map(instance => `
                        <div class="flex justify-between items-center">
                            <span class="text-sm">${escapeHtml(instance.instance_name || instance.instance_id)}</span>
                            <span class="text-sm font-semibold">${instance.event_count}</span>
                        </div>
                    `).join('') || '<p class="text-gray-500">No data</p>'}
                </div>
            </div>
        </div>

        <div class="mt-6 bg-yellow-50 rounded-lg p-4">
            <div class="flex justify-between items-center">
                <div>
                    <p class="text-sm text-yellow-800">Current instance</p>
                    <p class="font-semibold">${escapeHtml(auditStats.current_instance?.name)}</p>
                    <p class="text-xs text-yellow-600">ID: ${escapeHtml(auditStats.current_instance?.id)}</p>
                </div>
                <button onclick="refreshAuditStats()" class="text-yellow-800 hover:text-yellow-900">
                    <i data-feather="refresh-cw" class="w-5 h-5"></i>
                </button>
            </div>
        </div>
    `;
    if (typeof feather !== 'undefined') feather.replace();
}

// ==================== FILTERING ====================

/**
 * Apply filters
 */
function applyAuditFilters() {
    const eventType = document.getElementById('filter-event-type')?.value;
    const instanceName = document.getElementById('filter-instance')?.value;
    const startDate = document.getElementById('filter-start-date')?.value;
    const endDate = document.getElementById('filter-end-date')?.value;

    auditFilters.event_type = eventType || null;
    auditFilters.instance_name = instanceName || null;
    auditFilters.start_date = startDate || null;
    auditFilters.end_date = endDate || null;

    refreshAuditEvents();
}

/**
 * Reset filters
 */
function resetAuditFilters() {
    auditFilters = {
        event_type: null,
        instance_id: null,
        instance_name: null,
        start_date: null,
        end_date: null
    };

    // Reset form fields
    const filterForm = document.getElementById('audit-filters-form');
    if (filterForm) filterForm.reset();

    refreshAuditEvents();
}

/**
 * Update filter info
 */
function updateFilterInfo() {
    const filterInfo = document.getElementById('filter-info');
    if (!filterInfo) return;

    const activeFilters = [];
    if (auditFilters.event_type) activeFilters.push(`Type: ${auditFilters.event_type}`);
    if (auditFilters.instance_name) activeFilters.push(`Instance: ${auditFilters.instance_name}`);
    if (auditFilters.start_date) activeFilters.push(`From: ${formatDate(auditFilters.start_date, 'date')}`);
    if (auditFilters.end_date) activeFilters.push(`To: ${formatDate(auditFilters.end_date, 'date')}`);

    if (activeFilters.length > 0) {
        filterInfo.innerHTML = `
            <div class="flex items-center space-x-2 text-sm text-gray-600">
                <span>Active filters:</span>
                ${activeFilters.map(f => `<span class="bg-gray-100 px-2 py-1 rounded">${escapeHtml(f)}</span>`).join('')}
                <button onclick="resetAuditFilters()" class="text-red-600 hover:text-red-800 text-sm">Reset all</button>
            </div>
        `;
    } else {
        filterInfo.innerHTML = '<span class="text-sm text-gray-500">No active filters</span>';
    }
}

/**
 * Update the event type dropdown
 */
function updateEventTypeFilter() {
    const select = document.getElementById('filter-event-type');
    if (!select) return;

    select.innerHTML = '<option value="">All types</option>' +
        auditEventTypes.map(type => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join('');
}

/**
 * Update the instance dropdown
 */
function updateInstanceFilter() {
    const select = document.getElementById('filter-instance');
    if (!select) return;

    select.innerHTML = '<option value="">All instances</option>' +
        auditInstances.map(inst => `<option value="${escapeHtml(inst.instance_name)}">${escapeHtml(inst.instance_name)} (${escapeHtml(inst.instance_id)})</option>`).join('');
}

// ==================== PAGINATION ====================

/**
 * Update pagination
 */
function updateAuditPagination() {
    const paginationContainer = document.getElementById('audit-pagination');
    if (!paginationContainer) return;

    if (auditTotalPages <= 1) {
        paginationContainer.innerHTML = '';
        return;
    }

    let paginationHtml = '<div class="flex justify-center items-center space-x-2 mt-6">';

    // "Back" button
    paginationHtml += `
        <button onclick="goToAuditPage(${auditCurrentPage - 1})"
                ${auditCurrentPage === 1 ? 'disabled' : ''}
                class="px-3 py-2 border rounded-lg ${auditCurrentPage === 1 ? 'bg-gray-100 text-gray-400' : 'hover:bg-gray-50'}">
            ← Back
        </button>
    `;

    // Page numbers
    const startPage = Math.max(1, auditCurrentPage - 2);
    const endPage = Math.min(auditTotalPages, auditCurrentPage + 2);

    for (let i = startPage; i <= endPage; i++) {
        paginationHtml += `
            <button onclick="goToAuditPage(${i})"
                    class="px-3 py-2 border rounded-lg ${i === auditCurrentPage ? 'bg-blue-600 text-white' : 'hover:bg-gray-50'}">
                ${i}
            </button>
        `;
    }

    // "Next" button
    paginationHtml += `
        <button onclick="goToAuditPage(${auditCurrentPage + 1})"
                ${auditCurrentPage === auditTotalPages ? 'disabled' : ''}
                class="px-3 py-2 border rounded-lg ${auditCurrentPage === auditTotalPages ? 'bg-gray-100 text-gray-400' : 'hover:bg-gray-50'}">
            Next →
        </button>
    `;

    paginationHtml += `<span class="ml-4 text-sm text-gray-500">Total: ${auditTotalEvents} events</span>`;
    paginationHtml += '</div>';

    paginationContainer.innerHTML = paginationHtml;
}

/**
 * Go to page
 */
function goToAuditPage(page) {
    if (page < 1 || page > auditTotalPages || page === auditCurrentPage) return;
    loadAuditEvents(page, auditPageSize, false);
}

// ==================== DATA MANAGEMENT ====================

/**
 * Clean up old events
 */
async function cleanupOldEvents() {
    const days = prompt('Delete events older than (days):', '30');
    if (!days) return;

    if (!confirm(`Are you sure you want to delete all events older than ${days} days? This action cannot be undone.`)) return;

    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/events/cleanup?days=${days}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            showNotification(result.message, 'success');
            refreshAuditEvents();
            loadAuditStats();
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Cleanup error');
        }
    } catch (error) {
        console.error('Cleanup error:', error);
        showNotification('Cleanup error: ' + error.message, 'error');
    }
}

/**
 * Refresh the event list
 */
function refreshAuditEvents() {
    loadAuditEvents(1, auditPageSize, false);
}

/**
 * Refresh statistics
 */
function refreshAuditStats() {
    loadAuditStats();
}

// ==================== DATA EXPORT ====================

/**
 * Export events to CSV
 */
function exportEventsToCSV() {
    if (!auditEvents || auditEvents.length === 0) {
        showNotification('No data to export', 'warning');
        return;
    }

    const headers = ['ID', 'Event type', 'Text', 'Instance ID', 'Instance', 'Created at', 'Data'];
    const rows = auditEvents.map(event => [
        event.id,
        event.event_type,
        event.event_text,
        event.instance_id,
        event.instance_name || '',
        formatDate(event.created_at, 'datetime'),
        event.event_data ? JSON.stringify(event.event_data) : ''
    ]);

    const csvContent = [headers, ...rows].map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(["\uFEFF" + csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `audit_events_${new Date().toISOString().slice(0,19)}.csv`);
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    showNotification('Export completed', 'success');
}

// ==================== UI SECTION ====================

/**
 * Create the audit section
 */
function createAuditSection() {
    // Check whether the section already exists
    if (document.getElementById('audit-section')) return;

    const mainContent = document.querySelector('main');
    if (!mainContent) return;

    const section = document.createElement('div');
    section.id = 'audit-section';
    section.className = 'content-section';
    section.innerHTML = `
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-gray-800">Event audit</h2>
                <div class="flex space-x-2">
                    <button onclick="exportEventsToCSV()"
                            class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center">
                        <i data-feather="download" class="w-4 h-4 mr-2"></i>
                        Export CSV
                    </button>
                    <button onclick="cleanupOldEvents()"
                            class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 flex items-center">
                        <i data-feather="trash-2" class="w-4 h-4 mr-2"></i>
                        Clean up old
                    </button>
                    <button onclick="refreshAuditEvents()"
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                        Refresh
                    </button>
                </div>
            </div>

            <!-- Statistics -->
            <div id="audit-stats-container" class="mb-6"></div>

            <!-- Filters -->
            <div class="bg-gray-50 rounded-lg p-4 mb-6">
                <h3 class="font-semibold text-gray-800 mb-3">Filters</h3>
                <form id="audit-filters-form" class="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div>
                        <label class="block text-sm text-gray-600 mb-1">Event type</label>
                        <select id="filter-event-type" class="w-full border rounded-lg px-3 py-2">
                            <option value="">All types</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm text-gray-600 mb-1">Instance</label>
                        <select id="filter-instance" class="w-full border rounded-lg px-3 py-2">
                            <option value="">All instances</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm text-gray-600 mb-1">Date from</label>
                        <input type="date" id="filter-start-date" class="w-full border rounded-lg px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm text-gray-600 mb-1">Date to</label>
                        <input type="date" id="filter-end-date" class="w-full border rounded-lg px-3 py-2">
                    </div>
                </form>
                <div class="flex justify-between items-center mt-4">
                    <div id="filter-info"></div>
                    <div class="flex space-x-2">
                        <button onclick="applyAuditFilters()"
                                class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                            Apply
                        </button>
                        <button onclick="resetAuditFilters()"
                                class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                            Reset
                        </button>
                    </div>
                </div>
            </div>

            <!-- Event list -->
            <div id="audit-events-container" class="space-y-3">
                <div class="text-center py-12">
                    <i data-feather="loader" class="w-12 h-12 text-gray-400 mx-auto mb-3 animate-spin"></i>
                    <p class="text-gray-500">Loading events...</p>
                </div>
            </div>

            <!-- Pagination -->
            <div id="audit-pagination"></div>
        </div>
    `;

    mainContent.appendChild(section);
    if (typeof feather !== 'undefined') feather.replace();
}

/**
 * Show the audit section
 */
function showAuditSection() {
    if (window.appRouter) {
        window.appRouter.navigate('/selfcare/modules/audit');
    }

    // Hide all sections
    const contentSections = document.querySelectorAll('.content-section');
    contentSections.forEach(section => {
        section.classList.remove('active');
    });

    // Show the audit section
    const targetSection = document.getElementById('audit-section');
    if (targetSection) {
        targetSection.classList.add('active');
        // Update the title
        const titleEl = document.getElementById('current-section-title');
        if (titleEl) {
            titleEl.textContent = 'Event audit';
        }
    }

    isAuditRouteActive = true;
    if (typeof feather !== 'undefined') feather.replace();

    // Load data only if the section was created
    if (document.getElementById('audit-section')) {
        loadAuditStats();
        loadEventTypes();
        loadInstances();
        loadAuditEvents(1, auditPageSize, false);
    }
}

// ==================== NAVIGATION ====================

/**
 * Add an item to the sidebar menu
 */
function addAuditNavigation() {

    const mainNav = document.getElementById('main-nav');
    if (!mainNav) return;

    // Check whether the item is already added
    if (document.querySelector('[data-target="audit-section"]')) return;

    const navItem = document.createElement('a');
    navItem.href = '#';
    navItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600';
    navItem.setAttribute('data-target', 'audit-section');
    navItem.innerHTML = `
        <i data-feather="activity" class="w-5 h-5 mr-3"></i>
        <span class="nav-text">Event audit</span>
    `;

    navItem.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        showAuditSection();

        // Update the active menu item
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('text-white', 'bg-blue-800');
            item.classList.add('text-blue-100', 'hover:bg-blue-600');
        });

        this.classList.remove('text-blue-100', 'hover:bg-blue-600');
        this.classList.add('text-white', 'bg-blue-800');
    });

    // Add to the modules container or the main menu
    const modulesNavContainer = document.getElementById('modules-nav-container');
    if (modulesNavContainer) {
        modulesNavContainer.appendChild(navItem);
    } else {
        mainNav.appendChild(navItem);
    }

    if (typeof feather !== 'undefined') feather.replace();
}

// ==================== SYSTEM REGISTRATION ====================

/**
 * Register routes in the router
 */
function registerAuditRoutes() {
    if (window.appRouter) {
        window.appRouter.registerRoute('/selfcare/modules/audit', () => {
            console.log('Audit module: Handling route');
            showAuditSection();
        });
    }
}

/**
 * Register sections in the system
 */
function registerAuditSections() {
    if (window.appSections) {
        window.appSections.registerSection(
            'audit-section',
            '/selfcare/modules/audit',
            () => {
                console.log('Audit module: Handling section');
                showAuditSection();
            }
        );
        console.log('Audit module: Sections registered');
    }
}

// ==================== INITIALIZATION ====================

/**
 * Initialize the module (called from main_new.js)
 */
function initAuditModule() {
    // Prevent double initialization
    if (window.auditModuleInitialized) {
        console.log('Audit module already initialized');
        return;
    }

    console.log('Initializing Audit Module');

    // Create the UI section
    createAuditSection();

    // Register routes
    registerAuditRoutes();

    // Register sections
    registerAuditSections();

    // Add an item to navigation
    addAuditNavigation();

    window.auditModuleInitialized = true;
    console.log('Audit Module initialized');
}

// ==================== FUNCTION EXPORTS ====================

// Make functions globally accessible
window.auditEvents = auditEvents;
window.auditIsLoading = auditIsLoading;
window.auditCurrentPage = auditCurrentPage;

window.loadAuditEvents = loadAuditEvents;
window.refreshAuditEvents = refreshAuditEvents;
window.loadAuditStats = loadAuditStats;
window.refreshAuditStats = refreshAuditStats;
window.applyAuditFilters = applyAuditFilters;
window.resetAuditFilters = resetAuditFilters;
window.goToAuditPage = goToAuditPage;
window.cleanupOldEvents = cleanupOldEvents;
window.exportEventsToCSV = exportEventsToCSV;
window.toggleEventData = toggleEventData;
window.showAuditSection = showAuditSection;
window.initAuditModule = initAuditModule;

document.addEventListener('DOMContentLoaded', function() {
    const checkUserInterval = setInterval(function() {
        if (window.currentUser !== undefined) {
            clearInterval(checkUserInterval);
            initAuditModule();
        }
    }, 100);
});