/**
 * @jest-environment-options {"url": "https://www.xdcc.info/search?q=dune"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const ROW = (bot, pack, server, channel) => `
  <tr data-bot="${bot}" data-pack-num="${pack}"
      data-network-address="${server}" data-channel="${channel}">
    <td>${pack}</td><td>file.mkv</td><td class="td-actions"></td>
  </tr>`;

describe("userscript on www.xdcc.info", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    installGmStubs();
  });

  test("injects a Down button into .td-actions for each valid row", () => {
    document.body.innerHTML = `
      <table class="pack-table"><tbody>
        ${ROW("TS-HD|US|P|FIBER", "508", "irc.SceneP2P.net", "#THE.SOURCE")}
        ${ROW("Zombie-Alpha", "744", "irc.abandoned-irc.net", "#zombie-warez")}
      </tbody></table>`;
    loadScript();

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.SceneP2P.net",
      channel: "#THE.SOURCE",
      bot: "TS-HD|US|P|FIBER",
      pack: "508",
    });
    expect(buttons[0].textContent).toBe("Down");
    expect(buttons[0].closest(".td-actions")).not.toBeNull();
  });

  test("skips rows missing required data attributes", () => {
    document.body.innerHTML = `
      <table class="pack-table"><tbody>
        ${ROW("BotA", "1", "irc.rizon.net", "#chan")}
        <tr data-bot="BotB"><td class="td-actions"></td></tr>
        <tr><td class="td-actions"></td></tr>
      </tbody></table>`;
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);
  });

  test("adds buttons to rows added later via MutationObserver", async () => {
    document.body.innerHTML = `<table class="pack-table"><tbody>${ROW(
      "BotA",
      "1",
      "irc.rizon.net",
      "#chan"
    )}</tbody></table>`;
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);

    document
      .querySelector("table.pack-table tbody")
      .insertAdjacentHTML(
        "beforeend",
        ROW("LateBot", "9", "irc.abjects.net", "#beast-xdcc")
      );
    await new Promise((r) => setTimeout(r, 0));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[1].dataset).toMatchObject({
      server: "irc.abjects.net",
      channel: "#beast-xdcc",
      bot: "LateBot",
      pack: "9",
    });
  });

  test("re-injects buttons when the results container is replaced", async () => {
    // The language filter re-renders the whole results container, detaching
    // the node the observer was watching.
    document.body.innerHTML = `
      <div class="results"><table class="pack-table"><tbody>
        ${ROW("BotA", "1", "irc.rizon.net", "#chan")}
      </tbody></table></div>`;
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);

    document.querySelector(".results").outerHTML = `
      <div class="results"><table class="pack-table"><tbody>
        ${ROW("NewBot", "42", "irc.abjects.net", "#beast-xdcc")}
      </tbody></table></div>`;
    await new Promise((r) => setTimeout(r, 0));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.abjects.net",
      channel: "#beast-xdcc",
      bot: "NewBot",
      pack: "42",
    });
  });

  test("runs on the bare xdcc.info domain too", () => {
    // hostname is fixed by the environment URL; covered by the dispatch table
    // indirectly — see hostHandlers for 'xdcc.info'.
    document.body.innerHTML = `<table class="pack-table"><tbody>${ROW(
      "BotA",
      "1",
      "irc.rizon.net",
      "#chan"
    )}</tbody></table>`;
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);
  });
});
