import os
import socket
import threading
import time
import uuid

import paramiko


class SSHPasswordRequired(Exception):
    """Raised when a password-auth session has not received a password yet."""


class SSHAuthenticationFailed(Exception):
    """Raised without exposing SSH authentication details to the browser."""


class SSHConnectionFailed(Exception):
    """Raised when the configured SSH target cannot be reached."""


class SSHTerminalSession:
    """A persistent SSH PTY session independent of WebSocket attachments."""

    def __init__(self, host, port, username, known_hosts_path, key_path=None, password=None):
        self.id = str(uuid.uuid4())
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.RejectPolicy())
        self._client.load_system_host_keys()
        self._client.load_host_keys(known_hosts_path)
        connection_options = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": 10,
            "auth_timeout": 10,
            "banner_timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if password is not None:
            connection_options["password"] = password
        else:
            connection_options["key_filename"] = key_path
        try:
            self._client.connect(**connection_options)
        except paramiko.AuthenticationException as exc:
            self._client.close()
            raise SSHAuthenticationFailed() from exc
        except (paramiko.ssh_exception.NoValidConnectionsError, socket.timeout, OSError) as exc:
            self._client.close()
            raise SSHConnectionFailed() from exc
        self._channel = self._client.invoke_shell(term="xterm")
        self._lock = threading.RLock()
        self._last_activity = time.time()
        self._closed = False
        self._scrollback = bytearray()
        self._scrollback_start = 0
        self._scrollback_end = 0
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        while self.is_alive():
            try:
                if not self._channel.recv_ready():
                    time.sleep(0.05)
                    continue
                data = self._channel.recv(4096)
                if not data:
                    break
                with self._lock:
                    self._last_activity = time.time()
                    self._scrollback.extend(data)
                    self._scrollback_end += len(data)
                    overflow = len(self._scrollback) - 262144
                    if overflow > 0:
                        del self._scrollback[:overflow]
                        self._scrollback_start += overflow
            except Exception:
                break

    def write(self, data: str):
        with self._lock:
            if self._closed:
                raise RuntimeError("SSH terminal session is closed")
            self._last_activity = time.time()
            self._channel.sendall(data.encode("utf-8"))

    def resize(self, cols: int, rows: int):
        cols = max(10, min(int(cols), 500))
        rows = max(4, min(int(rows), 200))
        with self._lock:
            if not self._closed:
                self._channel.resize_pty(width=cols, height=rows)

    def output_snapshot(self):
        with self._lock:
            return self._scrollback_end, bytes(self._scrollback)

    def output_since(self, offset: int):
        with self._lock:
            if offset < self._scrollback_start:
                return self._scrollback_end, bytes(self._scrollback)
            if offset >= self._scrollback_end:
                return self._scrollback_end, b""
            start = offset - self._scrollback_start
            return self._scrollback_end, bytes(self._scrollback[start:])

    def idle_seconds(self) -> float:
        return time.time() - self._last_activity

    def is_alive(self) -> bool:
        return not self._closed and self._channel.active

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._channel.close()
                self._client.close()


class SSHSessionManager:
    """Own interactive SSH sessions for one fixed, server-configured target."""

    def __init__(self, idle_timeout_sec=1800):
        self.host = os.environ["SSH_TERMINAL_HOST"]
        self.port = int(os.environ.get("SSH_TERMINAL_PORT", "22"))
        self.username = os.environ["SSH_TERMINAL_USERNAME"]
        self.auth_method = os.environ.get("SSH_TERMINAL_AUTH_METHOD", "key").lower()
        self.key_path = os.environ["SSH_TERMINAL_KEY_PATH"]
        self.known_hosts_path = os.environ["SSH_TERMINAL_KNOWN_HOSTS_PATH"]
        if not self.host or not self.username:
            raise ValueError("SSH_TERMINAL_HOST and SSH_TERMINAL_USERNAME must be set")
        if self.auth_method not in {"key", "password"}:
            raise ValueError("SSH_TERMINAL_AUTH_METHOD must be 'key' or 'password'")
        if self.auth_method == "key" and not os.path.isfile(self.key_path):
            raise OSError(f"SSH terminal private key is unavailable: {self.key_path}")
        if not os.path.isfile(self.known_hosts_path):
            raise OSError(f"SSH terminal known-hosts file is unavailable: {self.known_hosts_path}")
        self.idle_timeout_sec = idle_timeout_sec
        self._sessions = {}
        self._lock = threading.RLock()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def get_or_create(self, session_id, password=None):
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session.is_alive():
                return session
            if session is not None:
                self._sessions.pop(session_id, None)
                session.close()
            if self.auth_method == "password" and password is None:
                raise SSHPasswordRequired()
            new_session = SSHTerminalSession(
                self.host,
                self.port,
                self.username,
                self.known_hosts_path,
                key_path=self.key_path if self.auth_method == "key" else None,
                password=password if self.auth_method == "password" else None,
            )
            self._sessions[session_id or new_session.id] = new_session
            return new_session

    def close(self, session_id):
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            session.close()

    def _cleanup_loop(self):
        while True:
            time.sleep(60)
            with self._lock:
                stale = [
                    session_id
                    for session_id, session in self._sessions.items()
                    if not session.is_alive() or session.idle_seconds() > self.idle_timeout_sec
                ]
                sessions = [self._sessions.pop(session_id) for session_id in stale]
            for session in sessions:
                session.close()