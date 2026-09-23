// Actions on a user row that app modules add.
// The framework does not know what these actions are: a "message on Telegram"
// button with its own request used to sit here directly, and the framework's
// users section ended up being part of one application's feature (keepup-27).
//
// A module puts an object { prepare?, render } here: prepare gets the whole
// list of users and can fetch its own data once; render gets a user and
// returns the button markup or an empty string.
window.KeepupUserActions = window.KeepupUserActions || [];

// Columns that modules of applications add to the users table. Same idea as
// the actions above and for the same reason, one step further: until
// keepup-28 this section carried a subscription model -- a plan column, a plan
// dialog, two hundred lines of one application's business shipped inside the
// framework to everyone who installs it.
//
// A module puts an object here: { heading, prepare?, render, afterRender? }.
// prepare receives the whole list of users and answers whether the column is
// to be shown at all -- which is how a capability that is not running in this
// deployment leaves no empty column behind and makes no request per row.
window.KeepupUserColumns = window.KeepupUserColumns || [];

//: Contributors whose column is shown for the list being rendered.
let usersShownColumns = [];

/** Asks each contributor whether its column belongs in this list, and prepares it. */
async function usersPrepareColumns(users) {
    usersShownColumns = [];
    for (const column of window.KeepupUserColumns) {
        try {
            const shown = typeof column.prepare === 'function' ? await column.prepare(users) : true;
            if (shown !== false) usersShownColumns.push(column);
        } catch (error) {
            // A contributor that fell over costs its own column, not the table.
            console.warn('A column of the users section failed to prepare:', error);
        }
    }
}

/** Puts the headings of the added columns in front of the actions column.

    Inserted rather than written into the template, because which columns there
    are is known only after the contributors have been asked -- and asked
    afresh on every load, since a capability can stop answering between two. */
function usersPlaceExtraHeadings() {
    const actions = document.getElementById('usersActionsHeading');
    if (!actions) return;
    actions.parentElement.querySelectorAll('.users-extra-heading').forEach(node => node.remove());
    for (const column of usersShownColumns) {
        const heading = document.createElement('th');
        heading.className = 'users-extra-heading px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase';
        heading.textContent = column.heading || '';
        actions.parentElement.insertBefore(heading, actions);
    }
}

/** Body cells of the added columns for one user. */
function usersExtraCells(user) {
    return usersShownColumns.map(column => {
        let body = '';
        try {
            body = column.render(user) || '';
        } catch (error) {
            console.warn('A column of the users section failed to render:', error);
        }
        return `<td class="px-6 py-4 whitespace-nowrap">${body}</td>`;
    }).join('');
}

/** Lets the contributors wire their own controls once the table is in the page. */
function usersColumnsRendered() {
    for (const column of usersShownColumns) {
        if (typeof column.afterRender !== 'function') continue;
        try {
            column.afterRender();
        } catch (error) {
            console.warn('A column of the users section failed to wire itself:', error);
        }
    }
}

/** Lets contributors fetch their own data before the table renders. */
async function usersPrepareActions(users) {
    for (const action of window.KeepupUserActions) {
        if (typeof action.prepare !== 'function') continue;
        try {
            await action.prepare(users);
        } catch (error) {
            // A contributor's failure does not take the whole users section down with it.
            console.warn('A contributor to the users section failed to prepare:', error);
        }
    }
}

/** Markup for the added actions on one row. */
function usersExtraActions(user) {
    return window.KeepupUserActions.map(action => {
        try {
            return typeof action.render === 'function' ? (action.render(user) || '') : '';
        } catch (error) {
            console.warn('A contributor to the users section failed to render:', error);
            return '';
        }
    }).join('');
}

function usersEscapeHtml(str) {
    return String(str === undefined || str === null ? '' : str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}


function renderUsers() {
    addUsersNavigation();
    addUsersSection();
    setupUsersEventListeners();
}

function addUsersNavigation() {
    const mainNav = document.getElementById('main-nav');
    if (!mainNav) return;

    if (document.querySelector('[data-target="users-section"]')) return;

    const usersNavItem = document.createElement('a');
    usersNavItem.href = '#';
    usersNavItem.className = 'nav-item flex items-center px-6 py-3 text-blue-100 hover:bg-blue-600';
    usersNavItem.setAttribute('data-target', 'users-section');
    usersNavItem.innerHTML = `
        <i data-feather="users" class="w-5 h-5 mr-3"></i>
        <span>Users</span>
    `;

    mainNav.appendChild(usersNavItem);
    feather.replace();
}

function addUsersSection() {
    const mainContent = document.querySelector('main');
    if (!mainContent) return;

    if (document.getElementById('users-section')) return;

    const usersSection = document.createElement('div');
    usersSection.id = 'users-section';
    usersSection.className = 'content-section';
    usersSection.innerHTML = `
        <div class="bg-white rounded-lg shadow-sm p-6 mb-6">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-2xl font-bold text-gray-800">User management</h2>
                <button onclick="loadAllUsers()" class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                    <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                    Refresh list
                </button>
            </div>

            <div class="bg-white rounded-lg shadow-sm overflow-hidden">
                <table class="responsive-table min-w-full divide-y divide-gray-200">
                    <thead class="bg-gray-50">
                        <tr>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">ID</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                            <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Registered</th>
                            <th id="usersActionsHeading" class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="usersListBody" class="bg-white divide-y divide-gray-200">
                        <tr>
                            <td colspan="7" class="px-6 py-4 text-center text-gray-500">
                                Loading...
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    `;

    const modulesContainer = document.getElementById('modules-container');
    if (modulesContainer) {
        modulesContainer.appendChild(usersSection);
    } else {
        const shopsSection = document.getElementById('shops-section');
        if (shopsSection) {
            shopsSection.parentNode.insertBefore(usersSection, shopsSection.nextSibling);
        }
    }

    feather.replace();
}

function setupUsersEventListeners() {
    document.addEventListener('click', function(e) {
        if (e.target.closest('[data-target="users-section"]')) {
            loadAllUsers();
        }
    });
}

async function loadAllUsers() {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch('/api/admin/users', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const users = await response.json();
            await usersPrepareActions(users);
            await usersPrepareColumns(users);
            displayUsersList(users);
        } else if (response.status === 403) {
            showNotification('Access denied. Administrator rights required.', 'error');
        }
    } catch (error) {

        showNotification('Error loading users', 'error');
    }
}




function displayUsersList(users) {
    const usersListBody = document.getElementById('usersListBody');
    if (!usersListBody) return;

    if (users.length === 0) {
        usersListBody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4 text-gray-500">
                    No users found
                </td>
            </tr>
        `;
        return;
    }

    usersListBody.innerHTML = users.map(user => `
        <tr>
            <td class="px-6 py-4 whitespace-nowrap">${user.id}</td>
            <td class="px-6 py-4 whitespace-nowrap font-medium">${user.username}</td>
            <td class="px-6 py-4 whitespace-nowrap">
                <span class="status-badge ${user.status === 'active' ? 'status-active' : 'status-blocked'}">
                    ${user.status === 'active' ? 'Active' : 'Blocked'}
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap">
                <span class="role-badge ${user.role === ROLE_ADMIN ? 'role-admin' : 'role-client'}">
                    ${user.role === ROLE_ADMIN ? 'Admin' : 'Client'}
                </span>
            </td>
            <td class="px-6 py-4 whitespace-nowrap">${new Date(user.created_at).toLocaleDateString('en-GB')}</td>
            ${usersExtraCells(user)}
            <td class="px-6 py-4 whitespace-nowrap">
                <div class="user-actions flex flex-col space-y-2">
                    <div class="flex space-x-2">
                        ${user.status === 'active' ?
                            `<button onclick="blockUserWithReason(${user.id}, '${user.username}')" class="user-action-btn btn-block text-xs px-2 py-1">Block</button>` :
                            `<button onclick="unblockUserWithReason(${user.id}, '${user.username}')" class="user-action-btn btn-activate text-xs px-2 py-1">Activate</button>`
                        }
                        <button onclick="showUserBlockHistory(${user.id}, '${user.username}')" class="user-action-btn text-xs px-2 py-1">Block log</button>
                        ${user.role === ROLE_CLIENT ?
                            `<button onclick="makeUserAdmin(${user.id})" class="user-action-btn btn-make-admin text-xs px-2 py-1">Make admin</button>` :
                            user.id !== (currentUser?.id || 0) ?
                                `<button onclick="makeUserClient(${user.id})" class="user-action-btn btn-make-client text-xs px-2 py-1">Make client</button>` :
                                ``
                        }
                    </div>
                    <div class="flex space-x-2">
                        <button onclick="showPasswordModal(${user.id}, '${user.username}')"
                                class="user-action-btn btn-change-password text-xs px-2 py-1">
                            Change password
                        </button>
                        ${usersExtraActions(user)}
                    </div>
                </div>
            </td>
        </tr>
    `).join('');

    usersPlaceExtraHeadings();
    usersColumnsRendered();
    feather.replace();
}

function showPasswordModal(userId, username) {
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
    modal.innerHTML = `
        <div class="bg-white rounded-lg p-6 w-full max-w-md">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-semibold">Change password for ${username}</h3>
                <button onclick="closeModal()" class="text-gray-500 hover:text-gray-700">
                    <i data-feather="x" class="w-5 h-5"></i>
                </button>
            </div>

            <div class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        New password
                    </label>
                    <div class="relative">
                        <input type="password"
                               id="newPasswordInput"
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                               placeholder="Enter a new password"
                               minlength="6"
                               required>
                        <button type="button"
                                onclick="togglePasswordVisibility('newPasswordInput')"
                                class="absolute right-3 top-2 text-gray-500 hover:text-gray-700">
                            <i data-feather="eye" class="w-5 h-5"></i>
                        </button>
                    </div>
                    <p class="mt-1 text-xs text-gray-500">
                        At least 6 characters
                    </p>
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        Confirm password
                    </label>
                    <div class="relative">
                        <input type="password"
                               id="confirmPasswordInput"
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                               placeholder="Repeat the new password"
                               minlength="6"
                               required>
                        <button type="button"
                                onclick="togglePasswordVisibility('confirmPasswordInput')"
                                class="absolute right-3 top-2 text-gray-500 hover:text-gray-700">
                            <i data-feather="eye" class="w-5 h-5"></i>
                        </button>
                    </div>
                </div>

                <div class="flex items-center space-x-2 mb-4">
                    <input type="checkbox"
                           id="generateRandomPassword"
                           class="h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500">
                    <label for="generateRandomPassword" class="text-sm text-gray-700">
                        Generate a random password
                    </label>
                </div>

                <div id="generatedPasswordContainer" class="hidden">
                    <div class="bg-gray-50 border border-gray-200 rounded-lg p-3">
                        <div class="flex justify-between items-center mb-2">
                            <span class="font-medium text-gray-700">Generated password:</span>
                            <button type="button"
                                    onclick="copyToClipboard('generatedPasswordText')"
                                    class="text-blue-600 hover:text-blue-800 text-sm">
                                <i data-feather="copy" class="w-4 h-4"></i>
                            </button>
                        </div>
                        <div class="font-mono text-sm bg-white p-2 rounded border" id="generatedPasswordText"></div>
                        <p class="mt-2 text-xs text-red-600">
                            Save this password! It will not be shown again.
                        </p>
                    </div>
                </div>

                <div class="flex items-center space-x-2 mb-4">
                    <input type="checkbox"
                           id="forcePasswordChange"
                           class="h-4 w-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500">
                    <label for="forcePasswordChange" class="text-sm text-gray-700">
                        The user must change their password at the next sign-in
                    </label>
                </div>
            </div>

            <div class="flex justify-end space-x-3 mt-6">
                <button onclick="generateRandomPassword()"
                        class="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 flex items-center">
                    <i data-feather="refresh-cw" class="w-4 h-4 mr-2"></i>
                    Generate
                </button>
                <button onclick="closeModal()"
                        class="px-4 py-2 text-gray-600 hover:text-gray-800">
                    Cancel
                </button>
                <button onclick="changeUserPassword(${userId}, '${username}')"
                        class="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 flex items-center">
                    <i data-feather="key" class="w-4 h-4 mr-2"></i>
                    Save password
                </button>
            </div>
        </div>
    `;

    window.closeModal = () => {
        document.body.removeChild(modal);
    };

    window.togglePasswordVisibility = (inputId) => {
        const input = document.getElementById(inputId);
        const icon = input.parentNode.querySelector('i');

        if (input.type === 'password') {
            input.type = 'text';
            icon.setAttribute('data-feather', 'eye-off');
        } else {
            input.type = 'password';
            icon.setAttribute('data-feather', 'eye');
        }
        feather.replace();
    };

    window.generateRandomPassword = () => {
        const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*';
        const password = Array.from({length: 12}, () => chars[Math.floor(Math.random() * chars.length)]).join('');

        document.getElementById('generatedPasswordText').textContent = password;
        document.getElementById('generatedPasswordContainer').classList.remove('hidden');

        // Automatically fill the fields
        document.getElementById('newPasswordInput').value = password;
        document.getElementById('confirmPasswordInput').value = password;

        // Show the password so it can be copied
        document.getElementById('newPasswordInput').type = 'text';
        document.getElementById('confirmPasswordInput').type = 'text';

        // Update the icons
        feather.replace();
    };

    window.copyToClipboard = (elementId) => {
        const text = document.getElementById(elementId).textContent;
        navigator.clipboard.writeText(text).then(() => {
            showNotification('Password copied to clipboard', 'success');
        });
    };

    window.changeUserPassword = async (userId, username) => {
        const newPassword = document.getElementById('newPasswordInput').value;
        const confirmPassword = document.getElementById('confirmPasswordInput').value;
        const generateRandom = document.getElementById('generateRandomPassword').checked;

        // Validation
        if (!newPassword && !generateRandom) {
            showNotification('Enter a new password or generate a random one', 'error');
            return;
        }

        if (newPassword && newPassword.length < 6) {
            showNotification('Password must be at least 6 characters', 'error');
            return;
        }

        if (newPassword !== confirmPassword) {
            showNotification('Passwords do not match', 'error');
            return;
        }

        try {
            const token = localStorage.getItem('authToken');

            let response;
            if (generateRandom) {
                // Use random password generation
                response = await fetch(`/api/admin/users/${userId}/generate-password`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });
            } else {
                // Use setting a specific password
                response = await fetch(`/api/admin/users/${userId}/password`, {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({
                        new_password: newPassword
                    })
                });
            }

            const data = await response.json();

            if (response.ok) {
                if (generateRandom) {
                    // Show the generated password
                    showNotification(
                        `New password for ${username}: ${data.generated_password}`,
                        'success',
                        10000 // Show it longer so there is time to copy it
                    );

                    // Automatic copying could be added here
                    navigator.clipboard.writeText(data.generated_password).then(() => {

                    });
                } else {
                    showNotification(`Password for ${username} changed successfully`, 'success');
                }

                // If needed, mark that the password must be changed at next sign-in
                const forceChange = document.getElementById('forcePasswordChange').checked;
                if (forceChange) {
                    // Logic for marking "change password at sign-in" could go here
                    // For example, add a users.force_password_change column to the database

                }

                closeModal();
            } else {
                showNotification(data.detail || 'Error changing password', 'error');
            }
        } catch (error) {

            showNotification('Network error while changing password', 'error');
        }
    };

    document.body.appendChild(modal);
    feather.replace();

    // Handler for the generation checkbox
    document.getElementById('generateRandomPassword').addEventListener('change', function() {
        const passwordInputs = document.querySelectorAll('#newPasswordInput, #confirmPasswordInput');
        passwordInputs.forEach(input => {
            input.disabled = this.checked;
            input.required = !this.checked;
        });

        if (this.checked) {
            generateRandomPassword();
        } else {
            document.getElementById('generatedPasswordContainer').classList.add('hidden');
            passwordInputs.forEach(input => {
                input.value = '';
                input.type = 'password';
            });
            feather.replace();
        }
    });
}

// Also add a function for the password reset button
function addPasswordResetButton() {
    return `
        <button onclick="resetUserPassword(${user.id}, '${user.username}')"
                class="user-action-btn btn-reset-password text-xs px-2 py-1">
            Reset password
        </button>
    `;
}

async function resetUserPassword(userId, username) {
    if (!confirm(`Reset ${username}'s password to the default (ChangeMe123!)?`)) {
        return;
    }

    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/users/${userId}/reset-password`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (response.ok) {
            showNotification(
                `Password for ${username} reset to: ${data.default_password}`,
                'warning',
                10000
            );

            navigator.clipboard.writeText(data.default_password).then(() => {

            });
        } else {
            showNotification(data.detail || 'Error resetting password', 'error');
        }
    } catch (error) {

        showNotification('Network error while resetting password', 'error');
    }
}





// Blocking and unblocking go through the account as a whole (task 229):
// a PATCH to the status used to only clear the flag, while API keys, node tokens
// and running machines stayed as they were -- a blocked user kept working.
// A reason is required: it lands in the block log and the audit, and it is
// what later explains the decision.
async function blockUserWithReason(userId, username) {
    const reason = prompt(`Why is «${username}» being blocked? The reason is recorded.`);
    if (reason === null) return;
    if (!String(reason).trim()) {
        showNotification('A reason is required', 'error');
        return;
    }
    await sendUserBlockAction(userId, 'block', reason);
}

async function unblockUserWithReason(userId, username) {
    const reason = prompt(`Why is «${username}» being unblocked? The reason is recorded.`);
    if (reason === null) return;
    if (!String(reason).trim()) {
        showNotification('A reason is required', 'error');
        return;
    }
    await sendUserBlockAction(userId, 'unblock', reason);
}

async function sendUserBlockAction(userId, action, reason) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/users/${userId}/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ reason: String(reason).trim() })
        });
        const data = await response.json();
        if (!response.ok) {
            showNotification(data.detail || 'Error changing status', 'error');
            return;
        }
        // What exactly was revoked is stated by the server: the administrator
        // must see that blocking did more than flip a flag.
        const summary = data.summary || {};
        const consequences = Object.keys(summary)
            .filter(name => summary[name])
            .map(name => `${name.replace(/_/g, ' ')}: ${summary[name]}`)
            .join(', ');
        showNotification(
            `Account ${action === 'block' ? 'blocked' : 'unblocked'}${consequences ? ' — ' + consequences : ''}`,
            'success');
        loadAllUsers();
    } catch (error) {
        showNotification('Network error', 'error');
    }
}

async function showUserBlockHistory(userId, username) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/users/${userId}/blocks`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();
        if (!response.ok) {
            showNotification(data.detail || 'Error loading the block log', 'error');
            return;
        }
        renderUserBlockHistory(username, data.blocks || []);
    } catch (error) {
        showNotification('Network error', 'error');
    }
}

function renderUserBlockHistory(username, blocks) {
    const modal = document.createElement('div');
    modal.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50';
    modal.innerHTML = `
        <div class="bg-white rounded-lg p-6 w-full max-w-2xl max-h-[80vh] overflow-y-auto">
            <h3 class="text-lg font-semibold mb-4">Block log — ${escapeUserHtml(username)}</h3>
            ${blocks.length ? `
                <table class="w-full text-sm responsive-table">
                    <thead><tr class="text-left text-gray-500 border-b">
                        <th class="py-2 pr-4">When</th><th class="py-2 pr-4">Action</th>
                        <th class="py-2 pr-4">Reason</th><th class="py-2 pr-4">Administrator</th>
                    </tr></thead>
                    <tbody>${blocks.map(entry => `
                        <tr class="border-b">
                            <td class="py-2 pr-4">${escapeUserHtml(entry.created_at)}</td>
                            <td class="py-2 pr-4">${escapeUserHtml(entry.action)}</td>
                            <td class="py-2 pr-4">${escapeUserHtml(entry.reason)}</td>
                            <td class="py-2 pr-4">${escapeUserHtml(entry.admin || entry.admin_id || '—')}</td>
                        </tr>`).join('')}
                    </tbody>
                </table>`
            : '<p class="text-gray-500">This account has never been blocked.</p>'}
            <div class="flex justify-end mt-4">
                <button class="px-4 py-2 bg-gray-200 rounded-lg" onclick="this.closest('.fixed').remove()">Close</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

function escapeUserHtml(value) {
    const holder = document.createElement('div');
    holder.textContent = value === null || value === undefined ? '' : String(value);
    return holder.innerHTML;
}

// Existing user management functions
async function toggleUserStatus(userId, newStatus) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/users/${userId}`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ status: newStatus })
        });

        const data = await response.json();

        if (response.ok) {
            showNotification(`User status changed to "${newStatus === 'active' ? 'active' : 'blocked'}"`, 'success');
            loadAllUsers();
        } else {
            showNotification(data.detail || 'Error changing status', 'error');
        }
    } catch (error) {
        showNotification('Network error', 'error');
    }
}

async function makeUserAdmin(userId) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/users/${userId}`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ role: ROLE_ADMIN })
        });

        const data = await response.json();

        if (response.ok) {
            showNotification('User is now an administrator', 'success');
            loadAllUsers();
        } else {
            showNotification(data.detail || 'Error changing role', 'error');
        }
    } catch (error) {
        showNotification('Network error', 'error');
    }
}

async function makeUserClient(userId) {
    try {
        const token = localStorage.getItem('authToken');
        const response = await fetch(`/api/admin/users/${userId}`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ role: ROLE_CLIENT })
        });

        const data = await response.json();

        if (response.ok) {
            showNotification('User is now a client', 'success');
            loadAllUsers();
        } else {
            showNotification(data.detail || 'Error changing role', 'error');
        }
    } catch (error) {
        showNotification('Network error', 'error');
    }
}

