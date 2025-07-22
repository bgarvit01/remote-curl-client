import unittest
from unittest.mock import patch, MagicMock
from remote_curl_client import RemoteCurlClient, RemoteResponse


class TestRemoteCurlClient(unittest.TestCase):

    def setUp(self):
        self.client = RemoteCurlClient("fakehost", "user", password="pass")

    @patch("paramiko.SSHClient")
    def test_successful_get_request(self, mock_ssh_cls):
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nHello World!CURLSTATUS:200"
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)

        response = self.client.request(method="GET", url="https://example.com")

        self.assertIsInstance(response, RemoteResponse)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Content-Type", response.headers)
        self.assertEqual(response.body, "Hello World!")

    @patch("paramiko.SSHClient")
    def test_retry_on_failure(self, mock_ssh_cls):
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh

        def fail_then_succeed(*args, **kwargs):
            if not hasattr(self, "called"):
                self.called = 1
                raise Exception("Simulated SSH failure")
            mock_stdout = MagicMock()
            mock_stdout.read.return_value = b"HTTP/1.1 200 OK\r\n\r\nOK!CURLSTATUS:200"
            mock_stderr = MagicMock()
            mock_stderr.read.return_value = b""
            return (None, mock_stdout, mock_stderr)

        mock_ssh.exec_command.side_effect = fail_then_succeed

        response = self.client.request(
            method="GET", url="https://example.com", retries=1, backoff_factor=0
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, "OK!")

    @patch("paramiko.SSHClient")
    def test_parse_headers_correctly(self, mock_ssh_cls):
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value = mock_ssh
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = (
            b"HTTP/1.1 404 Not Found\r\nContent-Type: text/html\r\nX-Test: value\r\n\r\nNot Found!CURLSTATUS:404"
        )
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_ssh.exec_command.return_value = (None, mock_stdout, mock_stderr)

        response = self.client.request(method="GET", url="https://example.com/404")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Test"], "value")
        self.assertIn("Not Found!", response.body)

    @patch("paramiko.SSHClient")
    def test_connection_failure(self, mock_ssh_cls):
        mock_ssh_cls.side_effect = Exception("Connection refused")
        with self.assertRaises(ConnectionError):
            self.client.request(method="GET", url="https://example.com")
