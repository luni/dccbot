// ==UserScript==
// @name         add-dccbot-btn
// @namespace    https://github.com/luni/dccbot/
// @website      https://github.com/luni/dccbot/
// @version      2025-12-01
// @description  Add Button for DCCbot to automate downloads.
// @author       luni
// @match        https://www.xdcc.eu/search.php*
// @match        https://nibl.co.uk/*
// @match        https://xdcc.animk.info/*
// @match        https://xdcc.rocks/*
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

    console.log("API Endpoint for DCCBot: " + dccbot_api);

    const BUTTON_STYLES = {
        default: {
            cursor: 'pointer',
            padding: '4px 8px',
            background: '#4CAF50',
            color: 'white',
            border: 'none',
            borderRadius: '3px',
        },
        xdccSearchHeader: {
            padding: '8px',
            marginLeft: '8px'
        }
    };

    function handleDccbotButtonClick(evt) {
        evt.preventDefault();
        if (evt.stopImmediatePropagation) evt.stopImmediatePropagation();
        evt.stopPropagation();
        const d = this.dataset;
        send_msg(d.server, d.channel, d.bot, "xdcc send #" + d.pack);
        this.style.background = '#CECECE';
        this.textContent = 'Sent';
    }

    function applyStyles(element, defaultStyle, styleOverrides) {
        const styles = Object.assign({}, defaultStyle, styleOverrides || {});
        Object.keys(styles).forEach(function (key) {
            element.style[key] = styles[key];
        });
    }

    function get_download_btn(server, channel, botname, packnum, styleOverrides) {
        const btn = document.createElement('button');
        btn.className = 'dccbot-btn';
        btn.textContent = 'Down';

        applyStyles(btn, BUTTON_STYLES.default, styleOverrides);

        btn.dataset.server = server;
        btn.dataset.channel = channel;
        btn.dataset.bot = botname;
        btn.dataset.pack = packnum;
        btn.onclick = handleDccbotButtonClick;
        return btn;
    }

    function send_msg(server, channel, user, message) {
        GM_xmlhttpRequest({
            method: "POST",
            url: dccbot_api + '/msg',
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
                console.log("[DCCBOT] API Response: " + response.responseText);
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
        document.getElementsByClassName('container')[0].style.maxWidth = "100%";
        document.getElementsByClassName('container')[0].style.width = "90%";
        document.getElementsByClassName('twelve')[0].style.marginTop = 0;

        const all_results = [];
        for (const x of document.getElementById('table').getElementsByTagName('tbody')[0].getElementsByTagName('tr')) {
            const d = [
                x.getElementsByTagName('td')[1].getElementsByTagName('a')[0].href.replace('irc://', '').split('/')[0],
            ];
            for (let y = 1; y < 4; y++) {
                d.push(x.getElementsByTagName('td')[y].textContent.trim());
            }
            d[3] = d[3].replace('#', '');
            all_results.push(d.join(';'));
            const btnCell = document.createElement('td'),
            btn = get_download_btn(d[0], d[1], d[2], d[3]);
            btnCell.appendChild(btn);
            x.appendChild(btnCell);
        }

        document.getElementsByTagName('h4')[0].onclick = function (e) {
            document.getElementById('msg').innerHTML = '<textarea style="width:100%;" rows=8>' + all_results.join('\n') + '</textarea>';
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

            const observer = new MutationObserver(scheduleProcess);
            observer.observe(tableBody, { childList: true, subtree: true });
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
            const channel = channelMatch ? channelMatch[1] : 'MK';

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

        const observer = new MutationObserver(function () {
            if (resultsRoot.querySelector('tr.font2_bg0_bg1')) {
                processRocksTable();
            }
        });
        observer.observe(resultsRoot, { childList: true, subtree: true });
        processRocksTable();


        function processRocksTable() {
            const table = resultsRoot.querySelector('table');
            if (!table) return;

            let currentServer = null;
            let currentChannel = null;

            const sections = Array.from(table.children);
            sections.forEach(function (section) {
                if (section.tagName === 'THEAD') {
                    const channelAnchor = section.querySelector('a[href^="irc://"]');
                    if (channelAnchor) {
                        try {
                            const url = new URL(channelAnchor.href);
                            currentServer = url.hostname;
                            currentChannel = channelAnchor.href.match(/(\#.+)$/)[1];
                        } catch (e) {
                            currentServer = null;
                            currentChannel = null;
                        }
                    }

                } else if (section.tagName === 'TBODY' && currentServer && currentChannel) {
                    const dataRows = Array.from(section.querySelectorAll('tr')).filter(function (row) {
                        return !row.querySelector('td[name]');
                    });

                    dataRows.forEach(function (row) {
                        if (row.querySelector('.dccbot-btn')) return;
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 3) return;

                        const botname = cells[0].textContent.trim();
                        const packnum = cells[1].textContent.trim();
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
            const label = labelEl.textContent.trim().toLowerCase().replace(":", "");
            const valueEl = labelEl.nextElementSibling;
            if (!valueEl) return;

            if (label === "bot") {
                meta.botname = valueEl.textContent.trim();
            } else if (label === "pack") {
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
        const cards = document.querySelectorAll(".pack-card");
        if (!cards.length) return;

        cards.forEach(function (card) {
            const meta = get_xdcc_search_meta(card);

            if (!meta.botname || !meta.packnum || !meta.channelName) {
                return;
            }

            const server = mapNetworkToServer(meta.networkName);
            const packHeader = card.querySelector(".pack-header");
            const sizeEl = packHeader ? packHeader.querySelector(".pack-size") : null;
            if (!packHeader || !sizeEl) return;

            if (card.querySelector(".dccbot-btn")) return;

            const btn = get_download_btn(server, meta.channelName, meta.botname, meta.packnum, BUTTON_STYLES.xdccSearchHeader);
            sizeEl.insertAdjacentElement("afterend", btn);

            const packCommandDiv = card.querySelector(".pack-command");
            if (packCommandDiv) {
                packCommandDiv.remove();
            }
        });
    }

    const hostHandlers = {
        'www.xdcc.eu': add_button_xdcc_eu,
        'nibl.co.uk': add_button_nibl,
        'xdcc.animk.info': add_button_animk_info,
        'xdcc.rocks': add_button_xdcc_rocks,
        'xdcc-search.com': add_button_xdcc_search
    };

    const handler = hostHandlers[window.location.hostname];
    if (handler) {
        handler();
    }
})();