"""Stable single-process backend CLI."""

import argparse
from pathlib import Path

import uvicorn

from backend.api.app import create_app
from backend.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m backend")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--data-dir", type=Path)
    options = parser.parse_args()
    config = load_config(data_dir=options.data_dir, host=options.host, port=options.port)

    def shutdown() -> None:
        server.should_exit = True

    app = create_app(config, shutdown=shutdown)
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.host, port=config.port, timeout_graceful_shutdown=5)
    )
    server.run()


if __name__ == "__main__":
    main()
