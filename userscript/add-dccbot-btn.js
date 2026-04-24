// ==UserScript==
// @name         add-dccbot-btn
// @namespace    https://github.com/luni/dccbot/
// @website      https://github.com/luni/dccbot/
// @version      2026-03-24
// @description  Add button for DCCbot to automate downloads.
// @author       luni
// @match        https://www.xdcc.eu/search.php*
// @match        https://nibl.co.uk/*
// @match        https://xdcc.animk.info/*
// @match        https://xdcc.rocks/*
// @match        https://www.xdcc.rocks/*
// @match        https://xdcc-search.com/*
// @downloadURL  https://raw.githubusercontent.com/luni/dccbot/refs/heads/main/userscript/add-dccbot-btn.js
// @connect      *
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @grant        GM_getValue
// @grant        GM_setValue
// ==/UserScript==

(function () {
    'use strict';

    // Prompt user to set API endpoint
    GM_registerMenuCommand("Set API Endpoint", function () {
        const current = GM_getValue("dccbot_api", "");
        const endpoint = prompt("Enter DCCBot API endpoint:", current);
        if (endpoint !== null) {
            GM_setValue("dccbot_api", endpoint);
            alert("API endpoint saved: " + endpoint);
        }
    });

    // Usage: fetch endpoint whenever needed
    const dccbot_api = GM_getValue("dccbot_api", "");
    if (!dccbot_api) {
        console.log("No API endpoint for DCCBot defined.");
        return;
    }

    function normalizeApiBase(input) {
        let url = (input || '').trim();
        if (!url) return '';
        if (!/^https?:\/\//i.test(url)) {
            url = 'http://' + url;
        }
        url = url.replace(/\/+$/, '');
        // Allow users to paste a UI URL and still derive API base.
        url = url.replace(/\/(?:index\.html|log\.html|info\.html)$/i, '');
        return url;
    }

    const dccbot_api_base = normalizeApiBase(dccbot_api);
    console.log("API Endpoint for DCCBot: " + dccbot_api_base);

    function buildApiUrl(path) {
        const normalizedPath = String(path || '').startsWith('/') ? path : ('/' + path);
        return dccbot_api_base + normalizedPath;
    }

    function buildWsUrl() {
        const parsed = new URL(dccbot_api_base);
        const protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
        const basePath = parsed.pathname.endsWith('/') ? parsed.pathname : (parsed.pathname + '/');
        const wsPath = new URL('ws', 'http://dummy' + basePath).pathname;
        return protocol + '//' + parsed.host + wsPath;
    }

    function isMixedContentBlocked() {
        try {
            const endpoint = new URL(dccbot_api_base);
            return window.location.protocol === 'https:' && endpoint.protocol === 'http:';
        } catch (e) {
            return false;
        }
    }

    const BUTTON_STYLES = {
        default: {
            display: 'inline-block',
            cursor: 'pointer',
            padding: '5px 10px',
            background: 'linear-gradient(180deg, #2ecb70 0%, #1f9f56 100%)',
            color: 'white',
            border: '1px solid #187f44',
            borderRadius: '5px',
            fontWeight: '700',
            letterSpacing: '0.2px',
            textShadow: '0 1px 0 rgba(0,0,0,0.25)',
            boxShadow: '0 1px 3px rgba(0,0,0,0.25)',
            userSelect: 'none',
        },
        xdccSearchHeader: {
            padding: '8px',
            marginLeft: '8px'
        }
    };

    function normalizeChannel(channel) {
        if (!channel) return '';
        const trimmed = channel.trim();
        return trimmed.startsWith('#') ? trimmed : ('#' + trimmed);
    }

    function extractPackNumber(value) {
        return (value || '').toString().replace(/[^\d]/g, '');
    }

    function parseIrcTarget(href) {
        if (!href) return { server: null, channel: null };
        const raw = href.trim();

        // Handles forms like irc://server/#channel and irc://server/channel
        const parsed = raw.match(/^irc:\/\/([^/]+)\/(?:#)?([^/?#]+)$/i);
        if (parsed) {
            return {
                server: (parsed[1] || '').trim() || null,
                channel: normalizeChannel(parsed[2] || '')
            };
        }

        // Fallback for less common URL shapes.
        try {
            const url = new URL(raw);
            const channel = (url.hash || url.pathname || '').replace(/^\//, '').replace(/^#/, '');
            return {
                server: url.hostname || null,
                channel: normalizeChannel(channel)
            };
        } catch (e) {
            return { server: null, channel: null };
        }
    }

    function observeMutations(target, onMutation, options) {
        if (!target) return null;
        const observer = new MutationObserver(onMutation);
        observer.observe(target, options || { childList: true, subtree: true });
        return observer;
    }

    let noticeContainer = null;

    function getNoticeContainer() {
        if (noticeContainer && noticeContainer.isConnected) return noticeContainer;
        noticeContainer = document.createElement('div');
        noticeContainer.style.position = 'fixed';
        noticeContainer.style.right = '12px';
        noticeContainer.style.bottom = '12px';
        noticeContainer.style.zIndex = '2147483647';
        noticeContainer.style.display = 'flex';
        noticeContainer.style.flexDirection = 'column';
        noticeContainer.style.gap = '6px';
        noticeContainer.style.maxWidth = 'min(420px, 90vw)';
        document.body.appendChild(noticeContainer);
        return noticeContainer;
    }

    function showNotice(message, kind, timeoutMs) {
        if (!message) return;
        const container = getNoticeContainer();
        const note = document.createElement('div');
        note.textContent = message;
        note.style.padding = '8px 10px';
        note.style.borderRadius = '6px';
        note.style.fontSize = '12px';
        note.style.lineHeight = '1.35';
        note.style.wordBreak = 'break-word';
        note.style.boxShadow = '0 4px 14px rgba(0,0,0,0.22)';
        note.style.color = '#fff';
        if (kind === 'error') {
            note.style.background = '#b81d13';
        } else if (kind === 'success') {
            note.style.background = '#227a34';
        } else {
            note.style.background = '#2e3238';
        }
        container.appendChild(note);
        setTimeout(function () {
            note.remove();
            if (container.childElementCount === 0) {
                container.remove();
            }
        }, timeoutMs || 5000);
    }

    function formatMsgSummary(server, channel, user, message) {
        return [server, channel, user, message].filter(Boolean).join(' | ');
    }

    function trimResponseText(text, maxLen) {
        const clean = (text || '').replace(/\s+/g, ' ').trim();
        if (clean.length <= maxLen) return clean;
        return clean.slice(0, maxLen) + '...';
    }

    function handleDccbotButtonClick(evt) {
        evt.preventDefault();
        if (evt.stopImmediatePropagation) evt.stopImmediatePropagation();
        evt.stopPropagation();
        const d = this.dataset;
        const btn = this;
        if (btn.getAttribute('aria-disabled') === 'true') return;
        setDownloadButtonBusy(btn, true);
        btn.style.background = '#6f7782';
        btn.style.borderColor = '#5b616a';
        btn.textContent = 'Sending...';
        send_msg(d.server, d.channel, d.bot, "xdcc send #" + d.pack, function (ok) {
            btn.style.background = ok ? '#227a34' : '#b81d13';
            btn.style.borderColor = ok ? '#1b612a' : '#8f160f';
            btn.textContent = ok ? 'Sent' : 'Retry';
            setDownloadButtonBusy(btn, false);
        });
    }

    function setDownloadButtonBusy(btn, busy) {
        if ('disabled' in btn) {
            btn.disabled = busy;
        }
        btn.setAttribute('aria-disabled', busy ? 'true' : 'false');
        btn.style.pointerEvents = busy ? 'none' : 'auto';
        btn.style.opacity = busy ? '0.9' : '1';
    }

    function applyStyles(element, defaultStyle, styleOverrides) {
        const styles = Object.assign({}, defaultStyle, styleOverrides || {});
        Object.keys(styles).forEach(function (key) {
            element.style[key] = styles[key];
        });
    }

    function get_download_btn(server, channel, botname, packnum, styleOverrides) {
        const btn = document.createElement('span');
        btn.className = 'dccbot-btn';
        btn.textContent = 'Down';
        btn.setAttribute('role', 'button');
        btn.setAttribute('tabindex', '0');

        applyStyles(btn, BUTTON_STYLES.default, styleOverrides);
        configure_dccbot_btn(btn, server, channel, botname, packnum);
        return btn;
    }

    function configure_dccbot_btn(btn, server, channel, botname, packnum) {
        btn.dataset.server = server;
        btn.dataset.channel = channel;
        btn.dataset.bot = botname;
        btn.dataset.pack = packnum;
        btn.onclick = handleDccbotButtonClick;
        btn.onkeydown = function (evt) {
            if (evt.key === 'Enter' || evt.key === ' ') {
                handleDccbotButtonClick.call(btn, evt);
            }
        };
        if (!('disabled' in btn)) {
            btn.setAttribute('role', 'button');
            btn.setAttribute('tabindex', '0');
        }
        setDownloadButtonBusy(btn, false);
    }

    function send_msg(server, channel, user, message, done) {
        const summary = formatMsgSummary(server, channel, user, message);
        GM_xmlhttpRequest({
            method: "POST",
            url: buildApiUrl('/msg'),
            headers: {
                "Content-Type": "application/json"
            },
            data: JSON.stringify({
                server: server,
                channel: channel,
                user: user,
                message: message
            }),
            onload: function (response) {
                const text = trimResponseText(response.responseText, 220);
                const ok = response.status >= 200 && response.status < 300;
                console.log("[DCCBOT] API Response (" + response.status + "): " + response.responseText);
                showNotice('[DCCBOT] ' + (ok ? 'Sent' : 'Failed') + ': ' + summary + ' -> [' + response.status + '] ' + (text || '(empty response)'), ok ? 'success' : 'error', ok ? 5500 : 9000);
                if (done) done(ok, response);
            },
            onerror: function (response) {
                const details = response && response.error ? response.error : 'network error';
                showNotice('[DCCBOT] Failed: ' + summary + ' -> ' + details, 'error', 9000);
                if (done) done(false, response);
            },
            ontimeout: function (response) {
                showNotice('[DCCBOT] Failed: ' + summary + ' -> request timeout', 'error', 9000);
                if (done) done(false, response);
            }
        });
    }

    let transferOverlay = null;
    let transferBody = null;
    let transferStatusLine = null;
    const TRANSFER_OVERLAY_COLLAPSED_KEY = 'dccbot.transfer_overlay_collapsed';
    let transferOverlayCollapsed = false;
    let transferSocket = null;
    let transferReconnectTimer = null;
    let transferPollTimer = null;
    let transferUsingPollingFallback = false;
    const transferStateByKey = {};

    function isTerminalTransferStatus(status) {
        return ['completed', 'failed', 'error', 'cancelled'].indexOf(status) !== -1;
    }

    function transferKey(t) {
        return [t.server || '', t.nick || '', t.filename || ''].join('|');
    }

    function loadTransferOverlayCollapsed() {
        try {
            const stored = window.localStorage.getItem(TRANSFER_OVERLAY_COLLAPSED_KEY);
            if (stored === null) return true;
            return stored === '1';
        } catch (e) {
            return true;
        }
    }

    function saveTransferOverlayCollapsed(collapsed) {
        try {
            window.localStorage.setItem(TRANSFER_OVERLAY_COLLAPSED_KEY, collapsed ? '1' : '0');
        } catch (e) {
            // Ignore storage failures (private mode / restrictive policies).
        }
    }

    function applyTransferOverlayCollapsedState(overlay, content, header, title) {
        const collapsed = Boolean(transferOverlayCollapsed);
        overlay.style.width = collapsed ? 'auto' : 'min(360px, 92vw)';
        content.style.display = collapsed ? 'none' : 'flex';
        title.style.display = collapsed ? 'none' : 'block';
        header.style.justifyContent = collapsed ? 'flex-end' : 'space-between';
    }

    function truncateWithEllipsis(text, maxLength) {
        const safeText = String(text || '');
        if (!maxLength || safeText.length <= maxLength) return safeText;
        if (maxLength <= 3) return '.'.repeat(maxLength);
        return safeText.slice(0, maxLength - 3) + '...';
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value >= 1024 * 1024 * 1024) return (value / 1024 / 1024 / 1024).toFixed(2) + ' GB';
        if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(2) + ' MB';
        if (value >= 1024) return (value / 1024).toFixed(2) + ' KB';
        return value.toFixed(0) + ' B';
    }

    function formatSpeedFromKbps(kbps) {
        const value = Number(kbps) || 0;
        if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(2) + ' GB/s';
        if (value >= 1024) return (value / 1024).toFixed(2) + ' MB/s';
        return value.toFixed(2) + ' KB/s';
    }

    function relativeNowTs() {
        return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function addTransferEvent(text, kind) {
        const level = kind || 'info';
        const timeoutMs = level === 'error' ? 9000 : 5500;
        showNotice('[DCCBOT] ' + text, level, timeoutMs);
    }

    function ensureTransferOverlay() {
        if (transferOverlay && transferOverlay.isConnected) return transferOverlay;
        transferOverlayCollapsed = loadTransferOverlayCollapsed();
        transferOverlay = document.createElement('div');
        transferOverlay.style.position = 'fixed';
        transferOverlay.style.top = '12px';
        transferOverlay.style.right = '12px';
        transferOverlay.style.zIndex = '2147483646';
        transferOverlay.style.width = 'min(360px, 92vw)';
        transferOverlay.style.background = 'rgba(22, 25, 30, 0.95)';
        transferOverlay.style.color = '#eef2f5';
        transferOverlay.style.border = '1px solid rgba(255,255,255,0.15)';
        transferOverlay.style.borderRadius = '8px';
        transferOverlay.style.boxShadow = '0 10px 30px rgba(0,0,0,0.35)';
        transferOverlay.style.backdropFilter = 'blur(3px)';
        transferOverlay.style.fontFamily = 'system-ui, -apple-system, Segoe UI, sans-serif';
        transferOverlay.style.fontSize = '12px';
        transferOverlay.style.lineHeight = '1.35';

        const header = document.createElement('div');
        header.style.display = 'flex';
        header.style.justifyContent = 'space-between';
        header.style.alignItems = 'center';
        header.style.padding = '8px 10px';
        header.style.cursor = 'pointer';
        header.style.borderBottom = '1px solid rgba(255,255,255,0.12)';

        const title = document.createElement('a');
        title.textContent = 'DCCBot Transfers';
        title.href = dccbot_api_base;
        title.target = '_blank';
        title.rel = 'noopener noreferrer';
        title.title = 'Open DCCBot';
        title.style.color = 'inherit';
        title.style.textDecoration = 'none';
        title.style.fontWeight = '700';
        title.style.letterSpacing = '0.2px';
        title.addEventListener('click', function (evt) {
            evt.stopPropagation();
        });
        header.appendChild(title);

        transferStatusLine = document.createElement('div');
        transferStatusLine.textContent = '...';
        transferStatusLine.style.color = '#9fb2bf';
        transferStatusLine.style.fontSize = '11px';
        header.appendChild(transferStatusLine);

        const content = document.createElement('div');
        content.style.padding = '8px 10px 10px 10px';
        content.style.display = 'flex';
        content.style.flexDirection = 'column';
        content.style.gap = '8px';

        transferBody = document.createElement('div');
        transferBody.style.display = 'flex';
        transferBody.style.flexDirection = 'column';
        transferBody.style.gap = '6px';
        content.appendChild(transferBody);

        applyTransferOverlayCollapsedState(transferOverlay, content, header, title);
        header.addEventListener('click', function () {
            transferOverlayCollapsed = !transferOverlayCollapsed;
            applyTransferOverlayCollapsedState(transferOverlay, content, header, title);
            saveTransferOverlayCollapsed(transferOverlayCollapsed);
        });

        transferOverlay.appendChild(header);
        transferOverlay.appendChild(content);
        document.body.appendChild(transferOverlay);
        return transferOverlay;
    }

    function renderTransferOverlay(transfers) {
        ensureTransferOverlay();

        const rows = Array.isArray(transfers) ? transfers : [];
        const active = rows.filter(function (t) {
            return t && (t.status === 'in_progress' || t.status === 'started');
        });
        const totalSize = active.reduce(function (sum, t) {
            const size = Number(t.size) || 0;
            return size > 0 ? (sum + size) : sum;
        }, 0);
        const totalReceived = active.reduce(function (sum, t) {
            const received = Number(t.received) || 0;
            return received > 0 ? (sum + received) : sum;
        }, 0);
        const totalSpeedKbps = active.reduce(function (sum, t) {
            const speed = Number(t.speed) || 0;
            return speed > 0 ? (sum + speed) : sum;
        }, 0);
        const totalPercent = totalSize > 0
            ? Math.max(0, Math.min(100, Math.round((totalReceived / totalSize) * 100)))
            : 0;
        const activityIcon = active.length > 0 ? '⬇' : '○';
        const activityColor = active.length > 0 ? '#37a169' : '#9fb2bf';
        const rightSide = active.length > 0 ? formatSpeedFromKbps(totalSpeedKbps) : relativeNowTs();
        const statusDetail = active.length + ' (' + totalPercent + '%) • ' + rightSide;
        transferStatusLine.innerHTML = '<span style="color:' + activityColor + ';">' + activityIcon + '</span> ' + statusDetail;

        transferBody.innerHTML = '';
        if (active.length === 0) {
            const empty = document.createElement('div');
            empty.textContent = 'No active transfers';
            empty.style.color = '#aab5bf';
            transferBody.appendChild(empty);
        } else {
            active.slice(0, 4).forEach(function (t) {
                const size = Number(t.size) || 0;
                const received = Number(t.received) || 0;
                const percent = size > 0 ? Math.max(0, Math.min(100, Math.round((received / size) * 100))) : 0;

                const row = document.createElement('div');
                row.style.padding = '6px 7px';
                row.style.border = '1px solid rgba(255,255,255,0.12)';
                row.style.borderRadius = '6px';
                row.style.background = 'rgba(255,255,255,0.03)';

                const name = document.createElement('div');
                const fullFilename = t.filename || 'unknown file';
                name.textContent = truncateWithEllipsis(fullFilename, 42);
                name.title = fullFilename;
                name.style.fontWeight = '600';
                row.appendChild(name);

                const meta = document.createElement('div');
                meta.textContent = (t.nick || '?') + ' @ ' + (t.server || '?');
                meta.style.color = '#9fb2bf';
                meta.style.fontSize = '11px';
                row.appendChild(meta);

                const details = document.createElement('div');
                details.textContent = percent + '% • ' + formatBytes(received) + ' / ' + formatBytes(size) + ' • ' + Number(t.speed || 0).toFixed(2) + ' KB/s';
                details.style.marginTop = '2px';
                row.appendChild(details);

                const barRow = document.createElement('div');
                barRow.style.marginTop = '6px';
                barRow.style.display = 'flex';
                barRow.style.alignItems = 'center';
                barRow.style.gap = '6px';

                const bar = document.createElement('div');
                bar.style.flex = '1';
                bar.style.height = '6px';
                bar.style.background = 'rgba(255,255,255,0.15)';
                bar.style.borderRadius = '3px';
                bar.style.overflow = 'hidden';

                const fill = document.createElement('div');
                fill.style.height = '100%';
                fill.style.width = percent + '%';
                fill.style.background = '#37a169';
                fill.style.transition = 'width 0.4s ease';
                bar.appendChild(fill);
                barRow.appendChild(bar);

                const cancelBtn = document.createElement('button');
                cancelBtn.textContent = 'X';
                cancelBtn.title = 'Cancel transfer';
                cancelBtn.setAttribute('aria-label', 'Cancel transfer');
                cancelBtn.style.background = '#b81d13';
                cancelBtn.style.color = '#fff';
                cancelBtn.style.border = 'none';
                cancelBtn.style.borderRadius = '999px';
                cancelBtn.style.width = '20px';
                cancelBtn.style.height = '20px';
                cancelBtn.style.lineHeight = '18px';
                cancelBtn.style.padding = '0';
                cancelBtn.style.textAlign = 'center';
                cancelBtn.style.cursor = 'pointer';
                cancelBtn.style.fontSize = '12px';
                cancelBtn.style.fontWeight = '700';
                cancelBtn.addEventListener('click', function (evt) {
                    evt.preventDefault();
                    evt.stopPropagation();
                    cancelBtn.disabled = true;
                    cancelBtn.textContent = '...';
                    cancelBtn.style.opacity = '0.8';
                    cancelTransfer(t.server, t.nick, t.filename, function (ok) {
                        if (ok) {
                            cancelBtn.textContent = '✓';
                        } else {
                            cancelBtn.disabled = false;
                            cancelBtn.textContent = 'X';
                            cancelBtn.style.opacity = '1';
                        }
                    });
                });
                barRow.appendChild(cancelBtn);
                row.appendChild(barRow);

                transferBody.appendChild(row);
            });
        }

    }

    function processTransferEvents(transfers) {
        const currentByKey = {};
        (transfers || []).forEach(function (t) {
            if (!t || !t.filename) return;
            const key = transferKey(t);
            const status = (t.status || 'unknown').toLowerCase();
            currentByKey[key] = status;

            const prev = transferStateByKey[key];
            if (!prev && (status === 'in_progress' || status === 'started')) {
                addTransferEvent('Started: ' + t.filename + ' (' + (t.nick || '?') + ')', 'info');
            } else if (prev && prev !== status && isTerminalTransferStatus(status)) {
                const kind = status === 'completed' ? 'success' : 'error';
                addTransferEvent((status === 'completed' ? 'Finished: ' : 'Ended (' + status + '): ') + t.filename + ' (' + (t.nick || '?') + ')', kind);
            }
        });

        Object.keys(transferStateByKey).forEach(function (key) {
            delete transferStateByKey[key];
        });
        Object.keys(currentByKey).forEach(function (key) {
            transferStateByKey[key] = currentByKey[key];
        });
    }

    function scheduleTransferReconnect() {
        if (transferUsingPollingFallback) return;
        if (transferReconnectTimer) return;
        transferReconnectTimer = setTimeout(function () {
            transferReconnectTimer = null;
            startTransferStream();
        }, 2000);
    }

    function startTransferPollingFallback(reasonText) {
        transferUsingPollingFallback = true;
        if (transferReconnectTimer) {
            clearTimeout(transferReconnectTimer);
            transferReconnectTimer = null;
        }
        if (transferStatusLine) {
            transferStatusLine.textContent = reasonText || 'fallback • polling /info';
        }
        pollTransferInfo();
        if (transferPollTimer) clearInterval(transferPollTimer);
        transferPollTimer = setInterval(pollTransferInfo, 3000);
    }

    function pollTransferInfo() {
        GM_xmlhttpRequest({
            method: "GET",
            url: buildApiUrl('/info'),
            onload: function (response) {
                if (!response || response.status < 200 || response.status >= 300) {
                    if (transferStatusLine) transferStatusLine.textContent = 'offline • HTTP ' + (response ? response.status : '?');
                    return;
                }
                let payload = null;
                try {
                    payload = JSON.parse(response.responseText || '{}');
                } catch (e) {
                    if (transferStatusLine) transferStatusLine.textContent = 'invalid /info response';
                    return;
                }
                const transfers = Array.isArray(payload.transfers) ? payload.transfers : [];
                processTransferEvents(transfers);
                renderTransferOverlay(transfers);
            },
            onerror: function () {
                if (transferStatusLine) transferStatusLine.textContent = 'offline • network error';
            },
            ontimeout: function () {
                if (transferStatusLine) transferStatusLine.textContent = 'offline • timeout';
            }
        });
    }

    function startTransferStream() {
        ensureTransferOverlay();
        if (transferUsingPollingFallback) return;
        if (isMixedContentBlocked()) {
            startTransferPollingFallback('fallback • mixed content (https page + http endpoint)');
            return;
        }
        if (transferSocket && (transferSocket.readyState === WebSocket.OPEN || transferSocket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        let wsUrl;
        try {
            wsUrl = buildWsUrl();
            transferSocket = new WebSocket(wsUrl);
        } catch (e) {
            startTransferPollingFallback('fallback • websocket init failed');
            return;
        }

        transferStatusLine.textContent = 'connecting...';

        transferSocket.addEventListener('open', function () {
            transferStatusLine.textContent = 'connected • ' + relativeNowTs();
        });

        transferSocket.addEventListener('message', function (event) {
            if (!event || !event.data) return;
            let payload = null;
            try {
                payload = JSON.parse(event.data);
            } catch (e) {
                return;
            }
            if (!payload || payload.type !== 'transfers') return;

            const transfers = Array.isArray(payload.transfers) ? payload.transfers : [];
            processTransferEvents(transfers);
            renderTransferOverlay(transfers);
        });

        transferSocket.addEventListener('close', function () {
            transferStatusLine.textContent = 'offline • reconnecting';
            transferSocket = null;
            scheduleTransferReconnect();
        });

        transferSocket.addEventListener('error', function () {
            transferStatusLine.textContent = 'offline • websocket error';
        });
    }

    function initTransferOverlay() {
        ensureTransferOverlay();
        startTransferStream();
    }

    function cancelTransfer(server, nick, filename, done) {
        GM_xmlhttpRequest({
            method: "POST",
            url: buildApiUrl('/cancel'),
            headers: {
                "Content-Type": "application/json"
            },
            data: JSON.stringify({
                server: server,
                nick: nick,
                filename: filename
            }),
            onload: function (response) {
                let message = '';
                try {
                    const payload = JSON.parse(response.responseText || '{}');
                    message = payload.message || '';
                } catch (e) {
                    message = trimResponseText(response.responseText, 180);
                }
                const ok = response.status >= 200 && response.status < 300;
                showNotice('[DCCBOT] ' + (ok ? 'Cancel requested' : 'Cancel failed') + ': ' + filename + ' -> [' + response.status + '] ' + (message || '(empty response)'), ok ? 'success' : 'error', ok ? 5500 : 9000);
                if (done) done(ok, response);
            },
            onerror: function (response) {
                const details = response && response.error ? response.error : 'network error';
                showNotice('[DCCBOT] Cancel failed: ' + filename + ' -> ' + details, 'error', 9000);
                if (done) done(false, response);
            },
            ontimeout: function (response) {
                showNotice('[DCCBOT] Cancel failed: ' + filename + ' -> request timeout', 'error', 9000);
                if (done) done(false, response);
            }
        });
    }

    function mapNetworkToServer(networkName) {
        if (!networkName) {
            return "irc.rizon.net";
        }
        const n = networkName.trim().toLowerCase();
        if (n === "rizon") return "irc.rizon.net";
        if (n === "abjects") return "irc.abjects.net";
        if (n === "scenep2p") return "irc.scenep2p.net";
        if (n === "coreirc") return "irc.coreirc.net";
        if (n === "abandoned-irc") return "irc.abandoned-irc.net";
        if (n === "pureirc") return "irc.pureirc.net";
        if (n === "terrachat") return "irc.terrachat.cl";
        const cleaned = n.replace(/[^a-z0-9]+/g, "");
        return "irc." + cleaned + ".net";
    }

    function add_button_xdcc_eu() {
        const mainContainer = document.getElementsByClassName('container')[0];
        const mainColumn = document.getElementsByClassName('twelve')[0];
        if (mainContainer) {
            mainContainer.style.maxWidth = "100%";
            mainContainer.style.width = "90%";
        }
        if (mainColumn) {
            mainColumn.style.marginTop = 0;
        }

        const all_results = [];
        const rows = document.querySelectorAll('#table tbody tr');
        for (const x of rows) {
            const cells = x.querySelectorAll('td');
            if (cells.length < 4) continue;

            let server = null;
            let channel = null;

            const infoLink = cells[1].querySelector('a[data-s][data-c]');
            if (infoLink) {
                server = (infoLink.dataset.s || '').trim();
                channel = normalizeChannel(infoLink.dataset.c || '');
            }

            if (!server || !channel) {
                const ircLink = cells[1].querySelector('a[href^="irc://"]');
                if (ircLink) {
                    const meta = parseIrcTarget(ircLink.getAttribute('href') || ircLink.href || '');
                    server = server || meta.server;
                    channel = channel || meta.channel;
                }
            }

            const botname = cells[2].textContent.trim();
            const packnum = extractPackNumber(cells[3].textContent);
            if (!server || !channel || !botname || !packnum) continue;
            all_results.push([server, channel, botname, packnum].join(';'));

            if (x.querySelector('.dccbot-btn')) continue;

            const btnCell = document.createElement('td');
            const btn = get_download_btn(server, channel, botname, packnum);
            btnCell.appendChild(btn);
            x.appendChild(btnCell);
        }

        const h4 = document.getElementsByTagName('h4')[0];
        if (h4) {
            h4.onclick = function () {
                const msg = document.getElementById('msg');
                if (msg) {
                    msg.textContent = '';
                    const textarea = document.createElement('textarea');
                    textarea.style.width = '100%';
                    textarea.rows = 8;
                    textarea.value = all_results.join('\n');
                    msg.appendChild(textarea);
                }
            };
        }
    }

    function add_button_nibl() {
        function handleNiblButtonClick(e) {
            e.preventDefault();
            const botpack = this.dataset.botpack,
                botname = this.dataset.botname;
            send_msg("irc.rizon.net", "#nibl", botname, "xdcc send " + botpack);

        }

        for (const copy_btn of document.querySelectorAll("button.copy-data")) {
            copy_btn.className = copy_btn.className.replace('copy-data ', '');
            copy_btn.innerHTML = 'Down';
            copy_btn.onclick = handleNiblButtonClick;
        }

        const copy_batch_btn = document.getElementById('copy-as-batch');
        if (copy_batch_btn) {
            copy_batch_btn.innerHTML = 'Download selected';
            copy_batch_btn.onclick = function (e) {
                e.preventDefault();
                const bots = {};
                for (const ckbox of document.querySelectorAll("input[name='batch']")) {
                    if (ckbox.checked) {
                        if (!bots[ckbox.dataset.botname]) {
                            bots[ckbox.dataset.botname] = [];
                        }
                        bots[ckbox.dataset.botname].push(ckbox.dataset.botpack);
                    }
                }

                for (const botname in bots) {
                    send_msg("irc.rizon.net", "#nibl", botname, "xdcc batch " + bots[botname].join(','));
                }
            };
        }
    }

    function add_button_animk_info() {
        let processScheduled = false;

        function scheduleProcess() {
            if (processScheduled) return;
            processScheduled = true;
            requestAnimationFrame(function () {
                processScheduled = false;
                processAnimkRows();
            });
        }

        function attachObserver() {
            const tableBody = document.querySelector('#listtable');
            if (!tableBody) {
                setTimeout(attachObserver, 300);
                return;
            }

            observeMutations(tableBody, scheduleProcess);
            scheduleProcess();
        }

        attachObserver();

        // Re-run processing whenever bot selection changes via sidebar or history navigation
        const botList = document.getElementById('botlist');
        if (botList) {
            botList.addEventListener('click', function (evt) {
                if (evt.target && evt.target.matches('a')) {
                    setTimeout(scheduleProcess, 400);
                }
            }, true);
        }
        window.addEventListener('popstate', scheduleProcess);

        function processAnimkRows() {
            const rows = document.querySelectorAll('#listtable tbody tr');
            if (rows.length === 0) return;

            // Get server and channel from page text
            const bodyText = document.body.textContent;
            const serverMatch = bodyText.match(/irc\.([a-z.]+)/i);
            const channelMatch = bodyText.match(/#([a-zA-Z0-9_-]+)/);
            const server = serverMatch ? 'irc.' + serverMatch[1] : 'irc.xertion.org';
            const channel = channelMatch ? normalizeChannel(channelMatch[1]) : '#MK';

            for (const row of rows) {
                if (row.querySelector('.dccbot-btn')) continue; // Already processed

                const cells = row.querySelectorAll('td');
                if (cells.length < 4) continue;

                const botname = cells[0].textContent.trim();
                const packnum = cells[1].textContent.trim();

                const btnCell = document.createElement('td');
                btnCell.className = 'number';
                const btn = get_download_btn(server, channel, botname, packnum);

                const stopRowPrompt = function (evt) {
                    evt.preventDefault();
                    if (evt.stopImmediatePropagation) evt.stopImmediatePropagation();
                    evt.stopPropagation();
                };

                ['mousedown', 'mouseup'].forEach(function (evtName) {
                    btn.addEventListener(evtName, stopRowPrompt, true);
                });

                btnCell.appendChild(btn);
                row.appendChild(btnCell);
            }
        }
    }

    function add_button_xdcc_rocks() {
        const resultsRoot = document.querySelector('.results');
        if (!resultsRoot) return;

        observeMutations(resultsRoot, function () {
            if (resultsRoot.querySelector('tr.font2_bg0_bg1')) {
                processRocksTable();
            }
        });
        processRocksTable();


        function parseRocksSectionMeta(section) {
            let server = null;
            let channel = null;

            const channelAnchor = section.querySelector('a[href^="irc://"]');
            if (channelAnchor) {
                const meta = parseIrcTarget(channelAnchor.getAttribute('href') || channelAnchor.href || '');
                server = meta.server;
                channel = meta.channel;
            }

            if (!server) {
                const serverRow = Array.from(section.querySelectorAll('tr')).find(function (tr) {
                    return /server\s*:/i.test(tr.textContent || '');
                });
                if (serverRow) {
                    const serverText = serverRow.textContent.replace(/server\s*:/i, '').trim();
                    server = serverText || null;
                }
            }

            if (!channel) {
                const channelRow = Array.from(section.querySelectorAll('tr')).find(function (tr) {
                    return /channel\s*:/i.test(tr.textContent || '');
                });
                if (channelRow) {
                    const channelText = channelRow.textContent.replace(/channel\s*:/i, '').trim();
                    channel = channelText || null;
                }
            }

            if (channel && !channel.startsWith('#')) channel = '#' + channel;
            return { server: server, channel: channel };
        }

        function processRocksTable() {
            const table = resultsRoot.querySelector('table');
            if (!table) return;

            let currentServer = null;
            let currentChannel = null;

            const sections = Array.from(table.children);
            sections.forEach(function (section) {
                if (section.tagName === 'THEAD') {
                    const meta = parseRocksSectionMeta(section);
                    currentServer = meta.server;
                    currentChannel = meta.channel;

                } else if (section.tagName === 'TBODY' && currentServer && currentChannel) {
                    const dataRows = Array.from(section.querySelectorAll('tr')).filter(function (row) {
                        return !row.querySelector('td[name]');
                    });

                    dataRows.forEach(function (row) {
                        if (row.querySelector('.dccbot-btn')) return;
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 3) return;

                        const botname = cells[0].textContent.trim();
                        const packnum = extractPackNumber(cells[1].textContent);
                        if (!packnum) return;
                        const filenameCell = cells[2];
                        if (filenameCell) {
                            const btn = get_download_btn(currentServer, currentChannel, botname, packnum);
                            filenameCell.insertBefore(btn, filenameCell.firstChild);
                        }
                    });
                }
            });
        }
    }

    function get_xdcc_search_meta(card) {
        const metaItems = card.querySelectorAll(".pack-meta-item");
        const meta = {
            botname: null,
            packnum: null,
            networkName: null,
            channelName: null
        };

        metaItems.forEach(function (item) {
            const labelEl = item.querySelector(".pack-meta-label");
            if (!labelEl) return;
            const label = labelEl.textContent
                .trim()
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, " ")
                .trim();
            const valueEl = labelEl.nextElementSibling;
            if (!valueEl) return;

            if (label === "bot") {
                meta.botname = valueEl.textContent.trim();
            } else if (label.indexOf("pack") === 0) {
                meta.packnum = valueEl.textContent.trim().replace(/^#/, "");
            } else if (label === "network") {
                meta.networkName = valueEl.textContent.trim();
            } else if (label === "channel") {
                const channelEl = item.querySelector(".channel-link") || valueEl;
                meta.channelName = channelEl.textContent.trim();
            }
        });

        return meta;
    }

    function add_button_xdcc_search() {
        function processCards() {
            const cards = document.querySelectorAll(".pack-card");
            if (!cards.length) return;

            cards.forEach(function (card) {
                const meta = get_xdcc_search_meta(card);
                const channel = normalizeChannel(meta.channelName || '');
                const packnum = extractPackNumber(meta.packnum);

                if (!meta.botname || !packnum || !channel) {
                    return;
                }

                if (card.querySelector(".dccbot-btn")) return;

                const server = mapNetworkToServer(meta.networkName);
                const packCommandDiv = card.querySelector(".pack-command");
                const existingDownloadBtn = packCommandDiv ? packCommandDiv.querySelector(".download-btn") : null;

                if (existingDownloadBtn) {
                    // Reuse native layout button instead of injecting a second one.
                    existingDownloadBtn.classList.add('dccbot-btn');
                    existingDownloadBtn.classList.remove('download-btn');
                    existingDownloadBtn.textContent = 'Down';
                    existingDownloadBtn.removeAttribute('onclick');
                    applyStyles(existingDownloadBtn, BUTTON_STYLES.default, BUTTON_STYLES.xdccSearchHeader);
                    configure_dccbot_btn(existingDownloadBtn, server, channel, meta.botname, packnum);
                } else if (packCommandDiv) {
                    const btn = get_download_btn(server, channel, meta.botname, packnum, BUTTON_STYLES.xdccSearchHeader);
                    packCommandDiv.appendChild(btn);
                } else {
                    const packHeader = card.querySelector(".pack-header");
                    const sizeEl = packHeader ? packHeader.querySelector(".pack-size") : null;
                    if (!packHeader || !sizeEl) return;
                    const btn = get_download_btn(server, channel, meta.botname, packnum, BUTTON_STYLES.xdccSearchHeader);
                    sizeEl.insertAdjacentElement("afterend", btn);
                }
            });
        }

        processCards();

        const resultsContainer = document.querySelector(".results-container");
        observeMutations(resultsContainer || document.body, processCards);
    }

    const hostHandlers = {
        'www.xdcc.eu': add_button_xdcc_eu,
        'nibl.co.uk': add_button_nibl,
        'xdcc.animk.info': add_button_animk_info,
        'xdcc.rocks': add_button_xdcc_rocks,
        'www.xdcc.rocks': add_button_xdcc_rocks,
        'xdcc-search.com': add_button_xdcc_search,
        'www.xdcc-search.com': add_button_xdcc_search
    };

    const handler = hostHandlers[window.location.hostname];
    if (handler) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () {
                initTransferOverlay();
                handler();
            }, { once: true });
        } else {
            initTransferOverlay();
            handler();
        }
    }
})();
