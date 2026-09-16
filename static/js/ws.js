// WebSocket 接続と自動再接続．
//
// サーバ発の一方向配信である．こちらからは何も送らない．
// 切断時は1秒後から指数的に間隔を延ばして再接続する（上限10秒）．
// 再接続に成功するとサーバが state を（質疑中なら questions と chart も）
// 再送するため，こちら側で差分を保持する必要はない．

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;

/**
 * @param {string} sessionId
 * @param {string} token
 * @param {{onMessage: (msg: object) => void,
 *          onOpen?: () => void,
 *          onClose?: () => void}} handlers
 */
export function connect(sessionId, token, handlers) {
  let delay = RECONNECT_MIN_MS;
  let socket = null;
  let closedByUs = false;

  function open() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${location.host}/ws`
      + `?s=${encodeURIComponent(sessionId)}&t=${encodeURIComponent(token)}`;

    socket = new WebSocket(url);

    socket.addEventListener("open", () => {
      delay = RECONNECT_MIN_MS;  // 成功したら間隔を戻す
      handlers.onOpen?.();
    });

    socket.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (err) {
        // 壊れたメッセージは握りつぶさずに記録する．接続自体は保つ．
        console.error("受信したメッセージを解釈できない", err, event.data);
        return;
      }
      handlers.onMessage(msg);
    });

    socket.addEventListener("close", () => {
      handlers.onClose?.();
      if (closedByUs) return;
      setTimeout(open, delay);
      delay = Math.min(delay * 2, RECONNECT_MAX_MS);
    });

    // error のあとには必ず close が来るので，ここでは再接続を仕掛けない．
    socket.addEventListener("error", () => socket.close());
  }

  open();

  return {
    close() {
      closedByUs = true;
      socket?.close();
    },
  };
}
