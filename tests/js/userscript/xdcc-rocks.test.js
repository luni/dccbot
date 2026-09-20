/**
 * @jest-environment-options {"url": "https://xdcc.rocks/"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const SECTION = (ircUrl, rows) => `
  <thead><tr><td><a href="${ircUrl}">channel</a></td></tr></thead>
  <tbody>${rows}</tbody>`;

const DATA_ROW = (bot, pack) => `
  <tr class="font2_bg0_bg1"><td>${bot}</td><td>#${pack}</td><td>file-${pack}.mkv</td></tr>`;

describe("userscript on xdcc.rocks", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    installGmStubs();
  });

  test("inserts a button into the filename cell of each data row", () => {
    document.body.innerHTML = `
      <div class="results"><table>
        ${SECTION(
          "irc://irc.rizon.net/elitewarez",
          DATA_ROW("[EWG]FourTwenty", "5797") + DATA_ROW("[EWG]k4rl", "421")
        )}
        ${SECTION(
          "irc://irc.abjects.net/beast-xdcc",
          DATA_ROW("BEAST-X", "181")
        )}
      </table></div>`;
    loadScript();

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(3);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.rizon.net",
      channel: "#elitewarez",
      bot: "[EWG]FourTwenty",
      pack: "5797",
    });
    // section-scoped server/channel
    expect(buttons[2].dataset).toMatchObject({
      server: "irc.abjects.net",
      channel: "#beast-xdcc",
      bot: "BEAST-X",
    });
  });

  test("ignores rows containing td[name]", () => {
    document.body.innerHTML = `
      <div class="results"><table>
        ${SECTION(
          "irc://irc.rizon.net/elitewarez",
          `<tr><td name="bot">not-a-data-row</td></tr>` + DATA_ROW("Bot", "1")
        )}
      </table></div>`;
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);
  });
});
