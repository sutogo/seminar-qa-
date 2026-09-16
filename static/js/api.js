// REST 呼び出しの薄いラッパ．
//
// クライアントからサーバへの送信は全て HTTP で行う．
// WebSocket はサーバからの受信専用であり，こちらからは何も送らない（ws.js）．

/** 参加者を識別するトークン等を localStorage に保持する． */
export const storage = {
  get token() { return localStorage.getItem("seminar.token") || ""; },
  set token(v) { localStorage.setItem("seminar.token", v); },

  get sessionId() { return localStorage.getItem("seminar.session") || ""; },
  set sessionId(v) { localStorage.setItem("seminar.session", v); },

  get grade() { return localStorage.getItem("seminar.grade") || ""; },
  set grade(v) { localStorage.setItem("seminar.grade", v); },

  // 自分の投稿一覧．発表中はサーバから質問が配信されないため，
  // POST の応答から組み立ててここに残す（docs/protocol.md）．
  //
  // キーにセッションIDを含める．発表IDは p_01 から採番し直されるので，
  // 発表IDだけを鍵にすると，サーバを再起動したあとの新しい発表に
  // 前回の投稿が紛れ込む．
  _myKey(presentationId) { return `${this.sessionId}:${presentationId}`; },

  myQuestions(presentationId) {
    try {
      const all = JSON.parse(localStorage.getItem("seminar.myQuestions") || "{}");
      return all[this._myKey(presentationId)] || [];
    } catch { return []; }
  },
  addMyQuestion(presentationId, question) {
    let all = {};
    try { all = JSON.parse(localStorage.getItem("seminar.myQuestions") || "{}"); }
    catch { all = {}; }
    const key = this._myKey(presentationId);
    all[key] = [...(all[key] || []), question];
    localStorage.setItem("seminar.myQuestions", JSON.stringify(all));
  },

  clear() {
    for (const k of ["seminar.token", "seminar.session", "seminar.grade"]) {
      localStorage.removeItem(k);
    }
  },
};

/** 応答が JSON でない場合（204 など）は null を返す． */
async function request(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    // DELETE でも本文を送る．fetch は本文付きの DELETE を送れるが，
    // ライブラリによっては簡易メソッドが本文を落とすので，
    // ここでは fetch を直接使っている（docs/protocol.md）．
    options.body = JSON.stringify(body);
  }

  const res = await fetch(path, options);
  if (!res.ok) {
    // 401 はトークンが通らなくなったとき．サーバの再起動が主な原因である．
    // 黙って失敗させると，参加者には「送ったのに反映されない」としか見えない．
    if (res.status === 401 && !location.pathname.startsWith("/join")) {
      storage.clear();
      location.replace("/join?rejoin=1");
      return null;
    }
    const detail = await res.text().catch(() => "");
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(`HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export const api = {
  currentSession: () => request("GET", "/api/sessions/current"),

  join: (sessionId, grade) =>
    request("POST", `/api/sessions/${sessionId}/participants`, { grade }),

  createPresentation: (sessionId, presenter, title) =>
    request("POST", `/api/sessions/${sessionId}/presentations`, { presenter, title }),

  start: (pid) => request("POST", `/api/presentations/${pid}/start`),
  end:   (pid) => request("POST", `/api/presentations/${pid}/end`),
  close: (pid) => request("POST", `/api/presentations/${pid}/close`),

  confusion: (pid, token) =>
    request("POST", `/api/presentations/${pid}/confusion`, { token }),

  postQuestion: (pid, token, body) =>
    request("POST", `/api/presentations/${pid}/questions`, { token, body }),

  addEmpathy:    (qid, token) => request("POST",   `/api/questions/${qid}/empathy`, { token }),
  removeEmpathy: (qid, token) => request("DELETE", `/api/questions/${qid}/empathy`, { token }),

  resolve:   (qid) => request("POST",   `/api/questions/${qid}/resolve`),
  unresolve: (qid) => request("DELETE", `/api/questions/${qid}/resolve`),
};
