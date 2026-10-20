#!/usr/bin/env python3
import os, socket, subprocess, sys, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run_kol(file, *args):
    return subprocess.run([sys.executable, "kol.py", "run", file, *args], cwd=ROOT, capture_output=True, text=True, timeout=20)

# TCP connect/send/recv round-trip against a local ephemeral port.
server = socket.socket()
server.bind(("127.0.0.1", 0)); server.listen(1)
port = server.getsockname()[1]
def tcp_worker():
    conn, _ = server.accept()
    data = conn.recv(16)
    conn.sendall(b"pong" if data == b"ping" else b"bad")
    conn.close(); server.close()
t = threading.Thread(target=tcp_worker, daemon=True); t.start()
r = run_kol("tests/net_tcp_client.kol", "127.0.0.1", str(port))
t.join(timeout=5)
if r.returncode != 0 or r.stdout.strip() != "4\npong":
    print("FAIL: TCP round trip", repr(r.stdout), r.stderr); sys.exit(1)

# UDP datagram send against a local receiver.
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(("127.0.0.1", 0)); port = udp.getsockname()[1]; udp.settimeout(5)
r = run_kol("tests/udp_send.kol", "127.0.0.1", str(port))
data, _ = udp.recvfrom(128); udp.close()
if r.returncode != 0 or r.stdout.strip() != "8" or data != b"udp-ping":
    print("FAIL: UDP send", repr(r.stdout), repr(data), r.stderr); sys.exit(1)

# HTTP/1.0 client round-trip against a local test server.
httpd = socket.socket(); httpd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
httpd.bind(("127.0.0.1", 0)); httpd.listen(1)
port = httpd.getsockname()[1]
def http_worker():
    conn, _ = httpd.accept()
    request = conn.recv(4096)
    body = b"kol-http-ok"
    conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    conn.close(); httpd.close()
t = threading.Thread(target=http_worker, daemon=True); t.start()
r = run_kol("tests/http_client_test.kol", f"http://127.0.0.1:{port}/health")
t.join(timeout=5)
if r.returncode != 0 or r.stdout.strip() != "200\nkol-http-ok":
    print("FAIL: HTTP client", repr(r.stdout), r.stderr); sys.exit(1)
print("PASS: TCP sockets and HTTP client")
