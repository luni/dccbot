/**
 * @jest-environment-options {"url": "https://www.xdcc.eu/search.php?searchkey=dune"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const ROW = (server, channel, bot, pack) => `
  <tr>
    <td>1</td>
    <td><a href="irc://${server}/${channel.replace("#", "")}"
           data-s="${server}" data-c="${channel}">info</a></td>
    <td>${bot}</td><td>${pack}</td><td>file-${pack}.mkv</td>
  </tr>`;

const PAGE = (rows) => `
  <div class="container"><div class="twelve">
    <table id="table"><tbody>${rows}</tbody></table>
  </div></div>
  <h4>results</h4><div id="msg"></div>`;

describe("userscript on www.xdcc.eu", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    installGmStubs();
  });

  test("appends a button cell per row with data-s/data-c metadata", () => {
    document.body.innerHTML = PAGE(
      ROW("irc.abjects.net", "#beast-xdcc", "BEAST-X", "#181") +
        ROW("irc.abandoned-irc.net", "#zombie-warez", "Zombie-NTb", "#796")
    );
    loadScript();

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.abjects.net",
      channel: "#beast-xdcc",
      bot: "BEAST-X",
      pack: "181", // leading '#' stripped
    });
    expect(buttons[0].parentElement.tagName).toBe("TD");
  });

  test("falls back to parsing the irc:// link when data attrs are missing", () => {
    document.body.innerHTML = PAGE(`
      <tr>
        <td>1</td>
        <td><a href="irc://irc.rizon.net/elitewarez">info</a></td>
        <td>LinkBot</td><td>#42</td><td>file.mkv</td>
      </tr>`);
    loadScript();
    expect(dccbotButtons()[0].dataset).toMatchObject({
      server: "irc.rizon.net",
      channel: "#elitewarez",
      bot: "LinkBot",
      pack: "42",
    });
  });

  test("h4 click dumps all results as a batch textarea into #msg", () => {
    document.body.innerHTML = PAGE(
      ROW("irc.abjects.net", "#beast-xdcc", "BEAST-X", "#181") +
        ROW("irc.rizon.net", "#elitewarez", "EWG", "#5")
    );
    loadScript();

    document.querySelector("h4").click();
    const textarea = document.querySelector("#msg textarea");
    expect(textarea).not.toBeNull();
    expect(textarea.value).toBe(
      "irc.abjects.net;#beast-xdcc;BEAST-X;181\nirc.rizon.net;#elitewarez;EWG;5"
    );
  });
});
