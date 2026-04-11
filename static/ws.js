(function () {
  const noop = () => {};
  const scriptSuffix = "/static/ws.js";

  function getBasePath() {
    const currentScript = document.currentScript;
    if (currentScript && currentScript.src) {
      const scriptUrl = new URL(currentScript.src, window.location.href);
      const path = scriptUrl.pathname;
      if (path.endsWith(scriptSuffix)) {
        return `${path.slice(0, -scriptSuffix.length)}/`;
      }
    }

    // Fallback to current page path when script URL inspection is unavailable.
    const pagePath = window.location.pathname || "/";
    if (pagePath.endsWith("/")) {
      return pagePath;
    }
    const slash = pagePath.lastIndexOf("/");
    return `${pagePath.slice(0, Math.max(0, slash))}/`;
  }

  function createDccbotSocket(options = {}) {
    const callbacks = {
      onLog: noop,
      onTransfers: noop,
      onCommandResponse: noop,
      onError: noop,
      onOpen: noop,
      onClose: noop,
      ...options,
    };

    const isSecure = window.location.protocol === "https:";
    const protocol = isSecure ? "wss:" : "ws:";
    const host = window.location.host;
    const basePath = getBasePath();
    const path = new URL("ws", `${window.location.origin}${basePath}`).pathname;
    const wsUrl = `${protocol}//${host}${path}`;
    const socket = new WebSocket(wsUrl);

    socket.onopen = (event) => {
      callbacks.onOpen(event);
    };

    socket.onmessage = (event) => {
      if (!event.data) {
        return;
      }

      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (err) {
        callbacks.onCommandResponse(event.data);
        return;
      }

      if (payload.type === "log") {
        callbacks.onLog(payload);
      } else if (payload.type === "transfers") {
        callbacks.onTransfers(payload);
      } else {
        callbacks.onCommandResponse(payload);
      }
    };

    socket.onerror = (event) => {
      callbacks.onError(event);
    };

    socket.onclose = (event) => {
      callbacks.onClose(event);
    };

    return socket;
  }

  function createDccbotUrl(pathname) {
    const normalizedPath = String(pathname || "").replace(/^\/+/, "");
    return new URL(normalizedPath, `${window.location.origin}${getBasePath()}`).toString();
  }

  window.createDccbotSocket = createDccbotSocket;
  window.createDccbotUrl = createDccbotUrl;
})();
