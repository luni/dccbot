/**
 * @jest-environment-options {"url": "https://xdcc-search.com/"}
 *
 * Tests for transfer-event notifications in the userscript.
 * The script polls /info in fallback mode (https page + http endpoint is
 * mixed-content-blocked), so driving the stubbed GM_xmlhttpRequest onload
 * reproduces the websocket broadcast path with full control over payloads.
 *
 * Notices are counted at creation time via MutationObserver because they
 * self-dismiss after a timeout and would vanish between polls.
 */

const { installGmStubs, loadScript } = require("./helpers");

function infoResponse(transfers) {
  return {
    status: 200,
    responseText: JSON.stringify({ transfers }),
  };
}

let createdNotices;
let observer;

function noticeWatcher() {
  observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === 1 && node.classList.contains("dccbot-notice")) {
          createdNotices.push(node.textContent);
        }
      });
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

async function flushNotices() {
  // MutationObserver callbacks are queued as microtasks.
  await Promise.resolve();
  await Promise.resolve();
}

async function deliver(transfers) {
  const calls = window.GM_xmlhttpRequest.mock.calls;
  const req = calls[calls.length - 1][0];
  expect(req.url).toMatch(/\/info$/);
  req.onload(infoResponse(transfers));
  await flushNotices();
}

async function nextPoll(transfers) {
  jest.advanceTimersByTime(3000);
  await deliver(transfers);
}

const T = (status, filename = "file.rar", nick = "BotNick") => ({
  server: "irc.rizon.net",
  nick,
  filename,
  status,
});

describe("transfer notifications", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    document.body.innerHTML = "";
    installGmStubs();
    // Capture /info requests instead of failing them; tests drive onload.
    window.GM_xmlhttpRequest = jest.fn(() => {});
    createdNotices = [];
    noticeWatcher();
    loadScript();
    // The first poll fires synchronously on load.
    expect(window.GM_xmlhttpRequest).toHaveBeenCalled();
  });

  afterEach(() => {
    observer.disconnect();
    jest.useRealTimers();
  });

  test("first snapshot after page load only seeds state, no popups", async () => {
    await deliver([
      T("completed"),
      T("in_progress", "running.mkv"),
      T("started", "queued.zip"),
      T("failed", "old.bin"),
    ]);
    expect(createdNotices).toEqual([]);

    // Still nothing on subsequent identical snapshots.
    await nextPoll([T("completed"), T("in_progress", "running.mkv")]);
    expect(createdNotices).toEqual([]);
  });

  test("notifies once on start and finish transitions", async () => {
    await deliver([]);
    await nextPoll([T("in_progress")]);
    expect(createdNotices).toEqual(["[DCCBOT] Started: file.rar (BotNick)"]);

    await nextPoll([T("completed")]);
    expect(createdNotices).toEqual([
      "[DCCBOT] Started: file.rar (BotNick)",
      "[DCCBOT] Finished: file.rar (BotNick)",
    ]);

    // A stable 'completed' status must not retrigger the notification.
    await nextPoll([T("completed")]);
    await nextPoll([T("completed")]);
    expect(
      createdNotices.filter((t) => t.includes("Finished: file.rar"))
    ).toHaveLength(1);
  });

  test("duplicate stale records for one key do not loop notifications", async () => {
    // A stale terminal record plus the real one under the same key.
    const dup = [
      T("failed", "file.rar", "BotNick"),
      T("completed", "file.rar", "BotNick"),
    ];
    await deliver(dup);
    expect(createdNotices).toEqual([]);

    for (let i = 0; i < 5; i += 1) {
      await nextPoll(dup);
      expect(createdNotices).toEqual([]);
    }
  });

  test("duplicate running+terminal records do not loop notifications", async () => {
    const dup = [T("in_progress"), T("completed")];
    await deliver(dup);
    for (let i = 0; i < 4; i += 1) {
      await nextPoll(dup);
      expect(createdNotices).toEqual([]);
    }
  });

  test("a new transfer appearing later still notifies Started", async () => {
    await deliver([]);
    await nextPoll([T("in_progress", "new.mkv")]);
    expect(createdNotices).toEqual(["[DCCBOT] Started: new.mkv (BotNick)"]);

    await nextPoll([T("in_progress", "new.mkv")]);
    expect(
      createdNotices.filter((t) => t.includes("Started: new.mkv"))
    ).toHaveLength(1);
  });

  test("notifies Ended for failed transitions", async () => {
    await deliver([]);
    await nextPoll([T("in_progress")]);
    await nextPoll([T("failed")]);
    expect(createdNotices).toEqual([
      "[DCCBOT] Started: file.rar (BotNick)",
      "[DCCBOT] Ended (failed): file.rar (BotNick)",
    ]);
  });

  test("a retry after failure notifies Started again", async () => {
    await deliver([]);
    await nextPoll([T("in_progress")]);
    await nextPoll([T("failed")]);
    await nextPoll([T("in_progress")]);
    expect(
      createdNotices.filter((t) => t.includes("Started: file.rar"))
    ).toHaveLength(2);
  });

  test("started -> in_progress progression notifies Started only once", async () => {
    await deliver([]);
    await nextPoll([T("started")]);
    await nextPoll([T("in_progress")]);
    await nextPoll([T("in_progress")]);
    expect(
      createdNotices.filter((t) => t.includes("Started: file.rar"))
    ).toHaveLength(1);
  });
});
