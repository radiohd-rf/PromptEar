import urllib.request, socket, sys

hosts = [
    ("download.pytorch.org", 443),
    ("download.pytorch.org", 80),
    ("pypi.org", 443),
]

for host, port in hosts:
    try:
        s = socket.create_connection((host, port), timeout=10)
        s.close()
        print(f"  {host}:{port} — OK")
    except Exception as e:
        print(f"  {host}:{port} — FAIL: {e}")

# Try fetching the page
try:
    req = urllib.request.Request("https://download.pytorch.org/whl/cu126/torch/")
    with urllib.request.urlopen(req, timeout=15) as f:
        print(f"  HTTP {f.status}")
except Exception as e:
    print(f"  HTTP FAIL: {e}")
