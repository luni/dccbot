/**
 * Shared helpers for userscript tests.
 * The userscript is an IIFE keyed on window.location.hostname, so each test
 * file must set the jsdom URL via @jest-environment-options and install the
 * GM_* stubs BEFORE requiring the script.
 */

const SCRIPT_PATH = "../../../userscript/add-dccbot-btn.js";

function installGmStubs(apiEndpoint) {
  // jsdom doesn't implement rAF unless pretendToBeVisual is enabled
  if (!window.requestAnimationFrame) {
    window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  }
  const store = { dccbot_api: apiEndpoint || "http://localhost:8080" };
  window.GM_getValue = jest.fn((key, fallback) =>
    key in store ? store[key] : fallback
  );
  window.GM_setValue = jest.fn((key, value) => {
    store[key] = value;
  });
  window.GM_registerMenuCommand = jest.fn();
  window.GM_xmlhttpRequest = jest.fn((request) => {
    if (request && request.onerror) {
      request.onerror({ error: "stubbed" });
    }
  });
  return store;
}

function loadScript() {
  jest.resetModules();
  require(SCRIPT_PATH);
}

function dccbotButtons() {
  return Array.from(document.querySelectorAll(".dccbot-btn"));
}

function lastApiRequest() {
  const calls = window.GM_xmlhttpRequest.mock.calls;
  return calls.length ? calls[calls.length - 1][0] : null;
}

function flushMutations() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

module.exports = {
  installGmStubs,
  loadScript,
  dccbotButtons,
  lastApiRequest,
  flushMutations,
};
