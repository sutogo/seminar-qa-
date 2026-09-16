#!/usr/bin/env python3
"""学内Wi-Fi のクライアント間通信を確認する簡易ツール．

実装に着手する前（段階0）に実行し，スマートフォンからサーバPCへ
HTTP が届くかどうかを確認する．届かない場合は設計そのものを
変える必要があるため，最初に潰しておく．

標準ライブラリのみで動作する．`pip install -r requirements.txt` の前でも実行できる．
`qrcode` が入っていれば端末にQRコードも表示する．

使い方：

    python3 tools/check_network.py

サーバPCで実行し，表示されたURL（またはQR）へスマートフォンからアクセスする．
"""

from __future__ import annotations

import argparse
import dataclasses
import http.server
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# ツールが待ち受けるポート．本番の uvicorn と同じ 8000 を既定とする．
# 同じポートで試さないと，ファイアウォールの許可状態が本番と一致しない．
DEFAULT_PORT = 8000
DEFAULT_TIMEOUT_SEC = 180


@dataclasses.dataclass
class Hit:
    """スマートフォンからの着信1件．診断のためだけに保持し，ファイルには残さない．"""

    client_ip: str
    user_agent: str
    host_header: str
    at: float


# 着信を溜めるリスト．HTTPハンドラ（別スレッド）から追記する．
hits: list[Hit] = []


# --------------------------------------------------------------------------
# ネットワーク情報の取得
# --------------------------------------------------------------------------

def primary_ipv4() -> str | None:
    """外向き通信に使われる自分のIPv4アドレスを得る．

    8.8.8.8 へ UDP ソケットを「接続」するが，UDP なのでパケットは送出されない．
    OS のルーティング表を引かせるためだけの常套手段である．
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return str(s.getsockname()[0])
    except OSError:
        return None
    finally:
        s.close()


def list_ipv4_addresses() -> list[tuple[str, str]]:
    """全インタフェースのIPv4アドレスを (インタフェース名, IP) の一覧で返す．

    有線とWi-Fiの両方が繋がっている場合や，VPN・仮想アダプタが挟まっている場合に
    「どのIPを使えばよいか」を人間が判断できるようにするためのもの．
    """
    found: list[tuple[str, str]] = []
    system = platform.system()

    try:
        if system == "Darwin":
            # macOS：ifconfig の出力を素朴に解析する．
            # 行頭から始まる行がインタフェース名，字下げされた inet 行がアドレス．
            out = subprocess.run(
                ["ifconfig"], capture_output=True, text=True, timeout=5
            ).stdout
            iface = "?"
            for line in out.splitlines():
                if line and not line[0].isspace():
                    iface = line.split(":")[0]
                elif line.strip().startswith("inet "):
                    found.append((iface, line.split()[1]))
        elif system == "Linux":
            out = subprocess.run(
                ["ip", "-o", "-4", "addr", "show"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    found.append((parts[1], parts[3].split("/")[0]))
    except (OSError, subprocess.SubprocessError):
        pass  # 取得できなくても致命的ではない．後段の primary_ipv4 で足りる．

    if not found:
        # Windows など，上記が使えない環境向けの代替手段．
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                found.append(("?", ip))
        except OSError:
            pass

    # ループバックとリンクローカル（169.254.x.x）は参加者からは使えないので除く．
    return [
        (i, a) for i, a in found
        if not a.startswith("127.") and not a.startswith("169.254.")
    ]


def macos_firewall_state() -> str | None:
    """macOS のアプリケーションファイアウォールの状態を返す．

    有効だと Python の待ち受けが黙って遮断されることがあり，
    プライバシーセパレータと区別がつかない「届かない」の原因になる．
    macOS 以外では None を返す．
    """
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(
            ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def port_is_free(port: int) -> bool:
    """指定ポートが空いているかを確認する．uvicorn が起動中だと塞がっている．"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# --------------------------------------------------------------------------
# 待ち受けサーバ
# --------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>疎通確認</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;
      background:#121211;color:#f2f0ea;
      font-family:"Hiragino Sans","Yu Gothic",sans-serif;text-align:center;padding:24px}}
 .mark{{font-size:88px;line-height:1;color:#4a9d8f}}
 h1{{font-size:24px;margin:16px 0 8px}}
 p{{color:#a8a59c;font-size:15px;line-height:1.8;margin:0}}
 code{{color:#e2613c;font-family:ui-monospace,Menlo,monospace}}
</style></head>
<body><div>
 <div class="mark">&#10003;</div>
 <h1>届きました</h1>
 <p>この端末からサーバPCへ接続できています．<br>
 サーバPC側の画面に結果が表示されているので確認してください．</p>
 <p style="margin-top:20px"><code>{client_ip}</code></p>
</div></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    """着信を記録して確認用ページを返すだけの最小ハンドラ．"""

    # HTTP/1.0 だと接続が毎回切れて着信が二重に見えることがあるため明示する．
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802（標準ライブラリ側の命名に従う）
        client_ip = self.client_address[0]

        # 自己テスト用のパス．サーバPC自身からの確認なので着信には数えない．
        if self.path == "/__selftest":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # ブラウザが勝手に取りに来るものは着信として数えない．
        if self.path in ("/favicon.ico", "/apple-touch-icon.png"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        hits.append(Hit(
            client_ip=client_ip,
            user_agent=self.headers.get("User-Agent", "?"),
            host_header=self.headers.get("Host", "?"),
            at=time.time(),
        ))

        body = PAGE.format(client_ip=client_ip).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """標準のアクセスログは抑止する．結果は main 側で整形して出す．"""
        return


# --------------------------------------------------------------------------
# 表示
# --------------------------------------------------------------------------

def print_qr(url: str, dark_terminal: bool = True) -> None:
    """端末にQRコードを描く．qrcode が未導入なら黙って諦める．

    QRは「明るい下地に暗いセル」でなければカメラが読まない．
    ターミナルの背景色によって，どちらの描き方が正しいかが逆になる．

    - 背景が暗い（既定）：塗りつぶし文字が明るく見えるので invert=True
    - 背景が明るい（macOS Terminal の既定など）：invert=False

    間違えると白黒が反転し，iPhone のカメラでは読み取れない．
    """
    try:
        import qrcode  # type: ignore[import-not-found]
    except ImportError:
        print("  （pip install qrcode で，ここにQRコードを表示できる）\n")
        return
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(out=sys.stdout, invert=dark_terminal)
    print("  読み取れない場合はターミナルの背景色と逆になっている．"
          f"{'--light-terminal' if dark_terminal else '--dark-terminal'} を付けて再実行する．\n")


def print_diagnosis(url: str, self_test_ok: bool, fw: str | None) -> None:
    """時間切れだった場合の切り分け手順を示す．"""
    print("\n" + "=" * 62)
    print("  結果：届きませんでした")
    print("=" * 62 + "\n")

    if not self_test_ok:
        print("  サーバPC自身からの接続も失敗しています．")
        print("  つまり学内Wi-Fi 以前に，このPC上で待ち受けができていません．")
        if fw and "enabled" in fw.lower():
            print("  ファイアウォールが有効です．Python の着信を許可してください：")
            print("    システム設定 → ネットワーク → ファイアウォール → オプション\n")
        print("  この状態ではクライアント間通信の判定はできません．先にこちらを解消してください．\n")
        return

    print("  サーバPC自身からは接続できているため，待ち受けは正常です．")
    print("  原因は次のいずれかです．上から順に確認してください．\n")
    print("  1. スマートフォンが別のネットワークに繋がっている")
    print("     モバイル通信が優先されていないか，ゲスト用SSIDに繋がっていないか．")
    print("     Wi-Fi設定でSSIDがサーバPCと同一か確認する．")
    print("  2. URLの打ち間違い")
    print(f"     {url}")
    print("     QRを読ませれば打ち間違いは起きない．")
    print("  3. プライバシーセパレータ（クライアント間通信の遮断）")
    print("     1と2を潰しても届かないなら，ほぼこれで確定．\n")
    print("  3 だった場合の代替手段：")
    print("    - サーバPCのテザリングに参加者を繋ぐ（最も確実．参加者は25台まで）")
    print("    - トンネリングで外部URLを発行する（学外への通信が必要）\n")


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="学内Wi-Fi のクライアント間通信を確認する")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"待ち受けポート（既定 {DEFAULT_PORT}）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                        help=f"待ち受け時間の秒数（既定 {DEFAULT_TIMEOUT_SEC}）")
    parser.add_argument("--ip", default=None,
                        help="QRに埋めるIPアドレスを明示する（自動判定を上書き）")
    # QRの白黒はターミナルの背景色に合わせる必要がある．既定は暗い背景向け．
    parser.add_argument("--light-terminal", dest="dark_terminal",
                        action="store_false", default=True,
                        help="背景が白いターミナル（macOS Terminal の既定など）でQRを描く")
    parser.add_argument("--dark-terminal", dest="dark_terminal",
                        action="store_true",
                        help="背景が黒いターミナルでQRを描く（既定）")
    args = parser.parse_args()

    print("\n" + "=" * 62)
    print("  ゼミ質疑アプリ 疎通確認ツール")
    print("=" * 62 + "\n")

    # --- 1. このPCのネットワーク情報 ---------------------------------------
    addresses = list_ipv4_addresses()
    ip = args.ip or primary_ipv4()

    if not ip:
        print("  IPアドレスを判定できませんでした．Wi-Fi に接続されていますか．")
        return 1

    print("  このPCのIPアドレス：")
    for iface, addr in addresses:
        mark = " ←これを使う" if addr == ip else ""
        print(f"    {iface:<12} {addr}{mark}")
    if not addresses:
        print(f"    {ip}")
    if len([a for _, a in addresses if a != ip]) > 0:
        print("\n  複数あります．有線とWi-Fiの両方が繋がっている場合，")
        print("  参加者と同じWi-Fi側のアドレスを選ぶ必要があります（--ip で指定できる）．")
    print()

    # --- 2. ファイアウォールとポートの確認 ---------------------------------
    fw = macos_firewall_state()
    if fw:
        print(f"  ファイアウォール：{fw}")
        if "enabled" in fw.lower():
            print("    有効です．初回起動時に着信許可のダイアログが出たら「許可」を選んでください．")
        print()

    if not port_is_free(args.port):
        print(f"  ポート {args.port} は既に使用中です．")
        print("  uvicorn が起動したままではありませんか．停止するか --port で別の番号を指定してください．\n")
        return 1

    # --- 3. 待ち受け開始 ---------------------------------------------------
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://{ip}:{args.port}/"

    # --- 4. 自己テスト -----------------------------------------------------
    # サーバPC自身からLAN側のIPへ繋ぐ．ここが失敗するなら，
    # 学内Wi-Fi ではなくこのPCの設定（ファイアウォール等）の問題である．
    self_test_ok = False
    try:
        with urllib.request.urlopen(f"{url}__selftest", timeout=3):
            self_test_ok = True
    except (urllib.error.URLError, OSError, TimeoutError):
        self_test_ok = False

    print(f"  自己テスト：{'OK' if self_test_ok else '失敗'}"
          f"（このPC自身から {ip} へ接続）")
    if not self_test_ok:
        print("    待ち受けができていません．このまま待っても届きません．")
    print()

    # --- 5. スマートフォンからのアクセスを待つ -----------------------------
    print("-" * 62)
    print(f"\n  スマートフォンから次のURLを開いてください：\n\n      {url}\n")
    print_qr(url, dark_terminal=args.dark_terminal)
    print(f"  {args.timeout} 秒待ちます．（Ctrl-C で中断）\n")

    deadline = time.time() + args.timeout
    try:
        while time.time() < deadline and not hits:
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n  中断しました．\n")
        server.shutdown()
        return 1

    server.shutdown()

    # --- 6. 結果 -----------------------------------------------------------
    if not hits:
        print_diagnosis(url, self_test_ok, fw)
        return 1

    hit = hits[0]
    print("\n" + "=" * 62)
    print("  結果：届きました．クライアント間通信は通っています")
    print("=" * 62 + "\n")
    print(f"  接続元      {hit.client_ip}")
    print(f"  Host ヘッダ {hit.host_header}")
    print(f"  端末        {hit.user_agent[:70]}")
    print("\n  （接続元IPは診断のために表示しているだけで，保存も記録もしていない）\n")

    # サーバPCとスマートフォンが同じサブネットにいるかを確認する．
    # 異なる場合，たまたま別経路で届いただけの可能性があり，当日は繋がらないことがある．
    if hit.client_ip.rsplit(".", 1)[0] != ip.rsplit(".", 1)[0]:
        print("  注意：サーバPCとスマートフォンのサブネットが異なります．")
        print(f"        PC {ip} ／ スマホ {hit.client_ip}")
        print("        別のネットワークを経由して届いた可能性があります．")
        print("        当日と同じWi-Fi・同じ場所で，もう一度確認してください．\n")

    print("  この構成で進められます．段階1（store.py / hub.py / main.py）へ．\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
