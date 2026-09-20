/**
 * @jest-environment-options {"url": "https://nibl.co.uk/search?query=one%20piece"}
 */

const {
  installGmStubs,
  loadScript,
  lastApiRequest,
} = require("./helpers");

const PAGE = `
  <input type="checkbox" name="batch" data-botname="Illum" data-botpack="2007" checked>
  <input type="checkbox" name="batch" data-botname="Illum" data-botpack="2008" checked>
  <input type="checkbox" name="batch" data-botname="Other" data-botpack="3" checked>
  <input type="checkbox" name="batch" data-botname="Skipped" data-botpack="9">
  <button class="copy-data btn" data-botpack="2007" data-botname="Illum">Copy</button>
  <button class="copy-data btn" data-botpack="3" data-botname="Other">Copy</button>
  <button id="copy-as-batch">Copy as batch</button>`;

describe("userscript on nibl.co.uk", () => {
  beforeEach(() => {
    document.body.innerHTML = PAGE;
    installGmStubs();
  });

  test("converts copy-data buttons into download buttons", () => {
    loadScript();

    expect(document.querySelectorAll("button.copy-data")).toHaveLength(0);
    const downs = Array.from(document.querySelectorAll("button")).filter(
      (b) => b.textContent.trim() === "Down"
    );
    expect(downs).toHaveLength(2);
    expect(document.getElementById("copy-as-batch").textContent).toBe(
      "Download selected"
    );
  });

  test("clicking a Down button POSTs /msg with 'xdcc send <pack>'", () => {
    loadScript();
    const btn = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent.trim() === "Down" && b.dataset.botname === "Illum"
    );
    btn.click();

    const req = lastApiRequest();
    expect(req.method).toBe("POST");
    expect(req.url).toBe("http://localhost:8080/msg");
    expect(JSON.parse(req.data)).toEqual({
      server: "irc.rizon.net",
      channel: "#nibl",
      user: "Illum",
      message: "xdcc send 2007",
    });
  });

  test("clicking a notice dismisses it before the timeout", () => {
    jest.useFakeTimers();
    try {
      loadScript();
      const btn = Array.from(document.querySelectorAll("button")).find(
        (b) => b.textContent.trim() === "Down"
      );
      btn.click();

      // GM_xmlhttpRequest stub fires onerror -> error notice appears
      const notice = document.querySelector(".dccbot-notice");
      expect(notice).not.toBeNull();
      expect(notice.textContent).toContain("[DCCBOT] Failed");

      notice.click();
      expect(document.querySelector(".dccbot-notice")).toBeNull();

      // no stray timer should recreate or error after dismissal
      jest.advanceTimersByTime(10000);
      expect(document.querySelector(".dccbot-notice")).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });

  test("batch download groups checked packs per bot", () => {
    loadScript();
    document.getElementById("copy-as-batch").click();

    const calls = window.GM_xmlhttpRequest.mock.calls
      .filter((c) => c[0].data)
      .map((c) => JSON.parse(c[0].data));
    expect(calls).toContainEqual({
      server: "irc.rizon.net",
      channel: "#nibl",
      user: "Illum",
      message: "xdcc batch 2007,2008",
    });
    expect(calls).toContainEqual({
      server: "irc.rizon.net",
      channel: "#nibl",
      user: "Other",
      message: "xdcc batch 3",
    });
    // unchecked checkbox is not sent
    expect(calls.find((c) => c.user === "Skipped")).toBeUndefined();
  });
});
