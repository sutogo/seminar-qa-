// 質疑ビュー（スクリーン投影）．
//
// 共感数の多い順に質問を並べ，上から順に消化する．
//
// ただし**共感が入るたびに並べ替えることはしない．**
// 読んでいる最中にカードが動くと，投影では質問を追えなくなる．
// 共感数はその場で書き換え，カードの移動は「消化」を押した瞬間にまとめて行う．
// 発表者が次へ進むときが，並びが変わってよい唯一のタイミングである．
// 詳しくは order.js を参照．

import { api, storage } from "./api.js";
import { connect } from "./ws.js";
import { createOrderKeeper } from "./order.js";

const $ = (id) => document.getElementById(id);

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const keeper = createOrderKeeper();

// 質問ID -> カードの DOM．作り直さずに使い回す．
// 毎回作り直すと，共感数が変わるだけで出現アニメーションが走ってしまう．
const cards = new Map();

let latest = [];
let presentationId = null;

// 描画待ちの最新データと，実行中かどうか．
// 演出（320ms）が終わる前に次の更新が来ると，前の演出が中断されてガタつく．
// 実行中は最新のデータだけを覚えておき，終わってからまとめて描く．
let pending = null;
let running = false;

// --------------------------------------------------------------------------
// 描画
// --------------------------------------------------------------------------

function render(items) {
  latest = items;
  pending = items;
  if (!running) flush();
}

async function flush() {
  while (pending) {
    const items = pending;
    pending = null;

    const { order, reordered, pending: misplaced } = keeper.arrange(items);
    updateResortButton(misplaced);

    // 並びが変わらない更新では演出を出さない．数字だけが書き換わる．
    if (!reordered || !document.startViewTransition || reducedMotion) {
      paint(items, order);
      continue;
    }

    running = true;
    const transition = document.startViewTransition(() => paint(items, order));
    // 中断されると finished は reject する．演出の失敗で描画を止めない．
    await transition.finished.catch(() => {});
    running = false;
  }
}

function paint(items, order) {
  const list = $("questions");
  const byId = new Map(items.map((q) => [q.id, q]));

  // 消えた質問のカードを捨てる（発表が切り替わったときなど）．
  for (const [id, el] of cards) {
    if (!byId.has(id)) { el.remove(); cards.delete(id); }
  }

  if (items.length === 0) {
    list.replaceChildren(emptyMessage());
    return;
  }

  for (const id of order) {
    const q = byId.get(id);
    if (!q) continue;
    let card = cards.get(id);
    if (card) {
      updateCard(card, q);
    } else {
      card = buildCard(q);
      cards.set(id, card);
    }
    // 既にある要素を append し直すと移動になる．
    // 並びが同じであれば何も動かない．
    list.append(card);
  }
}

function emptyMessage() {
  const p = document.createElement("p");
  p.className = "muted";
  p.textContent = "質問はまだありません．";
  return p;
}

function buildCard(q) {
  const card = document.createElement("article");
  card.className = "card q-card";
  // カードごとに固有の名前を振ると，並び替えが個別に追従する．
  card.style.viewTransitionName = `q-${q.id}`;

  const main = document.createElement("div");
  main.className = "q-main";
  const badge = document.createElement("span");
  badge.className = "badge";
  const body = document.createElement("div");
  body.className = "card-body";
  main.append(badge, body);

  const side = document.createElement("div");
  side.className = "q-side";
  const num = document.createElement("div");
  num.className = "empathy-num";
  const label = document.createElement("div");
  label.className = "faint";
  label.textContent = "共感";
  const btn = document.createElement("button");
  btn.className = "btn btn-resolve";
  btn.type = "button";
  btn.addEventListener("click", () => toggleResolve(q.id));
  side.append(num, label, btn);

  card.append(main, side);
  card._parts = { badge, body, num, btn };
  updateCard(card, q);
  return card;
}

/** カードの中身だけを書き換える．要素は作り直さない． */
function updateCard(card, q) {
  const { badge, body, num, btn } = card._parts;
  if (badge.textContent !== q.grade_label) badge.textContent = q.grade_label;
  if (body.textContent !== q.body) body.textContent = q.body;
  if (num.textContent !== String(q.empathy_count)) num.textContent = String(q.empathy_count);
  btn.textContent = q.resolved ? "戻す" : "消化";
  card.classList.toggle("resolved", q.resolved);
}

async function toggleResolve(qid) {
  const q = latest.find((item) => item.id === qid);
  if (!q) return;
  try {
    // 消化状態が変われば order.js が並べ替えを許す．
    // ここで手元の DOM を書き換えると，配信と二重に描いて並びが乱れる．
    if (q.resolved) await api.unresolve(qid);
    else await api.resolve(qid);
  } catch (err) {
    console.error("消化状態の変更に失敗した", err);
  }
}

// --------------------------------------------------------------------------
// 並べ替えの指示
// --------------------------------------------------------------------------

function updateResortButton(misplaced) {
  const btn = $("resort");
  btn.hidden = misplaced === 0;
  btn.textContent = `並べ替える（${misplaced}件）`;
}

function resortNow() {
  keeper.requestResort();
  render(latest);
}

// --------------------------------------------------------------------------
// 状態
// --------------------------------------------------------------------------

function applyState(msg) {
  const pres = msg.presentation;
  const meta = [];

  if (pres) {
    if (pres.id !== presentationId) {
      // 発表が変わったら並びを組み直す．
      presentationId = pres.id;
      keeper.reset();
      cards.clear();
      $("questions").replaceChildren();
    }
    $("pres-title").textContent = pres.title;
    $("pres-presenter").textContent = pres.presenter;
    meta.push(`質問 ${pres.question_count} 件`);
  } else {
    $("pres-title").textContent = "質疑";
    $("pres-presenter").textContent = "発表を待っています";
    presentationId = null;
    keeper.reset();
    cards.clear();
    render([]);
  }
  meta.push(`接続中 ${msg.participant_count} 人`);
  $("meta").textContent = meta.join("　");
}

// --------------------------------------------------------------------------

function main() {
  // 投影用のため参加は不要である．トークンを持たずに接続する．
  $("resort").addEventListener("click", resortNow);

  connect(storage.sessionId || "", storage.token || "", {
    onOpen: () => document.body.classList.remove("is-offline"),
    onClose: () => document.body.classList.add("is-offline"),
    onMessage: (msg) => {
      if (msg.type === "state") applyState(msg);
      else if (msg.type === "questions") render(msg.items);
      // chart は段階5 で扱う．
    },
  });
}

main();
