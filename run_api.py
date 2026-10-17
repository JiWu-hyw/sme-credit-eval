"""一键启动信用评估后端 API（FastAPI + uvicorn）。

用法：
    python run_api.py                # 默认 127.0.0.1:8000
    python run_api.py --port 8080    # 自定义端口
    python run_api.py --reload       # 开发热重载

依赖：fastapi / uvicorn[standard] / pydantic（见 requirements.txt「后端 API」段）。
"""

import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser(description="启动信用评估后端 API")
    ap.add_argument("--host", type=str, default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="开发热重载（不用于生产）")
    args = ap.parse_args()

    print("=" * 60)
    print(" 中小科技企业信用评估 — 后端 API (FastAPI)")
    print("=" * 60)
    print(f" 服务地址：http://{args.host}:{args.port}")
    print(f" OpenAPI 文档：http://{args.host}:{args.port}/docs")
    print(" 按 Ctrl+C 停止。")

    uvicorn.run(
        "src.app.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
