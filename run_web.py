"""一键启动轻量 Web 可视化界面（demo）。

启动本地静态服务，访问 web/index.html，并自动打开浏览器。

用法：
    .venv\\Scripts\\python.exe run_web.py
    .venv\\Scripts\\python.exe run_web.py --port 8080   # 自定义端口
    .venv\\Scripts\\python.exe run_web.py --no-browser  # 不自动打开浏览器
"""

import argparse
import os
import webbrowser
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__)))
WEB_DIR = os.path.join(ROOT, "web")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass  # 静默日志


def main():
    ap = argparse.ArgumentParser(description="启动信用评估 demo Web 界面")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    os.chdir(WEB_DIR)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/index.html"
    print("=" * 56)
    print(" 中小科技企业信用评估 — 轻量 Web 可视化 (demo)")
    print("=" * 56)
    print(f" 本地服务已启动：{url}")
    print(" 按 Ctrl+C 停止。")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            print(" (无法自动打开浏览器，请手动复制上面的链接)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n 已停止服务。")
        httpd.shutdown()


if __name__ == "__main__":
    main()
