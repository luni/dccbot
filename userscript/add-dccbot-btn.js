// ==UserScript==
// @name         add-dccbot-btn
// @namespace    https://github.com/luni/dccbot/
// @website      https://github.com/luni/dccbot/
// @version      2025-02-12
// @description  Add Button for DCCbot to automate downloads.
// @author       luni
// @match        https://www.xdcc.eu/search.php*
// @match        https://nibl.co.uk/*
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

    const hostname = window.location.hostname;
    if (hostname == 'www.xdcc.eu') {
        add_button_xdcc_eu();
        return;
    }
    if (hostname == 'nibl.co.uk') {
        add_button_nibl();
        return;
    }
})();