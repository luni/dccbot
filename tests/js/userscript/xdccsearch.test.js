/**
 * @jest-environment-options {"url": "https://xdccsearch.com/"}
 */

const {
  installGmStubs,
  loadScript,
  dccbotButtons,
} = require("./helpers");

const ROW = (network, channel, bot, slot) => `
  <tr>
    <td>${network}</td>
    <td>${channel}</td>
    <td>${bot}</td>
    <td class="cursor-pointer">#${slot}</td>
    <td>7x</td><td>[1.8MB]</td><td>25s</td>
    <td>file-${slot}.tar</td>
  </tr>`;

const PAGE = (rows) => `
  <div id="root">
    <div class="overflow-x-auto">
      <table class="min-w-full text-sm">
        <thead><tr><th>Network</th><th>Channel</th><th>Bot</th><th>Slot</th>
        <th>Gets</th><th>Size</th><th>Seen</th><th>Filename</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </div>`;

describe("userscript on xdccsearch.com", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    installGmStubs();
  });

  test("appends a Down button cell per row and a header cell", () => {
    document.body.innerHTML = PAGE(
      ROW("rizon", "#elitewarez", "[ewg]k4rl", "8") +
        ROW("abjects", "#beast-xdcc", "NewBot", "55")
    );
    loadScript();

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.rizon.net",
      channel: "#elitewarez",
      bot: "[ewg]k4rl",
      pack: "8",
    });
    expect(buttons[1].dataset.server).toBe("irc.abjects.net");
    expect(document.querySelector("th.dccbot-action")).not.toBeNull();
    expect(buttons[0].parentElement.tagName).toBe("TD");
    expect(buttons[0].parentElement.className).toBe("dccbot-action");
  });

  test("re-processes rows after the results view re-renders", async () => {
    document.body.innerHTML = PAGE(
      ROW("rizon", "#elitewarez", "[ewg]k4rl", "8")
    );
    loadScript();
    expect(dccbotButtons()).toHaveLength(1);

    // A new search replaces the results table entirely
    const wrapper = document.querySelector(".overflow-x-auto");
    wrapper.innerHTML = PAGE(ROW("terrachat", "#x", "TBot", "3"));
    await new Promise((r) => setTimeout(r, 0));

    const buttons = dccbotButtons();
    expect(buttons).toHaveLength(1);
    expect(buttons[0].dataset).toMatchObject({
      server: "irc.terrachat.cl",
      channel: "#x",
      bot: "TBot",
      pack: "3",
    });
    expect(document.querySelector("th.dccbot-action")).not.toBeNull();
  });
});
