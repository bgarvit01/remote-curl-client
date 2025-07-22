"""
remote_curl_client.client
-------------------------
Execute HTTP requests from a *remote* host over SSH by invoking `curl` remotely,
and return a structured Python object locally.

Key capabilities:
- Supports arbitrary HTTP methods.
- Pass through additional curl arguments.
- Query params, headers, request body (string or dict -> JSON).
- Insecure SSL (-k), redirect following (-L), timeout.
- Retry with exponential backoff + jitter.
- Logging & error handling.

NOTE: This module shells out to `curl` on the *remote* host. Ensure `curl` is installed there.
"""
import json
import logging
import random
import shlex
import time
from dataclasses import dataclass
from typing import Dict, Optional, Union, List, Tuple

import paramiko


# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s')
    _handler.setFormatter(_formatter)
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ------------------------------------------------------------------
# Public response dataclass
# ------------------------------------------------------------------
@dataclass
class RemoteResponse:
    status_code: int
    headers: Dict[str, str]
    body: str
    url: str
    raw_headers: str = ""  # full raw header dump for debugging


class RemoteCurlError(RuntimeError):
    """Generic remote-curl error."""


class RemoteCurlClient:
    """
    Make HTTP requests *from a remote machine* by SSH-ing in and running curl.

    Example:
        client = RemoteCurlClient('10.37.65.78', 'user', key_filename='~/.ssh/id_rsa')
        resp = client.request('GET', 'https://httpbin.org/get', params={'q':'x'}, retries=3)
    """
    def __init__(
        self,
        hostname: str,
        username: str,
        password: Optional[str] = None,
        port: int = 22,
        key_filename: Optional[str] = None,
        connect_timeout: int = 10,
    ) -> None:
        self.hostname = hostname
        self.username = username
        self.password = password
        self.port = port
        self.key_filename = key_filename
        self.connect_timeout = connect_timeout

    # ---------------- SSH connection helper ----------------
    def _get_ssh_client(self) -> paramiko.SSHClient:
        logger.info("Connecting to %s:%s via SSH...", self.hostname, self.port)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
                key_filename=self.key_filename,
                timeout=self.connect_timeout,
            )
        except Exception as e:  # broad: paramiko raises various subclasses
            logger.error("SSH connection failed: %s", e)
            raise ConnectionError(f"SSH connection failed: {e}") from e
        logger.info("SSH connection established.")
        return client

    # ---------------- Public request with retries ----------------
    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Union[str, Dict]] = None,
        params: Optional[Dict[str, str]] = None,
        curl_args: Optional[List[str]] = None,
        insecure: bool = False,
        follow_redirects: bool = True,
        timeout: Optional[int] = None,
        retries: int = 0,
        backoff_factor: float = 0.5,
        max_backoff: Optional[float] = None,
    ) -> RemoteResponse:
        """
        Perform a remote HTTP request with retry + exponential backoff.

        retries: number of *additional* attempts after the first. e.g. retries=3 -> up to 4 tries total.
        backoff_factor: base delay (seconds) multiplied by 2^(attempt-1), + small jitter.
        max_backoff: cap the backoff delay (seconds). None = uncapped.
        """
        attempt = 0
        while True:
            try:
                return self._perform_request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=data,
                    params=params,
                    curl_args=curl_args,
                    insecure=insecure,
                    follow_redirects=follow_redirects,
                    timeout=timeout,
                )
            except Exception as e:
                if attempt >= retries:
                    logger.error("Request failed after %s attempt(s): %s", attempt + 1, e)
                    raise
                attempt += 1
                delay = backoff_factor * (2 ** (attempt - 1))
                delay += random.uniform(0, 0.1)  # jitter
                if max_backoff is not None:
                    delay = min(delay, max_backoff)
                logger.warning(
                    "Remote request attempt %s failed (%s). Retrying in %.2fs...",
                    attempt, e, delay
                )
                time.sleep(delay)

    # ---------------- Core request (single attempt) ----------------
    def _perform_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[Union[str, Dict]] = None,
        params: Optional[Dict[str, str]] = None,
        curl_args: Optional[List[str]] = None,
        insecure: bool = False,
        follow_redirects: bool = True,
        timeout: Optional[int] = None,
    ) -> RemoteResponse:

        method = method.upper()

        # Add query params
        if params:
            # naive concatenation; could urlencode but curl will too if we use --data-urlencode for GET?
            # safest: use urllib.parse
            from urllib.parse import urlencode
            sep = '&' if ('?' in url) else '?'
            url = f"{url}{sep}{urlencode(params)}"

        parts = ["curl", "-sS", "-D", "-"]  # -D - dumps headers to stdout before body
        if follow_redirects:
            parts.append("-L")
        parts += ["-X", shlex.quote(method)]

        # Request body
        if data is not None:
            if isinstance(data, dict):
                body_str = json.dumps(data)
                # add content-type if not provided
                has_ct = any(k.lower() == "content-type" for k in (headers or {}))
                if not has_ct:
                    parts += ["-H", shlex.quote("Content-Type: application/json")]
            else:
                body_str = str(data)
            parts += ["--data-binary", shlex.quote(body_str)]

        # Headers
        if headers:
            for k, v in headers.items():
                parts += ["-H", shlex.quote(f"{k}: {v}")]

        if insecure:
            parts.append("-k")

        if timeout is not None:
            parts += ["--max-time", str(int(timeout))]

        if curl_args:
            # assume caller already properly formatted; still quote each
            parts += [shlex.quote(arg) for arg in curl_args]

        parts += [shlex.quote(url), "-w", shlex.quote("\nCURLSTATUS:%{http_code}")]

        remote_cmd = " ".join(parts)
        logger.debug("Remote curl command: %s", remote_cmd)

        with self._get_ssh_client() as ssh:
            try:
                stdin, stdout, stderr = ssh.exec_command(remote_cmd, timeout=timeout or 30)
                out_bytes = stdout.read()
                err_bytes = stderr.read()
            except Exception as e:
                logger.error("SSH exec_command failed: %s", e)
                raise RemoteCurlError(f"SSH command execution failed: {e}") from e

        if err_bytes:
            err_text = err_bytes.decode(errors="replace").strip()
            if err_text:
                logger.warning("Remote stderr: %s", err_text)

        out_text = out_bytes.decode(errors="replace")

        # Parse: curl -D - prints *all* headers (one block per redirect) followed by body, then our status line.
        # We'll split off the status and then keep only the *last* header block.
        try:
            out_text, status_code = self._split_status(out_text)
        except Exception as e:
            logger.error("Failed to split curl status: %s", e)
            raise RemoteCurlError(f"Failed to split curl status: {e}") from e

        header_blocks, body = self._split_header_blocks(out_text)
        raw_headers = header_blocks[-1] if header_blocks else ""
        parsed_headers = self._parse_headers_block(raw_headers)

        return RemoteResponse(
            status_code=status_code,
            headers=parsed_headers,
            body=body,
            url=url,
            raw_headers=raw_headers,
        )

    # -------------- helpers: parsing --------------
    @staticmethod
    def _split_status(text: str) -> Tuple[str, int]:
        """
        Given full curl output (headers+body+\nCURLSTATUS:NNN),
        return (everything_before_status, status_code_int).
        """
        marker = "CURLSTATUS:"
        if marker not in text:
            raise ValueError("CURLSTATUS marker not found")
        before, after = text.rsplit(marker, 1)
        status_str = after.strip().splitlines()[0].strip()
        return before, int(status_str)

    @staticmethod
    def _split_header_blocks(text: str) -> Tuple[List[str], str]:
        """
        curl -D - prints one header block per response (redirects included),
        each terminated by an empty line. The *body* follows the final blank line.
        We'll parse all header blocks and return a list + body string.
        """
        # Split by CRLF blank lines OR LF blank lines; handle both.
        # We'll scan for HTTP/ lines; naive but effective.
        blocks = []
        remaining = text

        # We'll iterate extracting from start until no HTTP/ at beginning.
        while remaining.startswith("HTTP/"):
            # Find header/body separator: two consecutive newlines (CRLF or LF)
            sep_pos = None
            for sep in ["\r\n\r\n", "\n\n"]:
                idx = remaining.find(sep)
                if idx != -1:
                    sep_pos = idx
                    sep_len = len(sep)
                    break
            if sep_pos is None:
                # no separator -> entire remaining is headers (malformed)
                blocks.append(remaining)
                remaining = ""
                break
            block = remaining[:sep_pos]
            blocks.append(block)
            remaining = remaining[sep_pos + sep_len:]
            # Next iteration may or may not start w/ HTTP/
            # If not, we exit loop
            if not remaining.startswith("HTTP/"):
                break

        body = remaining
        return blocks, body

    @staticmethod
    def _parse_headers_block(raw_headers: str) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for line in raw_headers.splitlines():
            if not line or line.startswith("HTTP/"):
                continue
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
        return headers
