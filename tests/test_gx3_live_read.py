from __future__ import annotations

import argparse
import socket
import threading

from gx3cli.gx3_live_read import (
    build_3e_binary_read_frame,
    decode_bit_values,
    parse_device,
    read_current_values,
)


def serve_once(response_payload: bytes, seen: list[bytes]) -> tuple[str, int, threading.Thread]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("localhost", 0))
    server.listen(1)
    host, port = server.getsockname()

    def run() -> None:
        try:
            conn, _addr = server.accept()
            with conn:
                data = conn.recv(1024)
                seen.append(data)
                response = b"\xD0\x00\x00\xFF\xFF\x03\x00" + (2 + len(response_payload)).to_bytes(2, "little")
                response += b"\x00\x00" + response_payload
                conn.sendall(response)
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return host, port, thread


def main() -> None:
    d100 = parse_device("D100")
    assert d100.prefix == "D"
    assert d100.number == 100
    assert parse_device("X1A").number == 0x1A

    frame = build_3e_binary_read_frame(d100, 2)
    assert frame[:2] == b"\x50\x00"
    assert frame[11:15] == b"\x01\x04\x00\x00"
    assert frame[15:18] == (100).to_bytes(3, "little")
    assert frame[18] == 0xA8
    assert frame[19:21] == (2).to_bytes(2, "little")

    assert decode_bit_values(bytes([0x10, 0x01]), 4) == [True, False, False, True]

    seen: list[bytes] = []
    host, port, thread = serve_once((123).to_bytes(2, "little") + (0xFFFF).to_bytes(2, "little"), seen)
    args = argparse.Namespace(
        ip=host,
        port=port,
        device="D100",
        count=2,
        type="signed-word",
        timeout=2.0,
        network=0,
        pc=0xFF,
        io=0x03FF,
        station=0,
        timer=0x0010,
    )
    result = read_current_values(args)
    thread.join(2)
    assert result["values"] == [123, -1]
    assert seen and seen[0] == frame

    print("live-read checks passed")


if __name__ == "__main__":
    main()
