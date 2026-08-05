import threading
import paramiko

from core.logutil import get_logger

log = get_logger("ssh")


class SSHClient:
    def __init__(self):
        self._client = None
        self._lock = threading.Lock()
        self._last_ip = None
        self._last_password = None
        self._last_port = 22

    @property
    def is_connected(self):
        # No lock — safe to read transport status without blocking the main thread
        client = self._client
        if client is None:
            return False
        try:
            transport = client.get_transport()
            return transport is not None and transport.is_active()
        except Exception:
            return False

    def connect(self, ip, password, port=22, timeout=10):
        with self._lock:
            self.disconnect_unlocked()
            log.info("SSH connecting to root@%s:%s (timeout=%ss)", ip, port, timeout)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                if password:
                    client.connect(
                        hostname=ip,
                        port=port,
                        username="root",
                        password=password,
                        timeout=timeout,
                        look_for_keys=False,
                        allow_agent=False,
                    )
                else:
                    # Blank USB root password and/or local SSH keys
                    client.connect(
                        hostname=ip,
                        port=port,
                        username="root",
                        password="",
                        timeout=timeout,
                        look_for_keys=True,
                        allow_agent=True,
                    )
            except Exception as e:
                log.error("SSH connect failed: %s: %s", type(e).__name__, e)
                raise
            self._client = client
            self._last_ip = ip
            self._last_password = password
            self._last_port = port
            log.info("SSH connected to %s", ip)

    def ensure_connected(self, timeout=15, retries=4, pause=2.0):
        """Reconnect if the transport died (e.g. after xochitl / network blip)."""
        import time

        if self.is_connected:
            return True
        if not self._last_ip:
            raise RuntimeError("Not connected (no prior connect to retry)")
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                log.warning(
                    "SSH reconnect attempt %s/%s to %s",
                    attempt,
                    retries,
                    self._last_ip,
                )
                self.connect(
                    self._last_ip,
                    self._last_password if self._last_password is not None else "",
                    port=self._last_port or 22,
                    timeout=timeout,
                )
                # Quick liveness check
                self.exec("true", timeout=5)
                return True
            except Exception as e:
                last_err = e
                time.sleep(pause)
        raise RuntimeError(f"SSH reconnect failed after {retries} tries: {last_err}")

    def disconnect(self):
        with self._lock:
            self.disconnect_unlocked()

    def disconnect_unlocked(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def exec(self, cmd, timeout=30):
        with self._lock:
            if self._client is None:
                raise RuntimeError("Not connected")
            # Truncate very long one-liners in logs
            shown = cmd if len(cmd) <= 220 else cmd[:217] + "..."
            log.debug("exec (t=%ss): %s", timeout, shown)
            stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
            # recv_exit_status() blocks forever if the command hangs,
            # so use the channel's event with a timeout instead
            channel = stdout.channel
            if not channel.status_event.wait(timeout=timeout):
                channel.close()
                log.warning("exec timed out after %ss: %s", timeout, shown)
                raise TimeoutError(f"Command timed out after {timeout}s: {cmd}")
            exit_code = channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            if exit_code != 0:
                log.debug(
                    "exec rc=%s out=%r err=%r",
                    exit_code,
                    (out or "")[:160],
                    (err or "")[:160],
                )
            return out, err, exit_code

    def upload(self, local_path, remote_path):
        with self._lock:
            if self._client is None:
                raise RuntimeError("Not connected")
            sftp = self._client.open_sftp()
            try:
                sftp.put(local_path, remote_path)
            finally:
                sftp.close()

    def upload_string(self, content, remote_path):
        with self._lock:
            if self._client is None:
                raise RuntimeError("Not connected")
            sftp = self._client.open_sftp()
            try:
                with sftp.file(remote_path, "w") as f:
                    f.write(content)
            finally:
                sftp.close()

    def download_bytes(self, remote_path):
        with self._lock:
            if self._client is None:
                raise RuntimeError("Not connected")
            sftp = self._client.open_sftp()
            try:
                with sftp.file(remote_path, "rb") as f:
                    return f.read()
            finally:
                sftp.close()

    def upload_bytes(self, data, remote_path):
        with self._lock:
            if self._client is None:
                raise RuntimeError("Not connected")
            sftp = self._client.open_sftp()
            try:
                with sftp.file(remote_path, "wb") as f:
                    f.write(data)
            finally:
                sftp.close()

    def open_channel(self):
        """Open a Paramiko channel with a PTY for interactive sessions.

        Does NOT acquire self._lock — transport is thread-safe for opening
        channels. Caller is responsible for closing the channel.
        """
        return self.open_pty_command(None)

    def open_pty_command(self, command=None):
        """Open a PTY session; optionally exec a single command (e.g. bluetoothctl).

        When command is None, starts an interactive shell (legacy path).
        """
        client = self._client
        if client is None:
            raise RuntimeError("Not connected")
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("Not connected")
        channel = transport.open_session()
        channel.get_pty(term="dumb", width=200, height=50)
        if command:
            log.debug("open PTY exec: %s", command)
            channel.exec_command(command)
        else:
            log.debug("open PTY interactive shell")
            channel.invoke_shell()
        return channel

    def run_in_background(self, cmd, callback, timeout=60):
        def worker():
            try:
                result = self.exec(cmd, timeout=timeout)
                callback(result, None)
            except Exception as e:
                callback(None, e)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        return t
