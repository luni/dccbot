/**
 * @jest-environment-options {"url": "https://xdcc-search.com/search/dune"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const CARD = (bot, pack, network, channel) => `
  <div class="pack-card">
    <div class="pack-header"><span class="pack-size">1G</span></div>
    <div class="pack-meta-item"><span class="pack-meta-label">Bot</span><span>${bot}</span></div>
    <div class="pack-meta-item"><span class="pack-meta-label">Pack #</span><span>${pack}</span></div>
    <div class="pack-meta-item"><span class="pack-meta-label">Network</span><span>${network}</span></div>
    <div class="pack-meta-item"><span class="pack-meta-label">Channel</span><span class="channel-link">${channel.replace(
      "#",
      ""
    )}</span></div>
    <div class="pack-command"><button class="download-btn">Download</button></div>
  </div>`;

describe("userscript on xdcc-search.com", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    installGmStubs();
  });

  test("reuses the native .download-btn and maps network names to irc servers", () => {
    document.body.innerHTML = `
      <div class="results-container">
        ${CARD("[ewg]gemupc", "7589", "CoreIRC", "#elitewarez")}
        ${CARD("BEAST-X", "181", "Abjects", "#beast-xdcc")}
      </div>`;
    loadScript();

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.coreirc.net",
      channel: "#elitewarez",
      bot: "[ewg]gemupc",
      pack: "7589",
    });
    expect(buttons[1].dataset.server).toBe("irc.abjects.net");
    // the native button was repurposed, not duplicated
    expect(document.querySelectorAll(".download-btn")).toHaveLength(0);
  });

  test("cards added later via MutationObserver get processed", async () => {
    document.body.innerHTML = `<div class="results-container">${CARD(
      "Bot",
      "1",
      "Rizon",
      "#chan"
    )}</div>`;
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);

    document
      .querySelector(".results-container")
      .insertAdjacentHTML(
        "beforeend",
        CARD("LateBot", "99", "SceneP2P", "#other")
      );
    await new Promise((r) => setTimeout(r, 0));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[1].dataset).toMatchObject({
      server: "irc.scenep2p.net",
      channel: "#other",
      bot: "LateBot",
      pack: "99",
    });
  });
});
