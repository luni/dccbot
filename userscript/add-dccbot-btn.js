// ==UserScript==
// @name         add-dccbot-btn
// @namespace    https://github.com/luni/dccbot/
// @website      https://github.com/luni/dccbot/
// @version      2025-11-23
// @description  Add Button for DCCbot to automate downloads.
// @author       luni
// @match        https://www.xdcc.eu/search.php*
// @match        https://nibl.co.uk/*
// @match        https://xdcc.animk.info/*
// @match        https://xdcc.rocks/*
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
        return
    }

    console.log("API Endpoint for DCCBot: " + dccbot_api);

    function get_element_by_xpath(path, parent) {
        return document.evaluate(path, parent || document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
    }

    function get_elements_by_xpath(xpath, parent) {
        let results = [], query = document.evaluate(xpath, parent || document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (let i = 0, length = query.snapshotLength; i < length; ++i) { results.push(query.snapshotItem(i)); };
        return results;
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
                console.log("[DCCBOT] API Reponse: " + response.responseText);
            }
        });
    }

    function add_button_xdcc_eu() {
        document.getElementsByClassName('container')[0].style.maxWidth = "100%";
        document.getElementsByClassName('container')[0].style.width = "90%";
        document.getElementsByClassName('twelve')[0].style.marginTop = 0;

        let all_results = [];
        for (let x of document.getElementById('table').getElementsByTagName('tbody')[0].getElementsByTagName('tr')) {
            let d = [
                x.getElementsByTagName('td')[1].getElementsByTagName('a')[0].href.replace('irc://', '').split('/')[0],
            ];
            for (let y = 1; y < 4; y++) {
                d.push(x.getElementsByTagName('td')[y].textContent.trim());
            }
            d[3] = d[3].replace('#', '');
            all_results.push(d.join(';'))
            x.dataset.data = d.join(';');
            x.dataset.channel = d[1];
            x.dataset.server = d[0];
            x.dataset.bot = d[2];
            x.dataset.pack = d[3];
            let clmn = document.createElement('td'),
                btn = document.createElement('button');
            btn.innerHTML = 'Down';
            clmn.appendChild(btn);
            x.appendChild(clmn)
            btn.onclick = function (e) {
                e.preventDefault();
                const d = this.parentNode.parentNode.dataset;
                send_msg(d.server, d.channel, d.bot, "xdcc send " + d.pack);
                this.parentNode.parentNode.style.background = '#CECECE';
                return;
            };
        }

        document.getElementsByTagName('h4')[0].onclick = function (e) {
            document.getElementById('msg').innerHTML = '<textarea style="width:100%;" rows=8>' + all_results.join('\n') + '</textarea>';
        }
    }

    function add_button_nibl() {
        function send_to_dccbot(e) {
            e.preventDefault();
            const botpack = this.dataset.botpack,
                botname = this.dataset.botname;
            send_msg("irc.rizon.net", "#nibl", botname, "xdcc send " + botpack);

        }

        for (let copy_btn of get_elements_by_xpath("//button[contains(@class, 'copy-data')]")) {
            // const td=copy_btn.parentNode, new_btn=copy_btn.cloneNode();
            copy_btn.className = copy_btn.className.replace('copy-data ', '');
            copy_btn.innerHTML = 'Down';
            copy_btn.onclick = send_to_dccbot;
            // td.appendChild(new_btn);
        }

        const copy_batch_btn = document.getElementById('copy-as-batch');
        copy_batch_btn.innerHTML = 'Download selected';
        copy_batch_btn.onclick = function (e) {
            e.preventDefault();
            let bots = {};
            for (const ckbox of get_elements_by_xpath("//input[@name='batch']")) {
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
        // Wait for table to be populated (it's dynamically loaded)
        const observer = new MutationObserver(function(mutations) {
            const rows = document.querySelectorAll('#listtable tbody tr.anime0, #listtable tbody tr.anime1');
            if (rows.length > 0) {
                observer.disconnect();
                processAnimkRows();
            }
        });

        const table = document.getElementById('listtable');
        if (table) {
            observer.observe(table, { childList: true, subtree: true });
        }

        // Also try to process immediately if rows already exist
        setTimeout(processAnimkRows, 1000);

        function processAnimkRows() {
            const rows = document.querySelectorAll('#listtable tbody tr.anime0, #listtable tbody tr.anime1');
            if (rows.length === 0) return;

            // Get server and channel from page text
            const bodyText = document.body.textContent;
            const serverMatch = bodyText.match(/irc\.([a-z.]+)/i);
            const channelMatch = bodyText.match(/#([a-zA-Z0-9_-]+)/);
            const server = serverMatch ? 'irc.' + serverMatch[1] : 'irc.xertion.org';
            const channel = channelMatch ? channelMatch[1] : 'MK';

            for (let row of rows) {
                if (row.querySelector('.dccbot-btn')) continue; // Already processed

                const cells = row.querySelectorAll('td');
                if (cells.length < 4) continue;

                const botname = cells[0].textContent.trim();
                const packnum = cells[1].textContent.trim();

                const btnCell = document.createElement('td');
                btnCell.className = 'number';
                const btn = document.createElement('button');
                btn.className = 'dccbot-btn';
                btn.textContent = 'Down';
                btn.style.cursor = 'pointer';
                btn.dataset.server = server;
                btn.dataset.channel = channel;
                btn.dataset.bot = botname;
                btn.dataset.pack = packnum;

                btn.onclick = function(e) {
                    e.preventDefault();
                    const d = this.dataset;
                    send_msg(d.server, d.channel, d.bot, "xdcc send " + d.pack);
                    this.parentNode.parentNode.style.background = '#CECECE';
                };

                btnCell.appendChild(btn);
                row.appendChild(btnCell);
            }
        }
    }

    function add_button_xdcc_rocks() {
        // Wait for results to load
        const observer = new MutationObserver(function(mutations) {
            const rows = document.querySelectorAll('tr.font2_bg0_bg1');
            if (rows.length > 0) {
                observer.disconnect();
                processRocksRows();
            }
        });

        const resultsDiv = document.querySelector('.results');
        if (resultsDiv) {
            observer.observe(resultsDiv, { childList: true, subtree: true });
        }

        // Also try to process immediately if rows already exist
        setTimeout(processRocksRows, 1000);

        function processRocksRows() {
            const rows = document.querySelectorAll('tr.font2_bg0_bg1');
            if (rows.length === 0) return;

            // Get server and channel from page text
            const bodyText = document.body.textContent;
            const serverMatch = bodyText.match(/Server[:\s]+([^\s\n]+)/i);
            const channelMatch = bodyText.match(/Channel[:\s]+([^\s\n]+)/i);
            const server = serverMatch ? serverMatch[1] : 'irc.abjects.net';
            const channel = channelMatch ? channelMatch[1].replace('#', '') : 'beast-xdcc';

            for (let row of rows) {
                if (row.querySelector('.dccbot-btn')) continue; // Already processed

                const cells = row.querySelectorAll('td');
                if (cells.length < 3) continue;

                const botname = cells[0].textContent.trim();
                const packnum = cells[1].textContent.trim();

                const btnCell = document.createElement('td');
                const btn = document.createElement('button');
                btn.className = 'dccbot-btn';
                btn.textContent = 'Down';
                btn.style.cursor = 'pointer';
                btn.style.padding = '4px 8px';
                btn.style.background = '#4CAF50';
                btn.style.color = 'white';
                btn.style.border = 'none';
                btn.style.borderRadius = '3px';
                btn.dataset.server = server;
                btn.dataset.channel = channel;
                btn.dataset.bot = botname;
                btn.dataset.pack = packnum;

                btn.onclick = function(e) {
                    e.preventDefault();
                    const d = this.dataset;
                    send_msg(d.server, d.channel, d.bot, "xdcc send #" + d.pack);
                    this.style.background = '#CECECE';
                    this.textContent = 'Sent';
                };

                btnCell.appendChild(btn);
                row.appendChild(btnCell);
            }
        }
    }

    const hostname = window.location.hostname;
    if (hostname == 'www.xdcc.eu') {
        add_button_xdcc_eu();
        return;
    }
    if (hostname == 'nibl.co.uk') {
        add_button_nibl();
        return;
    }
    if (hostname == 'xdcc.animk.info') {
        add_button_animk_info();
        return;
    }
    if (hostname == 'xdcc.rocks') {
        add_button_xdcc_rocks();
        return;
    }
})();