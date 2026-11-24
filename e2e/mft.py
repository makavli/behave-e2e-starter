"""MFT endpoints: SFTP (the usual MFT protocol) or a local / mounted folder, with the same four areas."""
import os
import posixpath
import stat
from pathlib import Path

from .config import ROOT, ConfigError, need

AREAS = ("inbound", "outbound", "archive", "error")


def connect(cfg):
    protocol = cfg.get("protocol", "sftp")
    if protocol == "sftp":
        return SftpMft(cfg)
    if protocol == "local":
        return LocalMft(cfg)
    raise ConfigError(f"[mft] protocol must be 'sftp' or 'local', got {protocol!r}")


class _Folders:
    def __init__(self, cfg):
        self.dirs = {area: cfg[area] for area in AREAS if cfg.get(area)}
        self.atomic = cfg.get("atomic_upload", True)  # upload as <name>.part, then rename
        self.cleanup = cfg.get("cleanup", True)

    def dir(self, area):
        if area not in self.dirs:
            raise ConfigError(f"[mft] has no '{area}' folder")
        return self.dirs[area]


class LocalMft(_Folders):
    """A drop folder on this machine or a mounted share (SMB, NFS)."""

    def _path(self, area, name):
        return Path(self.dir(area)) / name

    def put(self, area, name, data):
        target = self._path(area, name)
        tmp = target.with_name(name + ".part") if self.atomic else target
        tmp.write_bytes(data)
        if self.atomic:
            os.replace(tmp, target)

    def list(self, area):
        return sorted(p.name for p in Path(self.dir(area)).iterdir() if p.is_file())

    def get(self, area, name):
        return self._path(area, name).read_bytes()

    def delete(self, area, name):
        self._path(area, name).unlink(missing_ok=True)

    def close(self):
        pass


class SftpMft(_Folders):
    """An SFTP server, host key checked against known_hosts: an unknown or changed key is refused."""

    def __init__(self, cfg):
        super().__init__(cfg)
        import paramiko  # only SFTP runs need it

        self._paramiko, self.cfg = paramiko, cfg
        self.client = self.sftp = None
        self._connect()

    def _connect(self):
        paramiko, cfg = self._paramiko, self.cfg
        self.close()
        client = paramiko.SSHClient()
        try:
            known_hosts = cfg.get("known_hosts")
            if known_hosts:
                path = Path(known_hosts)
                client.load_host_keys(str(path if path.is_absolute() else ROOT / path))
            else:
                client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            client.connect(
                need(cfg, "mft", "host"), port=int(cfg.get("port", 22)), username=need(cfg, "mft", "username"),
                password=cfg.get("password") or None, key_filename=cfg.get("key_file") or None,
                passphrase=cfg.get("passphrase") or None, timeout=float(cfg.get("connect_timeout_s", 30)),
                allow_agent=False, look_for_keys=False)
            self.client, self.sftp = client, client.open_sftp()
        except BaseException:
            client.close()  # no socket or thread left behind by a refused login or host key
            raise

    def _again(self, fn, *args):
        """Run fn; if the server dropped the session (idle timeout, restart), reconnect once and retry."""
        try:
            return fn(*args)
        except (self._paramiko.SSHException, EOFError, ConnectionError):
            self._connect()
            return fn(*args)

    def _path(self, area, name):
        return posixpath.join(self.dir(area), name)

    def put(self, area, name, data):
        self._again(self._put, area, name, data)

    def list(self, area):
        return self._again(self._list, area)

    def get(self, area, name):
        return self._again(self._get, area, name)

    def delete(self, area, name):
        self._again(self._delete, area, name)

    def _put(self, area, name, data):
        target = self._path(area, name)
        tmp = target + ".part" if self.atomic else target
        with self.sftp.open(tmp, "wb") as f:
            f.set_pipelined(True)
            f.write(data)
        if self.atomic:
            try:
                self.sftp.posix_rename(tmp, target)
            except OSError:  # server without the posix-rename extension
                self.sftp.rename(tmp, target)

    def _list(self, area):
        return sorted(a.filename for a in self.sftp.listdir_attr(self.dir(area))
                      if a.st_mode is None or stat.S_ISREG(a.st_mode))

    def _get(self, area, name):
        with self.sftp.open(self._path(area, name), "rb") as f:
            f.prefetch()
            return f.read()

    def _delete(self, area, name):
        try:
            self.sftp.remove(self._path(area, name))
        except FileNotFoundError:
            pass

    def close(self):
        for part in (self.sftp, self.client):
            if part is not None:
                part.close()
        self.client = self.sftp = None
