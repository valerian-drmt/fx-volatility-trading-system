"""Dumb TCP server for the ib-stub container used by the Playwright e2e
CI job. Accepts a connection on 4002, reads whatever the client sends
and discards it. Never speaks the IB wire protocol — engines will drop
the connection after their handshake times out, retry, and the loop
continues. Good enough to let docker compose up run to healthy without
real IB credentials.
"""
from __future__ import annotations

import socket
import threading

HOST = "0.0.0.0"
PORT = 4002


def _client_loop(sock: socket.socket) -> None:
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                return
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(16)
    print(f"[ib-stub] listening on {HOST}:{PORT}", flush=True)
    while True:
        conn, addr = srv.accept()
        print(f"[ib-stub] accepted {addr}", flush=True)
        threading.Thread(target=_client_loop, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
