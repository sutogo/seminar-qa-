// 参加者の画面．発表中と質疑中の両方を担う．
//
// 同一URLのまま，サーバから配信される state に従って表示を切り替える．
// 参加者に画面遷移を意識させない（requirements.md §5）．

import { api, storage } from "./api.js";
import { connect } from "./ws.js";
import { createOrderKeeper } from "./order.js";

// 「わからん」の受付間隔．サーバ側（store.py の CONFUSION_INTERVAL_SEC）と
// 同じ値にしておく．超過分はサーバが黙って捨てるため，
// こちらでも送らずに残り秒数を出す．押した実感と実際の記録をずらさないため．
const CONFUSION_INTERVAL_MS = 30000;

const $ = (id) => document.getElementById(id);

const state = {
  presentationId: null,
  presentationState: "waiting",
  confusionCount: 0,
  lastConfusionAt: 0,
  // 共感済みの質問ID．再読込しても押した状態を保つ．
  empathized: loadEmpathized(),
};

function loadEmpathized() {
  try {
    return new Set(JSON.parse(localStorage.getItem("seminar.empathy") || "[]"));
  } catch { return new Set(); }
}

function saveEmpathized() {
  localStorage.setItem("seminar.empathy", JSON.stringify([...state.empathized]));
}

// --------------------------------------------------------------------------
// 表示の切り替え
// --------------------------------------------------------------------------

const LABELS = { waiting: "開始待ち", live: "発表中", review: "質疑中" };

function applyState(msg) {
  const pres = msg.presentation;

  if (!pres) {
    state.presentationId = null;
    state.presentationState = "waiting";
    $("pres-title").textContent = "";
    $("pres-presenter").textContent = "";
  } else {
    // 発表が切り替わったら，前の発表の分を持ち越さない．
    if (pres.id !== state.presentationId) {
      state.presentationId = pres.id;
      state.confusionCount = 0;
      state.lastConfusionAt = 0;
      keeper.reset();
      renderConfusionCount();
      renderMine();
    }
    state.presentationState = pres.state;
    $("pres-title").textContent = pres.title;
    $("pres-presenter").textContent = pres.presenter;
  }

  $("state-label").textContent =
    `${LABELS[state.presentationState] || ""}　接続 ${msg.participant_count} 人`;

  const mode = pres ? pres.state : "waiting";
  $("view-waiting").hidden = mode !== "waiting";
  $("view-live").hidden = mode !== "live";
  $("view-review").hidden = mode !== "review";
}

// --------------------------------------------------------------------------
// わからん
// --------------------------------------------------------------------------

function renderConfusionCount() {
  const remain = Math.ceil(
    (CONFUSION_INTERVAL_MS - (Date.now() - state.lastConfusionAt)) / 1000
  );
  const el = $("confusion-count");

  if (remain > 0) {
    el.textContent = `送信 ${state.confusionCount} 回　次は ${remain} 秒後`;
    // 1秒ごとに残りを更新する．画面が派手に変化すると発表の妨げになるので，
    // 文字だけを書き換える．
    setTimeout(renderConfusionCount, 1000);
  } else if (state.confusionCount === 0) {
    el.textContent = "まだ押していません";
  } else {
    el.textContent = `送信 ${state.confusionCount} 回`;
  }
}

async function sendConfusion() {
  if (!state.presentationId) return;
  // 受付間隔の内側なら送らない．サーバが捨てる分を数えてしまうと
  // 手元のカウンタと記録がずれる．
  if (Date.now() - state.lastConfusionAt < CONFUSION_INTERVAL_MS) return;

  state.lastConfusionAt = Date.now();
  state.confusionCount += 1;
  renderConfusionCount();
  navigator.vibrate?.(15);

  try {
    await api.confusion(state.presentationId, storage.token);
  } catch (err) {
    console.error("わからんの送信に失敗した", err);
  }
}

// --------------------------------------------------------------------------
// 質問の投稿
// --------------------------------------------------------------------------

function renderMine() {
  const list = $("mine-list");
  const items = state.presentationId ? storage.myQuestions(state.presentationId) : [];
  list.replaceChildren();

  for (const q of items) {
    const card = document.createElement("div");
    card.className = "card";
    const body = document.createElement("div");
    body.className = "card-body";
    body.textContent = q.body;
    card.append(body);
    list.append(card);
  }
  $("mine").hidden = items.length === 0;
}

async function submitQuestion(textarea, button) {
  const body = textarea.value.trim();
  if (!body || !state.presentationId) return;

  button.disabled = true;
  try {
    const { question_id } = await api.postQuestion(
      state.presentationId, storage.token, body
    );
    // 発表中はサーバから質問が配信されないため，
    // 自分の投稿は応答から組み立てて localStorage に残す．
    storage.addMyQuestion(state.presentationId, { id: question_id, body });
    textarea.value = "";
    updateCounter();
    renderMine();
  } catch (err) {
    console.error("質問の送信に失敗した", err);
    alert("送信できませんでした．もう一度試してください．");
  } finally {
    button.disabled = false;
  }
}

function updateCounter() {
  $("question-count").textContent = `${$("question-body").value.length} / 200`;
}

// --------------------------------------------------------------------------
// 質疑中の一覧と共感
// --------------------------------------------------------------------------

// 投影側と同じ規則で表示順を固定する．
// 読もうとした瞬間に並びが変われば，押し間違えて別の質問に共感してしまう．
// 消化されたときだけ並べ替わるので，投影されている画面とも順番が一致する．
const keeper = createOrderKeeper();

function renderQuestions(items) {
  const list = $("questions");
  const { order } = keeper.arrange(items);
  const byId = new Map(items.map((q) => [q.id, q]));

  list.replaceChildren();

  for (const id of order) {
    const q = byId.get(id);
    if (!q) continue;
    const card = document.createElement("div");
    card.className = "card" + (q.resolved ? " resolved" : "");

    const head = document.createElement("div");
    head.className = "card-head";

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = q.grade_label;

    const btn = document.createElement("button");
    btn.className = "btn btn-empathy";
    btn.type = "button";
    const pressed = state.empathized.has(q.id);
    btn.setAttribute("aria-pressed", String(pressed));
    btn.textContent = `共感 ${q.empathy_count}`;
    btn.disabled = q.resolved;
    btn.addEventListener("click", () => toggleEmpathy(q.id));

    head.append(badge, btn);

    const body = document.createElement("div");
    body.className = "card-body";
    body.textContent = q.body;

    card.append(head, body);
    list.append(card);
  }
}

async function toggleEmpathy(qid) {
  const had = state.empathized.has(qid);
  // 先に手元の状態を変える．結果はサーバからの questions で上書きされる．
  if (had) state.empathized.delete(qid); else state.empathized.add(qid);
  saveEmpathized();

  try {
    if (had) await api.removeEmpathy(qid, storage.token);
    else await api.addEmpathy(qid, storage.token);
  } catch (err) {
    // 失敗したら手元の状態を戻す．握りつぶさない．
    if (had) state.empathized.add(qid); else state.empathized.delete(qid);
    saveEmpathized();
    console.error("共感の送信に失敗した", err);
  }
}

// --------------------------------------------------------------------------

async function main() {
  // 参加していなければ参加画面へ戻す．
  if (!storage.token || !storage.sessionId) {
    location.replace("/join");
    return;
  }

  // 手元のセッションとサーバのセッションが一致するかを確かめる．
  // 一致しなければサーバが再起動しており，トークンは既に無効である．
  // ここで気付かせないと，投稿だけが静かに失敗し続ける．
  try {
    const session = await api.currentSession();
    if (session.session_id !== storage.sessionId) {
      storage.clear();
      location.replace("/join?rejoin=1");
      return;
    }
  } catch (err) {
    // サーバに繋がらないだけなら，WebSocket の再接続に任せて進む．
    console.error("セッションの確認に失敗した", err);
  }

  $("confusion").addEventListener("click", sendConfusion);
  $("question-body").addEventListener("input", updateCounter);

  $("question-form").addEventListener("submit", (e) => {
    e.preventDefault();
    submitQuestion($("question-body"), e.target.querySelector("button"));
  });
  $("question-form-2").addEventListener("submit", (e) => {
    e.preventDefault();
    submitQuestion($("question-body-2"), e.target.querySelector("button"));
  });

  connect(storage.sessionId, storage.token, {
    onOpen: () => document.body.classList.remove("is-offline"),
    onClose: () => document.body.classList.add("is-offline"),
    onMessage: (msg) => {
      if (msg.type === "state") applyState(msg);
      else if (msg.type === "questions") renderQuestions(msg.items);
      // chart は質疑ビュー（投影）だけが使う．参加者の画面では読み捨てる．
    },
  });
}

main();
