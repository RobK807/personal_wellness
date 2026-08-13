"""Run the Flask front-end.

    python serve.py                 # waitress if installed, else Flask's server
    python serve.py --debug         # Flask's reloader, for development

Waitress is a pure-Python WSGI server: no compiled dependencies, so it installs
cleanly on the NAS, and unlike Flask's built-in server it is meant for real use.
"""
from __future__ import annotations

import argparse

import config
from web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the personal wellness dashboard")
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 so Tailscale can reach it (default)")
    parser.add_argument("--port", type=int, default=config.WEB_PORT)
    parser.add_argument("--debug", action="store_true",
                        help="use Flask's dev server with auto-reload")
    args = parser.parse_args()

    app = create_app()

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
        return

    try:
        from waitress import serve
    except ImportError:
        print("waitress not installed - falling back to Flask's built-in server.")
        print("  pip install waitress")
        app.run(host=args.host, port=args.port, debug=False)
        return

    print(f"Serving on http://{args.host}:{args.port}")
    # Two threads is plenty for one person on a phone, and keeps memory down.
    serve(app, host=args.host, port=args.port, threads=2,
          ident="personal-wellness")


if __name__ == "__main__":
    main()
