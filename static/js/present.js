// 発表者ビュー．
//
// 数字を大きく見せるだけの画面である．装飾は入れない．
// **発表中は質問の本文を出さない．** 件数のみを表示する．
// 発表者が発表しながら批判的な質問を読んで動揺することを防ぐためであり，
// 意図した仕様である（CLAUDE.md）．
//
// セッションはサーバ起動時に自動生成されるので，ここでは作らない．
// 発表（発表者名とタイトル）の登録だけを行う．

import { api, storage } from "./api.js";
import { connect } from "./ws.js";

const $ = (id) => document.getElementById(id);

// 質疑ビューのタブにも名前を付けるが，使い回しをブラウザ任せにしない．
// 開いたタブの参照を自分で保持し，生きていれば開き直さない．
// 発表ごとにタブが増えないことを，こちら側で保証する．
const REVIEW_TAB = "seminar-review";
let reviewTab = null;

let sessionId = "";
let current = null;      // 進行中の発表
// 経過時間の起点．state は状態が変わったときにしか届かないので，
// 受け取った値を起点にして手元で数える．
let elapsedBase = 0;
let elapsedAt = 0;

// --------------------------------------------------------------------------
// 起動
// --------------------------------------------------------------------------

async function main() {
  warnIfLocalhost();

  const session = await api.currentSession();
  sessionId = session.session_id;

  $("title").textContent = session.title || "ゼミ質疑";
  $("subtitle").textContent = session.date || "";

  const qrUrl = session.qr_png_url;
  $("qr").src = qrUrl;
  $("qr-small").src = qrUrl;
  $("join-url").textContent = session.join_url;
  $("join-url-small").textContent = session.join_url;

  $("register").addEventListener("submit", register);
  // 人が押したときは前面に出す．発表の開始に伴う場合は出さない．
  $("open-review").addEventListener("click", () => openReviewTab(true));
  $("advance").addEventListener("click", advance);
  $("copy").addEventListener("click", copyRecord);
  $("next").addEventListener("click", () => {
    // 前の発表者の入力を必ず捨ててから待機へ戻す．
    // 常設PCで使い回す場合，残ったまま次の人が開始すると
    // 記録に別人の名前が載る．
    clearRegisterForm();
    showView("waiting");
  });

  setInterval(tickElapsed, 1000);

  // 発表者ビューは参加者ではないため，トークンを持たずに接続する．
  connect(sessionId, "", {
    onOpen: () => document.body.classList.remove("is-offline"),
    onClose: () => document.body.classList.add("is-offline"),
    onMessage: (msg) => { if (msg.type === "state") applyState(msg); },
  });
}

/**
 * localhost で開くと，QRも localhost を指してスマートフォンから繋がらない．
 * 参加用URLはリクエストの Host ヘッダから組み立てているためである．
 * 当日にこれをやると，全員が参加できないまま原因が分からなくなる．
 */
function warnIfLocalhost() {
  const host = location.hostname;
  if (host === "localhost" || host === "127.0.0.1" || host === "[::1]") {
    $("warn-host").textContent = location.origin;
    $("localhost-warn").hidden = false;
  }
}

// --------------------------------------------------------------------------
// 状態
// --------------------------------------------------------------------------

function applyState(msg) {
  $("participants").textContent = String(msg.participant_count);

  const pres = msg.presentation;
  current = pres;

  if (!pres) {
    // 記録を表示している最中に待機へ戻すと，記録が読めなくなる．
    if (!$("view-record").hidden) return;
    showView("waiting");
    return;
  }

  elapsedBase = pres.elapsed_sec;
  elapsedAt = Date.now();

  $("questions").textContent = String(pres.question_count);
  $("subtitle").textContent = `${pres.title}　${pres.presenter}`;

  if (pres.state === "live") {
    $("advance").textContent = "発表終了（質疑へ）";
    $("hint").textContent =
      "発表中は質問の本文を表示しません．件数のみです．";
  } else {
    $("advance").textContent = "質疑終了";
    $("hint").textContent =
      "スクリーンを /review に切り替えてください．共感の多い順に並びます．";
  }
  showView("running");
  tickElapsed();
}

function tickElapsed() {
  if (!current || !elapsedAt) return;
  const total = elapsedBase + Math.floor((Date.now() - elapsedAt) / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  $("elapsed").textContent = `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * 発表の登録フォームを空にする．
 *
 * 待機ビューを表示するたびに消すのは危ない．参加者が増減すると
 * state が届き，発表が未登録の間は待機ビューが描き直される．
 * そこで消すと，発表者が入力している最中に文字が消える．
 * 消すのは「開始した直後」と「次の発表を登録する を押したとき」だけにする．
 */
function clearRegisterForm() {
  $("presenter").value = "";
  $("pres-title").value = "";
}

function showView(name) {
  $("view-waiting").hidden = name !== "waiting";
  $("view-running").hidden = name !== "running";
  $("view-record").hidden = name !== "record";
}

// --------------------------------------------------------------------------
// 操作
// --------------------------------------------------------------------------

/**
 * 質疑ビューを別タブで開く．
 *
 * **await を挟む前に呼ぶこと．** 非同期の待ちを跨ぐとクリック操作との
 * 結び付きが切れ，ブラウザのポップアップブロックに掛かる．
 *
 * 既に開いているタブがあれば開き直さない．同名のタブを使い回す挙動は
 * ブラウザに委ねると確実でないため，参照を自分で持って判断する．
 *
 * @param {boolean} focusIt 人が押したときは true．前面に出す
 * @returns {boolean} 質疑ビューが利用できる状態か
 */
function openReviewTab(focusIt = false) {
  if (reviewTab && !reviewTab.closed) {
    $("popup-hint").hidden = true;
    if (focusIt) reviewTab.focus();
    return true;
  }

  reviewTab = window.open("/review", REVIEW_TAB);
  $("popup-hint").hidden = Boolean(reviewTab);
  if (!reviewTab) return false;

  // 開いたあと元のタブへ戻す．発表者はプロジェクタにスライドを映すため，
  // 開始した瞬間に質疑ビューが前面へ出ると，映してはいけない画面が映る．
  if (!focusIt) window.focus();
  return true;
}

async function register(event) {
  event.preventDefault();
  const presenter = $("presenter").value.trim();
  const title = $("pres-title").value.trim();
  if (!presenter || !title) return;

  // 通信の前に開く．await のあとでは間に合わない．
  openReviewTab();

  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    const { presentation_id } = await api.createPresentation(sessionId, presenter, title);
    // 未完了の発表が残っていても，サーバ側が自動的に閉じる（FR-03）．
    await api.start(presentation_id);
    clearRegisterForm();
  } catch (err) {
    console.error("発表の開始に失敗した", err);
    alert("発表を開始できませんでした．");
  } finally {
    button.disabled = false;
  }
}

async function advance() {
  if (!current) return;
  const toReview = current.state === "live";

  // 取り消せない操作である．発表中に誤って押すと，
  // その場で全員の画面が質疑モードに変わる．
  const message = toReview
    ? "発表を終了して質疑に移ります．よろしいですか．"
    : "質疑を終了します．記録を出力し，発表を完了します．";
  if (!confirm(message)) return;

  const button = $("advance");
  button.disabled = true;
  try {
    if (toReview) {
      await api.end(current.id);
    } else {
      const result = await api.close(current.id);
      showRecord(result);
    }
  } catch (err) {
    console.error("状態の変更に失敗した", err);
    alert("操作に失敗しました．");
  } finally {
    button.disabled = false;
  }
}

// --------------------------------------------------------------------------
// 記録
// --------------------------------------------------------------------------

function showRecord(result) {
  $("record").value = result.record_md;
  $("teams-status").textContent = result.teams_posted
    ? "Teams へ投稿しました．"
    : "Teams へは投稿していません（Webhook が未設定，または失敗）．下の記録を控えてください．";
  $("copy-status").textContent = "";
  showView("record");
}

async function copyRecord() {
  const text = $("record").value;

  // navigator.clipboard は HTTPS か localhost でしか使えない．
  // 当日は http://<LANのIP>:8000 で開くため，ここは基本的に通らない．
  // 通らなかった場合は選択状態にして，利用者が自分でコピーできるようにする．
  try {
    if (navigator.clipboard && isSecureContext) {
      await navigator.clipboard.writeText(text);
      $("copy-status").textContent = "コピーしました．";
      return;
    }
  } catch (err) {
    console.error("クリップボードへの書き込みに失敗した", err);
  }

  const area = $("record");
  area.focus();
  area.select();
  $("copy-status").textContent =
    "全文を選択しました．⌘C（Windows は Ctrl+C）でコピーしてください．";
}

main();
