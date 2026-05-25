/**
 * @jest-environment jsdom
 */

describe("ws.js", () => {
  beforeEach(() => {
    delete window.createDccbotSocket;
    delete window.createDccbotUrl;
  });

  afterEach(() => {
    jest.resetModules();
    delete window.__testLocation;
  });

  function loadWsModule() {
    require("../../static/ws.js");
  }

  describe("createDccbotSocket", () => {
    let mockSocket;
    let MockWebSocket;
    let openHandler;
    let messageHandler;
    let errorHandler;
    let closeHandler;

    beforeEach(() => {
      mockSocket = {
        onopen: null,
        onmessage: null,
        onerror: null,
        onclose: null,
      };

      MockWebSocket = jest.fn().mockImplementation((url) => {
        mockSocket.url = url;
        return mockSocket;
      });

      global.WebSocket = MockWebSocket;

      // Simulate page at http://localhost:8080/
      window.__testLocation = {
        protocol: "http:",
        host: "localhost:8080",
        origin: "http://localhost:8080",
        pathname: "/",
        href: "http://localhost:8080/",
      };

      // Remove any existing currentScript
      Object.defineProperty(document, "currentScript", {
        value: null,
        writable: true,
        configurable: true,
      });
    });

    afterEach(() => {
      delete global.WebSocket;
    });

    test("creates WebSocket with correct ws URL", () => {
      loadWsModule();
      window.createDccbotSocket({});
      expect(MockWebSocket).toHaveBeenCalledWith("ws://localhost:8080/ws");
    });

    test("uses wss when page is served over https", () => {
      window.__testLocation.protocol = "https:";
      window.__testLocation.origin = "https://localhost:8080";
      loadWsModule();
      window.createDccbotSocket({});
      expect(MockWebSocket).toHaveBeenCalledWith("wss://localhost:8080/ws");
    });

    test("invokes onOpen callback when socket opens", () => {
      const onOpen = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onOpen });
      mockSocket.onopen({ type: "open" });
      expect(onOpen).toHaveBeenCalledWith({ type: "open" });
    });

    test("dispatches log messages to onLog callback", () => {
      const onLog = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onLog });
      mockSocket.onmessage({ data: '{"type":"log","message":"hello"}' });
      expect(onLog).toHaveBeenCalledWith(
        expect.objectContaining({ type: "log", message: "hello" })
      );
    });

    test("dispatches transfer messages to onTransfers callback", () => {
      const onTransfers = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onTransfers });
      mockSocket.onmessage({ data: '{"type":"transfers","transfers":[]}' });
      expect(onTransfers).toHaveBeenCalledWith(
        expect.objectContaining({ type: "transfers", transfers: [] })
      );
    });

    test("dispatches unknown JSON messages to onCommandResponse", () => {
      const onCommandResponse = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onCommandResponse });
      mockSocket.onmessage({ data: '{"status":"ok","message":"done"}' });
      expect(onCommandResponse).toHaveBeenCalledWith(
        expect.objectContaining({ status: "ok", message: "done" })
      );
    });

    test("dispatches non-JSON raw text to onCommandResponse", () => {
      const onCommandResponse = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onCommandResponse });
      mockSocket.onmessage({ data: "raw server text" });
      expect(onCommandResponse).toHaveBeenCalledWith("raw server text");
    });

    test("ignores empty message data", () => {
      const onLog = jest.fn();
      const onTransfers = jest.fn();
      const onCommandResponse = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onLog, onTransfers, onCommandResponse });
      mockSocket.onmessage({ data: "" });
      expect(onLog).not.toHaveBeenCalled();
      expect(onTransfers).not.toHaveBeenCalled();
      expect(onCommandResponse).not.toHaveBeenCalled();
    });

    test("invokes onError callback on socket error", () => {
      const onError = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onError });
      mockSocket.onerror({ type: "error" });
      expect(onError).toHaveBeenCalledWith({ type: "error" });
    });

    test("invokes onClose callback on socket close", () => {
      const onClose = jest.fn();
      loadWsModule();
      window.createDccbotSocket({ onClose });
      mockSocket.onclose({ type: "close" });
      expect(onClose).toHaveBeenCalledWith({ type: "close" });
    });

    test("uses noop when no callbacks provided", () => {
      loadWsModule();
      expect(() => {
        window.createDccbotSocket({});
        mockSocket.onopen({});
        mockSocket.onmessage({ data: '{"type":"log"}' });
        mockSocket.onerror({});
        mockSocket.onclose({});
      }).not.toThrow();
    });
  });

  describe("createDccbotUrl", () => {
    beforeEach(() => {
      window.__testLocation = {
        protocol: "http:",
        host: "localhost:8080",
        origin: "http://localhost:8080",
        pathname: "/",
        href: "http://localhost:8080/",
      };
    });

    test("builds URL from pathname", () => {
      loadWsModule();
      expect(window.createDccbotUrl("join")).toBe("http://localhost:8080/join");
    });

    test("strips leading slashes from pathname", () => {
      loadWsModule();
      expect(window.createDccbotUrl("/join")).toBe("http://localhost:8080/join");
    });

    test("handles multiple leading slashes", () => {
      loadWsModule();
      expect(window.createDccbotUrl("//join")).toBe("http://localhost:8080/join");
    });

    test("handles empty pathname", () => {
      loadWsModule();
      expect(window.createDccbotUrl("")).toBe("http://localhost:8080/");
    });

    test("handles nested paths", () => {
      loadWsModule();
      expect(window.createDccbotUrl("api/v1/status")).toBe(
        "http://localhost:8080/api/v1/status"
      );
    });
  });

  describe("getBasePath", () => {
    test("derives base path from currentScript src", () => {
      window.__testLocation = {
        protocol: "http:",
        host: "localhost:8080",
        origin: "http://localhost:8080",
        pathname: "/",
        href: "http://localhost:8080/",
      };

      const script = document.createElement("script");
      script.src = "http://localhost:8080/static/ws.js";
      Object.defineProperty(document, "currentScript", {
        value: script,
        writable: true,
        configurable: true,
      });

      loadWsModule();
      expect(window.createDccbotUrl("join")).toBe("http://localhost:8080/join");
    });

    test("falls back to window.location.pathname when no currentScript", () => {
      window.__testLocation = {
        protocol: "http:",
        host: "localhost:8080",
        origin: "http://localhost:8080",
        pathname: "/dccbot/",
        href: "http://localhost:8080/dccbot/",
      };

      Object.defineProperty(document, "currentScript", {
        value: null,
        writable: true,
        configurable: true,
      });

      loadWsModule();
      expect(window.createDccbotUrl("join")).toBe("http://localhost:8080/dccbot/join");
    });

    test("handles non-trailing-slash pathname fallback", () => {
      window.__testLocation = {
        protocol: "http:",
        host: "localhost:8080",
        origin: "http://localhost:8080",
        pathname: "/dccbot/index.html",
        href: "http://localhost:8080/dccbot/index.html",
      };

      Object.defineProperty(document, "currentScript", {
        value: null,
        writable: true,
        configurable: true,
      });

      loadWsModule();
      expect(window.createDccbotUrl("join")).toBe("http://localhost:8080/dccbot/join");
    });
  });
});
