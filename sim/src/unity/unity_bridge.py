# src/unity/unity_bridge.py

from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Any

from unity_protocol import (
    decode_message,
    encode_message,
    hello_message,
    chunk_load_message,
)


class UnityBridge:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
    ):
        self.host = host
        self.port = port

        self.server_socket: socket.socket | None = None
        self.client_socket: socket.socket | None = None

        self.running = False
        self.connected = False

        self.outgoing: queue.Queue[dict[str, Any]] = queue.Queue()
        self.incoming: queue.Queue[dict[str, Any]] = queue.Queue()

        self._server_thread: threading.Thread | None = None
        self._client_thread: threading.Thread | None = None

    # ---------------------------------------------------------
    # Server lifecycle
    # ---------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return

        self.running = True

        self._server_thread = threading.Thread(
            target=self._server_loop,
            name="unity-server",
            daemon=True,
        )

        self._server_thread.start()

    def close(self) -> None:
        self.running = False
        self.connected = False

        if self.client_socket is not None:
            try:
                self.client_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

            try:
                self.client_socket.close()
            except OSError:
                pass

            self.client_socket = None

        if self.server_socket is not None:
            try:
                self.server_socket.close()
            except OSError:
                pass

            self.server_socket = None

    # ---------------------------------------------------------
    # Python -> Unity
    # ---------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        """
        Queue a protocol message for Unity.
        """

        self.outgoing.put(message)

    # ---------------------------------------------------------
    # Unity -> Python
    # ---------------------------------------------------------

    def poll(self) -> list[dict[str, Any]]:
        """
        Return all currently queued messages from Unity.
        """

        messages = []

        while True:
            try:
                messages.append(
                    self.incoming.get_nowait()
                )
            except queue.Empty:
                break

        return messages

    # ---------------------------------------------------------
    # Server
    # ---------------------------------------------------------

    def _server_loop(self) -> None:
        server = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        )

        server.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1,
        )

        server.bind(
            (self.host, self.port)
        )

        server.listen(1)

        self.server_socket = server

        print(
            f"[unity] listening on "
            f"{self.host}:{self.port}"
        )

        while self.running:

            try:
                client, address = server.accept()

            except OSError:
                break

            print(
                f"[unity] connected: {address}"
            )

            # Replace previous connection.
            if self.client_socket is not None:
                try:
                    self.client_socket.close()
                except OSError:
                    pass

            self.client_socket = client
            self.connected = True

            self._client_thread = threading.Thread(
                target=self._client_loop,
                args=(client,),
                name="unity-client",
                daemon=True,
            )

            self._client_thread.start()

    # ---------------------------------------------------------
    # Client connection
    # ---------------------------------------------------------

    def _client_loop(
        self,
        client: socket.socket,
    ) -> None:

        client.settimeout(0.05)

        receive_buffer = b""

        # Handshake.
        try:
            client.sendall(
                encode_message(
                    hello_message()
                )
            )
        except OSError:
            self.connected = False
            return

        while (
            self.running
            and self.client_socket is client
        ):

            # ---------------------------------------------
            # Send queued messages
            # ---------------------------------------------

            while True:

                try:
                    message = self.outgoing.get_nowait()

                except queue.Empty:
                    break

                try:
                    client.sendall(
                        encode_message(message)
                    )

                except OSError:
                    self.connected = False
                    return

            # ---------------------------------------------
            # Receive Unity messages
            # ---------------------------------------------

            try:
                data = client.recv(65536)

                if not data:
                    break

                receive_buffer += data

            except socket.timeout:
                pass

            except OSError:
                break

            # NDJSON framing:
            # one JSON object per line.
            while b"\n" in receive_buffer:

                line, receive_buffer = (
                    receive_buffer.split(
                        b"\n",
                        1,
                    )
                )

                if not line.strip():
                    continue

                try:
                    message = decode_message(line)

                except Exception as exc:
                    print(
                        f"[unity] invalid message: {exc}"
                    )
                    continue

                self.incoming.put(message)

            time.sleep(0.001)

        self.connected = False

        try:
            client.close()
        except OSError:
            pass

        if self.client_socket is client:
            self.client_socket = None

        print("[unity] disconnected")
        
        
if __name__ == "__main__":
    bridge = UnityBridge()
    bridge.start()

    print("[unity] bridge started")

    sent_test_chunk = False

    try:
        while True:
            if bridge.connected and not sent_test_chunk:
                bridge.send(
                    chunk_load_message(
                        0,
                        0,
                        "sim/cache/glb_chunks/stuttgart/chunk_0_0.glb",
                    )
                )

                print("[unity] sent test chunk 0,0")
                sent_test_chunk = True

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n[unity] stopping bridge")

    finally:
        bridge.close()