var themesData = [];
var themesCurrentPage = 1;
var themesTotalPages = 0;
var themesPageSize = 20;
var themesIsLoading = false;
var themesHasMore = false;
var currentActiveThemeId = null;

// Cache for themes
var themesCache = {
    data: null,
    timestamp: null,
    TTL: 2 * 60 * 1000,
    set: function(data) {
        this.data = data;
        this.timestamp = Date.now();
    },
    get: function() {
        if (!this.timestamp || (Date.now() - this.timestamp > this.TTL)) {
            this.clear();
            return null;
        }
        return this.data;
    },
    clear: function() {
        this.data = null;
        this.timestamp = null;
    }
};

// Helper functions
function escapeHtml(str) {
    if (typeof str !== 'string') return str || '';
    return str.replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;')
        .replace(/\//g, '&#x2F;');
}

function escapeCssSelector(id) {
    if (typeof id !== 'string') return id || '';
    return id.replace(/[!"#$%&'()*+,.\/:;<=>?@[\\\]^`{|}~]/g, '\\$&');
}

function formatDate(date, format = 'datetime') {
    if (!date) return '—';
    const d = new Date(date);
    if (isNaN(d.getTime())) return '—';
    const options = {
        date: { year: 'numeric', month: 'long', day: 'numeric' },
        datetime: { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' },
        time: { hour: '2-digit', minute: '2-digit', second: '2-digit' }
    };
    return d.toLocaleString('en-GB', options[format] || options.datetime);
}

// Main theme functions
async function loadThemes(page = 1, pageSize = 20, append = false) {
    if (themesIsLoading) return;

    try {
        themesIsLoading = true;
        const token = localStorage.getItem('authToken');

        const response = await fetch(`/themes`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            const items = result.themes || [];
            themesData = items;

            // Save the active theme's ID
            const activeTheme = result.active_theme;
            if (activeTheme && activeTheme.id) {
                currentActiveThemeId = activeTheme.id;
            }

            displayThemes(themesData);
            showNotification(`Loaded ${themesData.length} theme(s)`, 'success');
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to load themes');
        }
    } catch (error) {
        showNotification('Load failed: ' + error.message, 'error');
        displayThemesError(error.message);
    } finally {
        themesIsLoading = false;
    }
}

function displayThemes(themes) {
    const container = document.getElementById('themes-container');
    if (!container) return;

    if (!themes || themes.length === 0) {
        container.innerHTML = `
            <div class="text-center py-12">
                <i data-feather="layout" class="w-12 h-12 text-gray-400 mx-auto mb-3"></i>
                <p class="text-gray-500">No themes created yet</p>
                <button onclick="showCreateThemeModal()"
                        class="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                    Create first theme
                </button>
            </div>
        `;
        feather.replace();
        return;
    }

    container.innerHTML = '';
    themes.forEach(theme => {
        const themeElement = createThemeElement(theme);
        container.appendChild(themeElement);
    });
    feather.replace();
}

function createThemeElement(theme) {
    const id = theme.id;
    const isActive = theme.is_active === true || theme.is_active === 1;
    const escapedId = escapeCssSelector(id);

    const div = document.createElement('div');
    div.className = `theme-item bg-white border rounded-lg p-6 hover:shadow-md transition-shadow ${isActive ? 'border-blue-400 ring-2 ring-blue-200' : ''}`;
    div.setAttribute('data-theme-id', escapedId);

    div.innerHTML = `
        <div class="flex justify-between items-start mb-4">
            <div class="flex-1">
                <h3 class="font-semibold text-xl text-gray-800">${escapeHtml(theme.theme_name || 'Untitled')}</h3>
                <p class="text-sm text-gray-500 mt-1">ID: ${escapeHtml(theme.id)}</p>
            </div>
            <div class="flex items-center space-x-2">
                ${isActive ?
                    '<span class="px-3 py-1 text-xs rounded-full bg-green-100 text-green-800 flex items-center"><i data-feather="check-circle" class="w-3 h-3 mr-1"></i>Active</span>' :
                    '<span class="px-3 py-1 text-xs rounded-full bg-gray-100 text-gray-600">Inactive</span>'
                }
            </div>
        </div>

        <div class="space-y-2 mb-4">
            <div class="flex items-center text-sm">
                <i data-feather="file-text" class="w-4 h-4 text-gray-400 mr-2"></i>
                <span class="text-gray-600">Main page file:</span>
                <code class="ml-2 px-2 py-1 bg-gray-100 rounded text-sm">${escapeHtml(theme.main_page_file || 'index_new.html')}</code>
            </div>
            ${theme.created_at ? `
            <div class="flex items-center text-sm">
                <i data-feather="calendar" class="w-4 h-4 text-gray-400 mr-2"></i>
                <span class="text-gray-500">Created: ${formatDate(theme.created_at, 'date')}</span>
            </div>
            ` : ''}
        </div>

        <div class="flex flex-wrap gap-2 mt-4 pt-4 border-t">
            ${!isActive ? `
            <button onclick="activateTheme(${id})"
                    class="theme-action-btn bg-green-600 hover:bg-green-700 text-white px-3 py-2 rounded text-sm flex items-center">
                <i data-feather="check-circle" class="w-4 h-4 mr-1"></i>Activate
            </button>
            ` : ''}
            <button onclick="showEditThemeModal(${id})"
                    class="theme-action-btn bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded text-sm flex items-center">
                <i data-feather="edit-2" class="w-4 h-4 mr-1"></i>Edit
            </button>
            ${!isActive ? `
            <button onclick="deleteTheme(${id}, '${escapeHtml(theme.theme_name)}')"
                    class="theme-action-btn bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded text-sm flex items-center">
                <i data-feather="trash-2" class="w-4 h-4 mr-1"></i>Delete
            </button>
            ` : ''}
        </div>
    `;

    return div;
}

function displayThemesError(message) {
    const container = document.getElementById('themes-container');
    if (!container) return;

    container.innerHTML = `
        <div class="col-span-full text-center py-12">
            <i data-feather="alert-triangle" class="w-12 h-12 text-red-400 mx-auto mb-3"></i>
            <p class="text-red-500 mb-4">${escapeHtml(message)}</p>
            <button onclick="refreshThemes()"
                    class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                Try again
            </button>
        </div>
    `;
    feather.replace();
}

function refreshThemes() {
    loadThemes(1, themesPageSize, false);
}

// CRUD operations
async function activateTheme(themeId) {
    if (!confirm('Are you sure you want to activate this theme? Other themes will be deactivated.')) return;

    try {
        themesIsLoading = true;
        const token = localStorage.getItem('authToken');

        const response = await fetch(`/themes/${themeId}/activate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            showNotification(result.message || 'Theme activated successfully', 'success');
            refreshThemes();
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to activate theme');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
    } finally {
        themesIsLoading = false;
    }
}

async function createTheme(themeName, mainPageFile, isActive) {
    try {
        themesIsLoading = true;
        const token = localStorage.getItem('authToken');

        const response = await fetch(`/themes/create?theme_name=${encodeURIComponent(themeName)}&main_page_file=${encodeURIComponent(mainPageFile)}&is_active=${isActive}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            showNotification(result.message || 'Theme created successfully', 'success');
            refreshThemes();
            closeThemeModal();
            return true;
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to create theme');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
        return false;
    } finally {
        themesIsLoading = false;
    }
}

async function updateTheme(themeId, themeName, mainPageFile, isActive) {
    try {
        themesIsLoading = true;
        const token = localStorage.getItem('authToken');

        // First delete the old one, then create a new one with the same ID (if supported)
        // The current API implementation has no direct update, so we'd use a combination
        showNotification('Theme update is not yet implemented', 'info');
        return false;

    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
        return false;
    } finally {
        themesIsLoading = false;
    }
}

async function deleteTheme(themeId, themeName) {
    if (!confirm(`Are you sure you want to delete the theme "${themeName}"? This action cannot be undone.`)) return;

    try {
        themesIsLoading = true;
        const token = localStorage.getItem('authToken');

        const response = await fetch(`/themes/${themeId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            showNotification(result.message || 'Theme deleted successfully', 'success');
            refreshThemes();
        } else {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to delete theme');
        }
    } catch (error) {
        showNotification('Error: ' + error.message, 'error');
    } finally {
        themesIsLoading = false;
    }
}

// Modal windows
function showCreateThemeModal() {
    const existingModal = document.getElementById('theme-modal');
    if (existingModal) existingModal.remove();

    const modal = document.createElement('div');
    modal.id = 'theme-modal';
    modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4';
    modal.innerHTML = `
        <div class="bg-white rounded-lg shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
            <div class="flex justify-between items-center p-6 border-b">
                <h3 class="text-xl font-bold text-gray-800">Create new theme</h3>
                <button onclick="closeThemeModal()" class="text-gray-500 hover:text-gray-700 p-2">
                    <i data-feather="x" class="w-6 h-6"></i>
                </button>
            </div>

            <div class="p-6">
                <form id="theme-form" onsubmit="handleThemeFormSubmit(event)">
                    <div class="space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">
                                Theme name <span class="text-red-500">*</span>
                            </label>
                            <input type="text" id="theme-name" required
                                   placeholder="e.g. dark, light, blue"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">
                                Main page file <span class="text-red-500">*</span>
                            </label>
                            <input type="text" id="theme-file" required
                                   placeholder="e.g. index_new.html"
                                   value="index_new.html"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <p class="text-xs text-gray-500 mt-1">HTML file in the static/ folder</p>
                        </div>

                        <div>
                            <label class="flex items-center">
                                <input type="checkbox" id="theme-active" class="mr-2">
                                <span class="text-sm text-gray-700">Activate immediately after creation</span>
                            </label>
                        </div>
                    </div>
                </form>
            </div>

            <div class="flex justify-end space-x-3 p-6 border-t bg-gray-50">
                <button onclick="closeThemeModal()"
                        class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                    Cancel
                </button>
                <button type="submit" form="theme-form"
                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                    Create
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    feather.replace();
}

function showEditThemeModal(themeId) {
    const theme = themesData.find(t => t.id === themeId);
    if (!theme) {
        showNotification('Theme not found', 'error');
        return;
    }

    const existingModal = document.getElementById('theme-modal');
    if (existingModal) existingModal.remove();

    const modal = document.createElement('div');
    modal.id = 'theme-modal';
    modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4';
    modal.innerHTML = `
        <div class="bg-white rounded-lg shadow-xl w-full max-w-md max-h-[90vh] overflow-y-auto">
            <div class="flex justify-between items-center p-6 border-b">
                <h3 class="text-xl font-bold text-gray-800">Edit theme</h3>
                <button onclick="closeThemeModal()" class="text-gray-500 hover:text-gray-700 p-2">
                    <i data-feather="x" class="w-6 h-6"></i>
                </button>
            </div>

            <div class="p-6">
                <form id="theme-form" onsubmit="handleThemeUpdateSubmit(event, ${themeId})">
                    <div class="space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">
                                Theme name <span class="text-red-500">*</span>
                            </label>
                            <input type="text" id="theme-name" required
                                   value="${escapeHtml(theme.theme_name)}"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                        </div>

                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-1">
                                Main page file <span class="text-red-500">*</span>
                            </label>
                            <input type="text" id="theme-file" required
                                   value="${escapeHtml(theme.main_page_file)}"
                                   class="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500">
                            <p class="text-xs text-gray-500 mt-1">HTML file in the static/ folder</p>
                        </div>

                        <div>
                            <label class="flex items-center">
                                <input type="checkbox" id="theme-active" ${theme.is_active ? 'checked' : ''}>
                                <span class="text-sm text-gray-700">Active theme</span>
                            </label>
                        </div>
                    </div>
                </form>
            </div>

            <div class="flex justify-end space-x-3 p-6 border-t bg-gray-50">
                <button onclick="closeThemeModal()"
                        class="px-4 py-2 bg-gray-300 text-gray-700 rounded-lg hover:bg-gray-400">
                    Cancel
                </button>
                <button type="submit" form="theme-form"
                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
                    Save
                </button>
            </div>
        </div>
    `;

    document.body.appendChild(modal);
    feather.replace();
}

function closeThemeModal() {
    const modal = document.getElementById('theme-modal');
    if (modal) modal.remove();
}

function handleThemeFormSubmit(e) {
    e.preventDefault();

    const themeName = document.getElementById('theme-name')?.value;
    const mainPageFile = document.getElementById('theme-file')?.value;
    const isActive = document.getElementById('theme-active')?.checked || false;

    if (!themeName || !mainPageFile) {
        showNotification('Please fill in all required fields', 'error');
        return;
    }

    createTheme(themeName, mainPageFile, isActive);
}

function handleThemeUpdateSubmit(e, themeId) {
    e.preventDefault();

    const themeName = document.getElementById('theme-name')?.value;
    const mainPageFile = document.getElementById('theme-file')?.value;
    const isActive = document.getElementById('theme-active')?.checked || false;

    if (!themeName || !mainPageFile) {
        showNotification('Please fill in all required fields', 'error');
        return;
    }

    updateTheme(themeId, themeName, mainPageFile, isActive);
}

// Section for display
function createThemesSection() {
    const mainContent = document.querySelector('main');
    if (!mainContent) return;
    if (document.getElementById('themes-section')) return;

    const section = document.createElement('div');
    section.id = 'themes-section';
    section.className = 'content-section';
    section.innerHTML = `
        <div class="bg-white rounded-lg shadow-sm p-6">
            <div class="flex justify-between items-center mb-6">
                <div>
                    <h2 class="text-2xl font-bold text-gray-800">Visual Themes</h2>
                    <p class="text-gray-600 mt-1">Manage the interface's look and feel</p>
                </div>
                <div class="flex space-x-2">
                    <button onclick="refreshThemes()"
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>Refresh
                    </button>
                    <button onclick="showCreateThemeModal()"
                            class="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 flex items-center">
                        <i data-feather="plus" class="w-4 h-4 mr-2"></i>Create theme
                    </button>
                </div>
            </div>

            <div id="themes-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                <div class="col-span-full text-center py-12">
                    <i data-feather="loader" class="w-12 h-12 text-gray-400 mx-auto mb-3 animate-spin"></i>
                    <p class="text-gray-500">Loading themes...</p>
                </div>
            </div>
        </div>
    `;

    mainContent.appendChild(section);
    feather.replace();
}

function showThemesSection() {
    if (window.appRouter) {
        window.appRouter.navigate('/selfcare/modules/themes');
    }

    const contentSections = document.querySelectorAll('.content-section');
    contentSections.forEach(section => {
        section.classList.remove('active');
    });

    const targetSection = document.getElementById('themes-section');
    if (targetSection) {
        targetSection.classList.add('active');
        const titleEl = document.getElementById('current-section-title');
        if (titleEl) titleEl.textContent = 'Visual Themes';
    }

    // Load themes when the section is shown
    loadThemes();

    feather.replace();
}

// Navigation
function addThemesNavigation() {
    const mainNav = document.getElementById('main-nav');
    if (!mainNav) return;
    if (document.querySelector('[data-target="themes-section"]')) return;

    const navItem = document.createElement('a');
    navItem.href = '#';
    navItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600';
    navItem.setAttribute('data-target', 'themes-section');
    navItem.innerHTML = `
        <i data-feather="layout" class="w-5 h-5 mr-3"></i>
        <span class="nav-text">Visual Themes</span>
    `;

    navItem.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();

        showThemesSection();

        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('text-white', 'bg-blue-800');
            item.classList.add('text-blue-100', 'hover:bg-blue-600');
        });

        this.classList.remove('text-blue-100', 'hover:bg-blue-600');
        this.classList.add('text-white', 'bg-blue-800');
    });

    const modulesNavContainer = document.getElementById('modules-nav-container');
    if (modulesNavContainer) {
        modulesNavContainer.appendChild(navItem);
    } else {
        mainNav.appendChild(navItem);
    }

    // Adapt for a collapsed sidebar
    const sidebar = document.getElementById('sidebar');
    if (sidebar && sidebar.classList.contains('sidebar-collapsed')) {
        const navText = navItem.querySelector('.nav-text');
        const icon = navItem.querySelector('i');
        if (navText) navText.style.display = 'none';
        if (icon) icon.classList.remove('mr-3');
        navItem.classList.add('justify-center', 'px-3');
    }

    feather.replace();
}

// Registration in the global router
function registerThemesRoutes() {
    if (window.appRouter) {
        window.appRouter.registerRoute('/selfcare/modules/themes', () => {
            setTimeout(() => showThemesSection(), 100);
        });
    }
}

function registerThemesSections() {
    if (window.appSections) {
        window.appSections.registerSection('themes-section', '/selfcare/modules/themes', () => {
            showThemesSection();
        });
    }
}

// Module initialization
function initThemesModule() {
    // Global functions
    window.themesData = themesData;
    window.loadThemes = loadThemes;
    window.refreshThemes = refreshThemes;
    window.activateTheme = activateTheme;
    window.deleteTheme = deleteTheme;
    window.createTheme = createTheme;
    window.showCreateThemeModal = showCreateThemeModal;
    window.showEditThemeModal = showEditThemeModal;
    window.closeThemeModal = closeThemeModal;
    window.handleThemeFormSubmit = handleThemeFormSubmit;
    window.handleThemeUpdateSubmit = handleThemeUpdateSubmit;
    window.showThemesSection = showThemesSection;

    // Create the section and add navigation
    createThemesSection();
    addThemesNavigation();
    registerThemesRoutes();
    registerThemesSections();

    // If the current URL matches themes, show it
    if (window.location.pathname === '/selfcare/modules/themes') {
        setTimeout(() => showThemesSection(), 100);
    }
}

// Auto-load the module
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemesModule);
} else {
    initThemesModule();
}