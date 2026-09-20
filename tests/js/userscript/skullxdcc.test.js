/**
 * @jest-environment-options {"url": "https://skullxdcc.com/search/dune"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const ROW = (server, channel, bot, pack) => `
  <tr>
    <td class="network" title="${server}"><a href="irc://${server}">${server.replace(
      "irc.",
      ""
    )}</a></td>
    <td class="channel"><a href="irc://${server}/${channel.replace(
      "#",
      ""
    )}">${channel.replace("#", "")}</a></td>
    <td class="bot"><a href="#">${bot}</a></td>
    <td class="pack"><a href="#">${pack}</a></td>
    <td class="fname"><a href="#">file-${pack}.mkv</a></td>
    <td class="size">1G</td><td class="gets">3</td><td class="seen">9/20</td>
  </tr>`;

const PAGE = (rows) => `
  <div class="results-wrapper">
    <table>
      <thead><tr><th>Network</th><th>Channel</th><th>Bot</th><th>Pack</th>
      <th>Filename</th><th>Size</th><th>Gets</th><th>Seen</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;

describe("userscript on skullxdcc.com", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    installGmStubs();
  });

  test("appends a Down button cell per row and a header cell", () => {
    document.body.innerHTML = PAGE(
      ROW("irc.rizon.net", "#asthenia", "AA-DCC08", "7") +
        ROW("irc.rizon.net", "#elitewarez", "[EWG]k4rl", "421")
    );
    loadScript();

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.rizon.net",
      channel: "#asthenia",
      bot: "AA-DCC08",
      pack: "7",
    });
    expect(buttons[1].dataset.bot).toBe("[EWG]k4rl");
    expect(document.querySelector("th.dccbot-action")).not.toBeNull();
    // button lives in its own trailing td
    expect(buttons[0].parentElement.tagName).toBe("TD");
    expect(buttons[0].parentElement.className).toBe("dccbot-action");
  });

  test("re-processes rows after client-side pagination", async () => {
    document.body.innerHTML = PAGE(
      ROW("irc.rizon.net", "#asthenia", "AA-DCC08", "7")
    );
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);

    // React-style re-render: replace the whole tbody
    const tbody = document.querySelector("table tbody");
    tbody.innerHTML = ROW("irc.abjects.net", "#beast-xdcc", "NewBot", "55");
    await new Promise((r) => setTimeout(r, 0));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.abjects.net",
      channel: "#beast-xdcc",
      bot: "NewBot",
      pack: "55",
    });
  });
});
