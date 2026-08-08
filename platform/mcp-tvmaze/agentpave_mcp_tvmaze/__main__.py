"""stdio entrypoint — how Claude Code talks to this server.

Dogfooding (ARCHITECTURE.md §6): the same governed tool serves the production
agent and the developer's assistant. Nothing here is special-cased for that —
it is the same server object the Lambda serves, over a different transport.
"""

from .server import build_server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
