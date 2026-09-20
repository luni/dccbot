/**
 * @jest-environment-options {"url": "https://xdcc.animk.info/"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const ROW = (bot, pack) => `
  <tr class="anime0"><td class="number">${bot}</td><td class="number">${pack}</td>
  <td class="number">100M</td><td class="name">file-${pack}.mkv</td>
  <td class="number">0</td></tr>`;

describe("userscript on xdcc.animk.info", () => {
  beforeEach(() => {
    installGmStubs();
    document.body.innerHTML = `
      <div>Botpacks for channel #MK on irc.xertion.org</div>
      <div id="botlist"><a href="#">YameiiServ</a></div>
      <table id="listtable"><tbody></tbody></table>`;
  });

  test("appends a button cell to each data row, skipping header rows", async () => {
    document.querySelector("#listtable tbody").innerHTML = `
      <tr class="animeColumn"><th>Bot</th><th>Pack</th></tr>
      ${ROW("YameiiServ", "1")}
      ${ROW("YameiiServ", "2")}`;
    loadScript();
    await new Promise((r) => setTimeout(r, 50));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.xertion.org",
      channel: "#MK",
      bot: "YameiiServ",
      pack: "1",
    });
    expect(buttons[0].parentElement.className).toBe("number");
  });

  test("falls back to default server/channel when page text lacks them", async () => {
    document.body.innerHTML = `
      <div id="botlist"></div>
      <table id="listtable"><tbody>${ROW("SomeBot", "42")}</tbody></table>`;
    loadScript();
    await new Promise((r) => setTimeout(r, 50));
    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0].dataset.server).toBe("irc.xertion.org");
    expect(buttons[0].dataset.channel).toBe("#MK");
  });

  test("processes rows populated after selecting a bot", async () => {
    loadScript();
    expect(dccbotButtons()).toHaveLength(0);

    document.querySelector("#listtable tbody").innerHTML = ROW(
      "LateBot",
      "10"
    );
    await new Promise((r) => setTimeout(r, 50));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0].dataset.bot).toBe("LateBot");
  });
});
