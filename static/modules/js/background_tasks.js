// plugins/background_tasks.js

class BackgroundTasksPlugin {
    constructor(config) {
        this.plugin_id = "background_tasks";
        this.name = "Background Tasks";
        this.config = config || {};
        this.initialized = false;
    }

    async initialize() {
        console.log('Initializing background tasks plugin...');
        
        // Add the nav item
        this.addNavigation();
        
        // Create the content section
        this.createContentSection();
        
        this.initialized = true;
        return true;
    }

    createContentSection() {
        // Skip if the section already exists
        if (document.getElementById('background_tasks-section')) {
            return;
        }

        const mainContent = document.querySelector('main');
        if (!mainContent) {
            console.error('Main content not found');
            return;
        }

        // Create the content section
        const contentSection = document.createElement('div');
        contentSection.id = 'background_tasks-section';
        contentSection.className = 'content-section';
        contentSection.innerHTML = this.getContentHTML();

        mainContent.appendChild(contentSection);
        console.log('Section "Background Tasks" created');

        // Wire up automatic data loading once the section is activated
        this.setupSectionObserver();
    }

    setupSectionObserver() {
        // Watch for attribute changes on the section
        const observer = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                    const section = mutation.target;
                    if (section.classList.contains('active')) {
                        // Section became active -- load the data
                        this.loadData();
                    }
                }
            });
        });

        const section = document.getElementById('background_tasks-section');
        if (section) {
            observer.observe(section, {
                attributes: true,
                attributeFilter: ['class']
            });
        }
    }

    addNavigation() {
        if (document.querySelector('[data-target="background_tasks-section"]')) {
            return;
        }

        const mainNav = document.getElementById('main-nav');
        if (!mainNav) {
            console.error('Main navigation not found');
            return;
        }

        // Create the nav item
        const navItem = document.createElement('a');
        navItem.href = '#';
        navItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600';
        navItem.setAttribute('data-target', 'background_tasks-section');
        navItem.innerHTML = `
            <i data-feather="clock" class="w-5 h-5 mr-3"></i>
            <span class="nav-text">Background Tasks</span>
        `;

        // Do NOT attach our own handler -- rely on the existing navigation system
        // Just add the element; the main navigation system picks it up on its own

        // Add to the modules container
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
        const sidebar = document.getElementById('sidebar');
        if (sidebar && sidebar.classList.contains('sidebar-collapsed')) {
            this.updateNavItemForCollapsedState(navItem, true);
        }

        console.log('Nav item "Background Tasks" added');

        // Re-run navigation setup for the new element
        if (typeof setupNavigation === 'function') {
            setupNavigation();
        }
    }

    updateNavItemForCollapsedState(navItem, isCollapsed) {
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

    getContentHTML() {
        return `
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-gray-800">Background Tasks and Locks</h2>
                <div class="flex space-x-2">
                    <button onclick="backgroundTasksPlugin.refreshAllData()" 
                            class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                        Refresh
                    </button>
                </div>
            </div>

            <!-- Scheduler statistics -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
                <div class="bg-blue-50 border border-blue-200 rounded-lg p-4">
                    <div class="flex items-center">
                        <i data-feather="play-circle" class="w-8 h-8 text-blue-600 mr-3"></i>
                        <div>
                            <p class="text-sm text-blue-600">Scheduler status</p>
                            <p id="scheduler-status" class="text-lg font-semibold text-blue-800">Loading...</p>
                        </div>
                    </div>
                </div>
                <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                    <div class="flex items-center">
                        <i data-feather="list" class="w-8 h-8 text-green-600 mr-3"></i>
                        <div>
                            <p class="text-sm text-green-600">Total jobs</p>
                            <p id="total-jobs" class="text-lg font-semibold text-green-800">0</p>
                        </div>
                    </div>
                </div>
                <div class="bg-purple-50 border border-purple-200 rounded-lg p-4">
                    <div class="flex items-center">
                        <i data-feather="clock" class="w-8 h-8 text-purple-600 mr-3"></i>
                        <div>
                            <p class="text-sm text-purple-600">Next run</p>
                            <p id="next-wakeup" class="text-lg font-semibold text-purple-800">—</p>
                        </div>
                    </div>
                </div>
                <div class="bg-orange-50 border border-orange-200 rounded-lg p-4">
                    <div class="flex items-center">
                        <i data-feather="cpu" class="w-8 h-8 text-orange-600 mr-3"></i>
                        <div>
                            <p class="text-sm text-orange-600">Pending tasks</p>
                            <p id="pending-tasks" class="text-lg font-semibold text-orange-800">0</p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Active locks -->
            <div class="mb-6">
                <h3 class="text-lg font-semibold text-gray-800 mb-4">Active Locks</h3>
                <div id="active-locks-container" class="bg-gray-50 rounded-lg p-4">
                    <div class="text-center py-4">
                        <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 mx-auto mb-2"></div>
                        <p class="text-gray-500">Loading locks...</p>
                    </div>
                </div>
            </div>

            <!-- Scheduler jobs -->
            <div class="mb-6">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="text-lg font-semibold text-gray-800">Scheduler Jobs</h3>
                    <button onclick="backgroundTasksPlugin.triggerAllJobs()" 
                            class="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700 flex items-center">
                        <i data-feather="play" class="w-3 h-3 mr-1"></i>
                        Run all
                    </button>
                </div>
                <div id="scheduler-jobs-container" class="bg-white border border-gray-200 rounded-lg overflow-hidden">
                    <div class="text-center py-4">
                        <div class="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600 mx-auto mb-2"></div>
                        <p class="text-gray-500">Loading jobs...</p>
                    </div>
                </div>
            </div>

            <!-- Admin actions -->
            <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <h3 class="text-lg font-semibold text-yellow-800 mb-2">Admin Actions</h3>
                <p class="text-yellow-700 text-sm mb-3">
                    Warning: these actions are available only to system administrators
                </p>
                <div class="flex space-x-2">
                    <button onclick="backgroundTasksPlugin.forceCleanLocks()" 
                            class="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 flex items-center">
                        <i data-feather="trash-2" class="w-4 h-4 mr-2"></i>
                        Clear all locks
                    </button>
                    <button onclick="backgroundTasksPlugin.restartScheduler()" 
                            class="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 flex items-center">
                        <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                        Restart scheduler
                    </button>
                </div>
            </div>
        </div>
        `;
    }

    async loadData() {
        await this.loadSchedulerStats();
        await this.loadActiveLocks();
        await this.loadSchedulerJobs();
    }

    async loadSchedulerStats() {
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch('/api/admin/scheduler/stats', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const stats = await response.json();
                this.displaySchedulerStats(stats);
            } else {
                this.displayError('Error loading statistics');
            }
        } catch (error) {
            console.error('Error loading statistics:', error);
            this.displayError('Network error');
        }
    }

    async loadActiveLocks() {
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch('/api/admin/locks', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                this.displayActiveLocks(data.locks || []);
            } else {
                this.displayLocksError();
            }
        } catch (error) {
            console.error('Error loading locks:', error);
            this.displayLocksError();
        }
    }

    async loadSchedulerJobs() {
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch('/api/admin/scheduler/jobs', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                const data = await response.json();
                this.displaySchedulerJobs(data.jobs || []);
            } else {
                this.displayJobsError();
            }
        } catch (error) {
            console.error('Error loading jobs:', error);
            this.displayJobsError();
        }
    }

    displaySchedulerStats(stats) {
    if (stats.error) {
        document.getElementById('scheduler-status').textContent = 'Unavailable';
        document.getElementById('scheduler-status').className = 'text-lg font-semibold text-red-600';
        return;
    }

    document.getElementById('scheduler-status').textContent =
        stats.scheduler_running ? 'Running' : 'Stopped';
    document.getElementById('scheduler-status').className =
        `text-lg font-semibold ${stats.scheduler_running ? 'text-green-600' : 'text-red-600'}`;

    document.getElementById('total-jobs').textContent = stats.total_jobs || 0;
    document.getElementById('pending-tasks').textContent = stats.pending_tasks || 0;

    const nextWakeup = document.getElementById('next-wakeup');
    if (stats.next_wakeup) {
        nextWakeup.textContent = new Date(stats.next_wakeup).toLocaleString();
    } else {
        nextWakeup.textContent = 'No scheduled jobs';
    }
}

    displayActiveLocks(locks) {
        const container = document.getElementById('active-locks-container');
        
        if (locks.length === 0) {
            container.innerHTML = `
                <div class="text-center py-4 text-gray-500">
                    <i data-feather="lock" class="w-12 h-12 mx-auto mb-2"></i>
                    <p>No active locks</p>
                </div>
            `;
            feather.replace();
            return;
        }

        container.innerHTML = locks.map(lock => `
            <div class="border border-gray-200 rounded-lg p-4 mb-3 last:mb-0">
                <div class="flex justify-between items-start mb-2">
                    <div>
                        <h4 class="font-semibold text-gray-800">${lock.lock_name}</h4>
                        <p class="text-sm text-gray-600">Instance: ${lock.instance_id}</p>
                    </div>
                    <div class="flex space-x-2">
                        <button onclick="backgroundTasksPlugin.forceReleaseLock('${lock.lock_name}')"
                                class="px-3 py-1 bg-red-100 text-red-700 rounded text-sm hover:bg-red-200 flex items-center">
                            <i data-feather="unlock" class="w-3 h-3 mr-1"></i>
                            Release
                        </button>
                    </div>
                </div>
                <div class="text-xs text-gray-500">
                    Acquired: ${new Date(lock.acquired_at).toLocaleString()}
                </div>
            </div>
        `).join('');

        feather.replace();
    }

    displaySchedulerJobs(jobs) {
        const container = document.getElementById('scheduler-jobs-container');
        
        if (jobs.length === 0) {
            container.innerHTML = `
                <div class="text-center py-4 text-gray-500">
                    <i data-feather="clock" class="w-12 h-12 mx-auto mb-2"></i>
                    <p>No jobs found</p>
                </div>
            `;
            feather.replace();
            return;
        }

        container.innerHTML = `
            <table class="responsive-table min-w-full divide-y divide-gray-200">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">ID</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Next run</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Trigger</th>
                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-200">
                    ${jobs.map(job => `
                        <tr class="hover:bg-gray-50">
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">${job.id}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">${job.name}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                                ${job.next_run_time ? new Date(job.next_run_time).toLocaleString() : '—'}
                            </td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">${job.trigger}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm">
                                <button onclick="backgroundTasksPlugin.triggerJob('${job.id}')"
                                        class="text-blue-600 hover:text-blue-900 bg-blue-100 px-3 py-1 rounded text-sm flex items-center">
                                    <i data-feather="play" class="w-3 h-3 mr-1"></i>
                                    Run
                                </button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;

        feather.replace();
    }

    displayError(message) {
        document.getElementById('scheduler-status').textContent = message;
        document.getElementById('scheduler-status').className = 'text-lg font-semibold text-red-600';
    }

    displayLocksError() {
        const container = document.getElementById('active-locks-container');
        container.innerHTML = `
            <div class="text-center py-4 text-red-500">
                <i data-feather="alert-triangle" class="w-12 h-12 mx-auto mb-2"></i>
                <p>Error loading locks</p>
            </div>
        `;
        feather.replace();
    }

    displayJobsError() {
        const container = document.getElementById('scheduler-jobs-container');
        container.innerHTML = `
            <div class="text-center py-4 text-red-500">
                <i data-feather="alert-triangle" class="w-12 h-12 mx-auto mb-2"></i>
                <p>Error loading jobs</p>
            </div>
        `;
        feather.replace();
    }

    // Action methods
    async refreshAllData() {
        await this.loadData();
        this.showNotification('Data refreshed', 'success');
    }

    async triggerJob(jobId) {
        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch(`/api/admin/scheduler/jobs/${jobId}/trigger`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                this.showNotification(`Job ${jobId} started`, 'success');
                // Refresh the job list a second later
                setTimeout(() => this.loadSchedulerJobs(), 1000);
            } else {
                this.showNotification('Error starting the job', 'error');
            }
        } catch (error) {
            console.error('Error starting the job:', error);
            this.showNotification('Network error', 'error');
        }
    }

    async triggerAllJobs() {
        try {
            const token = localStorage.getItem('authToken');
            const jobsResponse = await fetch('/api/admin/scheduler/jobs', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (jobsResponse.ok) {
                const data = await jobsResponse.json();
                const jobs = data.jobs || [];
                
                let successCount = 0;
                for (const job of jobs) {
                    try {
                        const triggerResponse = await fetch(`/api/admin/scheduler/jobs/${job.id}/trigger`, {
                            method: 'POST',
                            headers: {
                                'Authorization': `Bearer ${token}`
                            }
                        });
                        if (triggerResponse.ok) {
                            successCount++;
                        }
                    } catch (error) {
                        console.error(`Error starting job ${job.id}:`, error);
                    }
                }

                this.showNotification(`Started ${successCount} of ${jobs.length} jobs`, 'success');
                setTimeout(() => this.loadSchedulerJobs(), 1000);
            }
        } catch (error) {
            console.error('Error bulk-starting jobs:', error);
            this.showNotification('Error starting jobs', 'error');
        }
    }

    async forceReleaseLock(lockName) {
        if (!confirm(`Are you sure you want to force-release the lock "${lockName}"?`)) {
            return;
        }

        try {
            const token = localStorage.getItem('authToken');
            const response = await fetch(`/api/admin/locks/${lockName}?confirm=true`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (response.ok) {
                this.showNotification(`Lock "${lockName}" released`, 'success');
                setTimeout(() => this.loadActiveLocks(), 1000);
            } else {
                this.showNotification('Error releasing the lock', 'error');
            }
        } catch (error) {
            console.error('Error releasing the lock:', error);
            this.showNotification('Network error', 'error');
        }
    }

    async forceCleanLocks() {
        if (!confirm('Are you sure you want to clear ALL locks? This may disrupt background tasks.')) {
            return;
        }

        try {
            const token = localStorage.getItem('authToken');
            const locksResponse = await fetch('/api/admin/locks', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            if (locksResponse.ok) {
                const data = await locksResponse.json();
                const locks = data.locks || [];
                
                let releasedCount = 0;
                for (const lock of locks) {
                    try {
                        await fetch(`/api/admin/locks/${lock.lock_name}?confirm=true`, {
                            method: 'DELETE',
                            headers: {
                                'Authorization': `Bearer ${token}`
                            }
                        });
                        releasedCount++;
                    } catch (error) {
                        console.error(`Error releasing lock ${lock.lock_name}:`, error);
                    }
                }

                this.showNotification(`Released ${releasedCount} locks`, 'success');
                setTimeout(() => this.loadActiveLocks(), 1000);
            }
        } catch (error) {
            console.error('Error clearing locks:', error);
            this.showNotification('Error clearing locks', 'error');
        }
    }

    async restartScheduler() {
        if (!confirm('Restart the job scheduler? All current jobs will be restarted.')) {
            return;
        }

        this.showNotification('Scheduler restart is not implemented yet', 'info');
        // An API call to restart the scheduler could be added here
    }

    showNotification(message, type = 'info') {
        // Use the existing showNotification function, or fall back to our own
        if (typeof showNotification === 'function') {
            showNotification(message, type);
        } else {
            // Simple fallback when the global function is not available
            const notification = document.createElement('div');
            notification.className = `fixed top-4 right-4 p-4 rounded-lg shadow-lg z-50 ${
                type === 'success' ? 'bg-green-100 text-green-800 border border-green-200' :
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
            
            if (typeof feather !== 'undefined') {
                feather.replace();
            }
            
            setTimeout(() => {
                if (notification.parentElement) {
                    notification.remove();
                }
            }, 5000);
        }
    }

    getApiRoutes() {
        return [];
    }

    getHandlers() {
        return {};
    }

    async cleanup() {
        this.initialized = false;
    }
}

window.backgroundTasksPlugin = new BackgroundTasksPlugin();

function initBackgroundTasks() {
    console.log('Starting background tasks plugin initialization...');
    return window.backgroundTasksPlugin.initialize();
}