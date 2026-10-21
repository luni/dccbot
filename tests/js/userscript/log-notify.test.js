/**
 * @jest-environment-options {"url": "https://xdcc-search.com/"}
 *
 * Tests for server log notices in the userscript. An https endpoint is not
 * mixed-content-blocked on an https page, so the userscript uses its
 * WebSocket path; a stub captures the socket so tests can emit the same
 * payloads the dccbot server broadcasts.
 *
 * Notices are counted at creation time via MutationObserver because they
 * self-dismiss after a timeout and would vanish between checks.
 */

const { installGmStubs, loadScript } = require("./helpers");

let createdNotices;
let observer;
let sockets;

class MockWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = MockWebSocket.OPEN;
    this.listeners = {};
    sockets.push(this);
  }

  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }

  emit(type, event) {
    (this.listeners[type] || []).slice().forEach((fn) => fn(event));
  }
}

MockWebSocket.CONNECTING = 0;
MockWebSocket.OPEN = 1;
MockWebSocket.CLOSING = 2;
MockWebSocket.CLOSED = 3;

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

function sendLog(level, message) {
  const socket = sockets[sockets.length - 1];
  socket.emit("message", {
    data: JSON.stringify({ type: "log", level, message }),
  });
  return flushNotices();
}

describe("log notifications", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    document.body.innerHTML = "";
    installGmStubs("https://dccbot.local:8443");
    sockets = [];
    window.WebSocket = MockWebSocket;
    createdNotices = [];
    noticeWatcher();
    loadScript();
    // The userscript opens the transfer stream on load.
    expect(sockets).toHaveLength(1);
    expect(sockets[0].url).toMatch(/^wss:\/\/dccbot\.local:8443\/ws$/);
  });

  afterEach(() => {
    observer.disconnect();
    delete window.WebSocket;
    jest.useRealTimers();
  });

  test("WARNING and ERROR log entries surface as notices", async () => {
    await sendLog("WARNING", "Unable to join channel #moviegods: Banned from channel");
    await sendLog("ERROR", "Timed out waiting for NickServ authentication");
    expect(createdNotices).toEqual([
      "[DCCBOT] Unable to join channel #moviegods: Banned from channel",
      "[DCCBOT] Timed out waiting for NickServ authentication",
    ]);
  });

  test("INFO and DEBUG log entries do not notify", async () => {
    await sendLog("INFO", "Try to join channel: #moviegods");
    await sendLog("DEBUG", "internal detail");
    expect(createdNotices).toEqual([]);
  });

  test("identical log entries are deduplicated", async () => {
    const message = "Unable to join channel #mg-chat: You need a registered nick to join that channel.";
    await sendLog("WARNING", message);
    await sendLog("WARNING", message);
    await sendLog("WARNING", message);
    expect(createdNotices).toEqual(["[DCCBOT] " + message]);
  });

  test("non-log payloads are ignored", async () => {
    const socket = sockets[sockets.length - 1];
    socket.emit("message", { data: JSON.stringify({ type: "transfers", transfers: [] }) });
    socket.emit("message", { data: JSON.stringify({ type: "queues", queues: [] }) });
    socket.emit("message", { data: "not json" });
    socket.emit("message", { data: "" });
    await flushNotices();
    expect(createdNotices).toEqual([]);
  });
});
