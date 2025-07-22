# remote-curl-client

Send HTTP requests **from a remote machine (over SSH) using `curl`**, and receive structured responses locally in Python.

This is useful when:
- The remote network has different routing, VPN access, or firewalls.
- You need to debug connectivity *from the remote host's perspective*.
- You want a lightweight alternative to installing a full API stack remotely.

---

## ✨ Features
- All HTTP methods (`GET`, `POST`, `PUT`, `DELETE`, etc.).
- Pass any additional curl flags.
- Headers, query params, and request bodies (string or JSON dict).
- Follow redirects, toggle SSL verification, set timeouts.
- **Retry with exponential backoff + jitter.**
- Structured `RemoteResponse` object (status, headers, body, raw headers).
- Password or SSH-key auth (Paramiko).

---

## 🚀 Install

From PyPI (after release):
```bash
pip install remote-curl-client
```

---

## 🔧 Quick Start

```python
from remote_curl_client import RemoteCurlClient

client = RemoteCurlClient(
    hostname="10.37.65.78",
    username="user",
    key_filename="~/.ssh/id_rsa",  # or password="..."
)

resp = client.request(
    method="GET",
    url="https://httpbin.org/get",
    params={"q": "remote"},
    headers={"User-Agent": "RemoteCurlClient/1.0"},
    retries=3,
    backoff_factor=0.5,
)

print(resp.status_code)
print(resp.headers)
print(resp.body[:200])
```

---

## 📨 POST JSON

```python
data = {"hello": "remote world"}

resp = client.request(
    method="POST",
    url="https://httpbin.org/post",
    data=data,  # dict -> JSON serialized
    headers={"Authorization": "Bearer token123"},
    timeout=10,
)
```

---

## 🔁 Retries & Backoff

```python
resp = client.request(
    method="GET",
    url="https://httpbin.org/status/500",
    retries=4,          # total attempts = 1 + 4 = 5
    backoff_factor=1.0, # 1s, 2s, 4s, 8s...
    max_backoff=5.0,    # cap
)
```

---

## ⚙️ Extra curl flags

Anything curl supports can be passed through:
```python
resp = client.request(
    method="GET",
    url="https://example.com",
    curl_args=["--compressed", "--http2"],
)
```

---

## 🔐 SSH with Password
```python
client = RemoteCurlClient(
    hostname="10.37.65.78",
    username="user",
    password="your_password"
)
```

---

## 🧪 Testing Locally Against localhost
If you have SSH to localhost and curl installed, you can quickly validate:
```python
client = RemoteCurlClient("localhost", "youruser")
resp = client.request("GET", "https://httpbin.org/get")
print(resp.status_code)
```


---

## 📄 License
MIT License. See `LICENSE`.
