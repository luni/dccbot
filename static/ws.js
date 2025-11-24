(function () {
  const noop = () => {};

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
    const path = new URL("./ws", window.location.href).pathname;
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

  window.createDccbotSocket = createDccbotSocket;
})();
