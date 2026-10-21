// The second factor, seen by a person (keepup-176).
//
// The server has answered the API since keepup-174: a confirmed account gets
// `mfa_required` and a five-minute challenge instead of a session, and the MFA
// endpoints enrol, confirm and disable the factor. This file is the other half:
// the code field the sign-in form grows after the password, and the screen that
// puts a secret into an authenticator app and reads it back.
//
// It builds its elements rather than shipping markup in a page: the same script
// is loaded by every theme, and a theme that replaces the sign-in form still
// gets the second factor. Text is written with textContent, never spliced into
// markup.
(function () {
    'use strict';

    const state = {challenge: null, busy: false};

    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function payloadFor(value) {
        const code = String(value || '').trim();
        if (!code) return null;
        // A recovery code is the only thing people type with a dash. The
        // server accepts both fields and checks the value; this only names the
        // one the person meant.
        return /^[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$/.test(code)
            ? {recovery_code: code}
            : {code: code};
    }

    async function request(path, body) {
        const answer = await fetch(path, {
            method: body === undefined ? 'GET' : 'POST',
            headers: {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body)
        });
        let data = {};
        try {
            data = await answer.json();
        } catch (error) {
            data = {detail: answer.statusText || 'The server did not answer'};
        }
        return {ok: answer.ok, status: answer.status, data: data};
    }

    // --- the code field on the sign-in form ---------------------------------

    function challengeElements() {
        return {
            form: document.getElementById('loginForm'),
            block: document.getElementById('mfaStep'),
            input: document.getElementById('loginCode'),
            username: document.getElementById('loginUsername'),
            password: document.getElementById('loginPassword')
        };
    }

    function installChallenge() {
        const found = challengeElements();
        if (!found.form || found.block || !found.password) return null;

        const block = node('div', 'mb-6 hidden');
        block.id = 'mfaStep';
        block.appendChild(node('p', 'text-sm text-gray-600 mb-2',
            'Enter the six-digit code from your authenticator app.'));
        const input = node('input', 'w-full border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-blue-500');
        input.id = 'loginCode';
        input.type = 'text';
        input.inputMode = 'numeric';
        input.autocomplete = 'one-time-code';
        input.placeholder = '000000';
        block.appendChild(input);
        block.appendChild(node('p', 'mt-2 text-xs text-gray-500',
            'Lost the device? Enter one of your recovery codes instead.'));
        const back = node('button', 'mt-3 text-sm text-blue-600 hover:underline', 'Back to the password');
        back.type = 'button';
        back.addEventListener('click', function () {
            clearChallenge();
            if (found.username) found.username.focus();
        });
        block.appendChild(back);

        (found.password.closest('.mb-6') || found.password.parentElement)
            .insertAdjacentElement('afterend', block);
        return block;
    }

    function setSubmitLabel(text) {
        const {form} = challengeElements();
        const submit = form && form.querySelector('button[type="submit"]');
        if (submit) submit.textContent = text;
    }

    function showChallenge(token) {
        state.challenge = token || null;
        installChallenge();
        const {block, input, username, password} = challengeElements();
        if (username) (username.closest('.mb-4') || username.parentElement).classList.add('hidden');
        if (password) (password.closest('.mb-6') || password.parentElement).classList.add('hidden');
        if (block) block.classList.remove('hidden');
        setSubmitLabel('Verify');
        if (input) {
            input.value = '';
            input.focus();
        }
    }

    function clearChallenge() {
        state.challenge = null;
        const {block, input, username, password} = challengeElements();
        if (username) (username.closest('.mb-4') || username.parentElement).classList.remove('hidden');
        if (password) (password.closest('.mb-6') || password.parentElement).classList.remove('hidden');
        if (block) block.classList.add('hidden');
        if (input) input.value = '';
        setSubmitLabel('Sign in');
    }

    async function verify(value) {
        const payload = payloadFor(value);
        if (!state.challenge || !payload) {
            return {ok: false, status: 400, data: {detail: 'Enter a code first'}};
        }
        return request('/api/auth/mfa/verify', {mfa_token: state.challenge, ...payload});
    }

    // --- the account screen -------------------------------------------------

    let modal = null;

    function buildModal() {
        if (modal) return modal;

        const overlay = node('div', 'modal-overlay hidden');
        overlay.id = 'mfaModal';
        const content = node('div', 'modal-content');
        const header = node('div', 'modal-header');
        header.appendChild(node('h2', 'modal-title', 'Two-factor authentication'));
        const close = node('button', 'close-btn');
        close.type = 'button';
        close.setAttribute('aria-label', 'Close');
        const icon = node('i');
        icon.setAttribute('data-feather', 'x');
        close.appendChild(icon);
        close.addEventListener('click', closeModal);
        header.appendChild(close);
        content.appendChild(header);

        const body = node('div', 'modal-body');

        const status = node('p', 'text-sm text-gray-600 mb-4');
        status.id = 'mfaStatus';
        body.appendChild(status);

        const enabled = node('div', 'hidden');
        enabled.id = 'mfaEnabled';
        enabled.appendChild(node('p', 'text-sm text-gray-600 mb-3',
            'A code from your authenticator is required when you sign in.'));
        const disableCode = codeInput('mfaDisableCode', 'Code from the app');
        enabled.appendChild(disableCode);
        const disable = node('button', 'mt-3 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700', 'Disable');
        disable.type = 'button';
        disable.addEventListener('click', disableFactor);
        enabled.appendChild(disable);
        body.appendChild(enabled);

        const disabled = node('div', 'hidden');
        disabled.id = 'mfaDisabled';
        disabled.appendChild(node('p', 'text-sm text-gray-600 mb-3',
            'Add a second step to your sign-in. You will need an authenticator app.'));
        const start = node('button', 'px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700', 'Set up');
        start.type = 'button';
        start.addEventListener('click', startFactor);
        disabled.appendChild(start);
        body.appendChild(disabled);

        const setup = node('div', 'hidden');
        setup.id = 'mfaSetup';
        setup.appendChild(node('p', 'text-sm text-gray-600 mb-2',
            'Type this secret into your authenticator app, or open the address in it.'));
        const secret = node('code', 'block break-all bg-gray-100 rounded p-3 mb-2');
        secret.id = 'mfaSecret';
        setup.appendChild(secret);
        const uri = node('code', 'block break-all text-xs bg-gray-100 rounded p-3 mb-3');
        uri.id = 'mfaUri';
        setup.appendChild(uri);
        setup.appendChild(codeInput('mfaConfirmCode', 'Code from the app'));
        const confirm = node('button', 'mt-3 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700', 'Confirm');
        confirm.type = 'button';
        confirm.addEventListener('click', confirmFactor);
        setup.appendChild(confirm);
        body.appendChild(setup);

        const recovery = node('div', 'hidden mt-4');
        recovery.id = 'mfaRecovery';
        recovery.appendChild(node('h3', 'font-medium text-gray-800 mb-2', 'Recovery codes'));
        recovery.appendChild(node('p', 'text-sm text-gray-600 mb-2',
            'Keep these somewhere safe. Each works once, and they will not be shown again.'));
        const list = node('ul', 'font-mono text-sm bg-gray-100 rounded p-3 space-y-1');
        list.id = 'mfaRecoveryList';
        recovery.appendChild(list);
        body.appendChild(recovery);

        const message = node('p', 'mt-4 text-sm');
        message.id = 'mfaMessage';
        body.appendChild(message);
        content.appendChild(body);
        overlay.appendChild(content);
        overlay.addEventListener('click', function (event) {
            if (event.target === overlay) closeModal();
        });
        document.body.appendChild(overlay);
        modal = overlay;
        return overlay;
    }

    function codeInput(id, placeholder) {
        const input = node('input', 'w-full border border-gray-300 rounded-lg px-4 py-3 focus:outline-none focus:ring-2 focus:ring-blue-500');
        input.id = id;
        input.type = 'text';
        input.autocomplete = 'one-time-code';
        input.placeholder = placeholder || '000000';
        return input;
    }

    function say(text, tone) {
        const message = document.getElementById('mfaMessage');
        if (!message) return;
        message.textContent = text || '';
        message.className = 'mt-4 text-sm ' + (tone === 'error' ? 'text-red-600' : 'text-green-700');
    }

    function show(id, visible) {
        const element = document.getElementById(id);
        if (element) element.classList.toggle('hidden', !visible);
    }

    function openModal() {
        buildModal();
        modal.classList.remove('hidden');
        say('', 'ok');
        refreshStatus();
        if (window.feather && window.feather.replace) window.feather.replace();
    }

    function closeModal() {
        if (modal) modal.classList.add('hidden');
    }

    async function refreshStatus() {
        const answer = await request('/api/auth/mfa');
        const status = document.getElementById('mfaStatus');
        const enabled = answer.ok && answer.data.enabled;
        if (status) status.textContent = enabled
            ? 'Two-factor authentication is on.'
            : 'Two-factor authentication is off.';
        show('mfaEnabled', enabled);
        show('mfaDisabled', !enabled);
        if (!enabled) show('mfaSetup', false);
    }

    async function startFactor() {
        const answer = await request('/api/auth/mfa/totp/start', {});
        if (!answer.ok) return say(answer.data.detail || 'Could not start setup', 'error');
        document.getElementById('mfaSecret').textContent = answer.data.secret || '';
        document.getElementById('mfaUri').textContent = answer.data.otpauth_uri || '';
        show('mfaSetup', true);
        show('mfaDisabled', false);
        show('mfaRecovery', false);
        say('Scan the address, then enter the code it shows.', 'ok');
    }

    async function confirmFactor() {
        const code = document.getElementById('mfaConfirmCode');
        const answer = await request('/api/auth/mfa/totp/confirm', {code: code ? code.value : ''});
        if (!answer.ok) return say(answer.data.detail || 'That code is not right', 'error');
        const list = document.getElementById('mfaRecoveryList');
        list.replaceChildren(...(answer.data.recovery_codes || []).map(value => {
            const item = node('li', '', value);
            return item;
        }));
        show('mfaSetup', false);
        show('mfaRecovery', true);
        show('mfaEnabled', true);
        show('mfaDisabled', false);
        const status = document.getElementById('mfaStatus');
        if (status) status.textContent = 'Two-factor authentication is on.';
        say('Two-factor authentication is on. Save the recovery codes now.', 'ok');
    }

    async function disableFactor() {
        const code = document.getElementById('mfaDisableCode');
        const answer = await request('/api/auth/mfa/totp/disable', {code: code ? code.value : ''});
        if (!answer.ok) return say(answer.data.detail || 'That code is not right', 'error');
        show('mfaRecovery', false);
        say('Two-factor authentication is off.', 'ok');
        refreshStatus();
    }

    function installAccountButton() {
        const logout = document.querySelector('[data-action="shell.logout"]');
        if (!logout || document.getElementById('mfaOpen')) return;
        const button = node('button', 'ml-2 p-2 text-gray-600 rounded-full hover:bg-gray-100 hover:text-blue-600 transition-colors duration-150');
        button.id = 'mfaOpen';
        button.type = 'button';
        button.title = 'Two-factor authentication';
        button.setAttribute('aria-label', 'Two-factor authentication');
        const icon = node('i');
        icon.setAttribute('data-feather', 'shield');
        button.appendChild(icon);
        button.addEventListener('click', openModal);
        logout.parentElement.insertBefore(button, logout);
        if (window.feather && window.feather.replace) window.feather.replace();
    }

    function install() {
        installChallenge();
        installAccountButton();
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && modal && !modal.classList.contains('hidden')) closeModal();
        });
    }

    window.MfaPanel = {
        install: install,
        payloadFor: payloadFor,
        verify: verify,
        showChallenge: showChallenge,
        clearChallenge: clearChallenge,
        challengeToken: () => state.challenge,
        open: openModal,
        close: closeModal
    };

    // The shell calls install() once the page is ready: both pages that load
    // this file also load main_new.js, and a second listener would install the
    // second factor twice (keepup-176).
})();
