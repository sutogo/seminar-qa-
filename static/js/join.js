// 参加（学年選択）．
//
// 参加時にサーバがランダムなトークンを発行し，localStorage に保存する．
// 以降の全ての POST にこのトークンを添える．
// 操作はゼミ開始時の1回のみで，再読込しても学年は維持される（FR-02）．

import { api, storage, ApiError } from "./api.js";

// 学年の表示名．teacher だけ日本語にする．
const GRADE_LABELS = {
  B3: "B3", B4: "B4",
  M1: "M1", M2: "M2",
  D1: "D1", D2: "D2", D3: "D3",
  teacher: "教員",
};

const $ = (id) => document.getElementById(id);

function showError(text) {
  const el = $("error");
  el.textContent = text;
  el.hidden = false;
}

function goLive() {
  location.href = "/live";
}

async function main() {
  let session;
  try {
    session = await api.currentSession();
  } catch (err) {
    showError("サーバに繋がりません．Wi-Fi の接続とURLを確認してください．");
    console.error(err);
    return;
  }

  $("session-title").textContent = session.title + (session.date ? `　${session.date}` : "");

  // 再参加（サーバが再起動してトークンが無効になった）ときの案内．
  if (new URLSearchParams(location.search).has("rejoin")) {
    showError("サーバが再起動しました．学年をもう一度選んでください．");
  }

  // QR に付いてくる ?s= は，将来セッションが複数になったときのためのもの．
  // 現状はサーバ起動時に1つだけ自動生成されるので，取り違えは起きない．
  const sessionId = new URLSearchParams(location.search).get("s") || session.session_id;

  // 既に参加済みなら選び直さずに進める．
  if (storage.token && storage.sessionId === sessionId) {
    $("current-grade").textContent = GRADE_LABELS[storage.grade] || storage.grade;
    $("already").hidden = false;
    $("continue").addEventListener("click", goLive);
    $("rechoose").addEventListener("click", () => {
      $("already").hidden = true;
      $("choose").hidden = false;
    });
    return;
  }

  renderGrades(session.grades, sessionId);
  $("choose").hidden = false;
}

function renderGrades(grades, sessionId) {
  const box = $("grades");
  for (const grade of grades) {
    const btn = document.createElement("button");
    btn.className = "btn";
    btn.type = "button";
    btn.textContent = GRADE_LABELS[grade] || grade;
    btn.addEventListener("click", () => choose(sessionId, grade, box));
    box.append(btn);
  }
}

async function choose(sessionId, grade, box) {
  // 二重送信を防ぐ．通信中に連打されるとトークンが複数発行される．
  for (const b of box.querySelectorAll("button")) b.disabled = true;

  try {
    const { token } = await api.join(sessionId, grade);
    storage.token = token;
    storage.sessionId = sessionId;
    storage.grade = grade;
    goLive();
  } catch (err) {
    for (const b of box.querySelectorAll("button")) b.disabled = false;
    if (err instanceof ApiError && err.status === 404) {
      showError("セッションが見つかりません．サーバが再起動した可能性があります．");
    } else {
      showError("参加に失敗しました．もう一度押してください．");
    }
    console.error(err);
  }
}

main();
