"""An in-process SFTP server (paramiko) confined to one folder: the local MFT endpoint.

It writes a known_hosts file for its fresh host key, so clients run with strict host-key checking, as in production.
"""
import errno
import hmac
import os
import posixpath
import socket
import threading

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface, ServerInterface
from paramiko.sftp import SFTP_OK


class _Auth(ServerInterface):
    def __init__(self, username, password):
        self.username, self.password = username, password

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        ok = (hmac.compare_digest(username.encode(), self.username.encode())
              and hmac.compare_digest(password.encode(), self.password.encode()))
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Handle(SFTPHandle):
    def stat(self):
        try:
            return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)


class _Folder(SFTPServerInterface):
    def __init__(self, server, root):
        super().__init__(server)
        self.root = os.path.abspath(root)

    def canonicalize(self, path):
        return posixpath.normpath("/" + path.replace("\\", "/").lstrip("/"))  # never above "/"

    def _real(self, path):
        real = os.path.abspath(os.path.join(self.root, *[p for p in self.canonicalize(path).split("/") if p]))
        # On Windows a part like "C:" or "name:stream" would leave the root; refuse anything outside it.
        if ":" in path or os.path.commonpath([self.root, real]) != self.root:
            raise PermissionError(errno.EACCES, "outside the SFTP root", path)
        return real

    @staticmethod
    def _do(fn, *args):
        try:
            fn(*args)
            return SFTP_OK
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    def list_folder(self, path):
        real = self._real(path)
        try:
            out = []
            for name in os.listdir(real):
                attr = SFTPAttributes.from_stat(os.stat(os.path.join(real, name)))
                attr.filename = name
                out.append(attr)
            return out
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        try:
            return SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    lstat = stat

    def open(self, path, flags, attr):
        try:
            fd = os.open(self._real(path), flags | getattr(os, "O_BINARY", 0), 0o666)
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)
        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"
        handle = _Handle(flags)
        handle.filename = path
        handle.readfile = handle.writefile = os.fdopen(fd, mode)
        return handle

    def remove(self, path):
        return self._do(os.remove, self._real(path))

    def rename(self, oldpath, newpath):
        return self._do(os.rename, self._real(oldpath), self._real(newpath))

    def posix_rename(self, oldpath, newpath):
        return self._do(os.replace, self._real(oldpath), self._real(newpath))

    def mkdir(self, path, attr):
        return self._do(os.mkdir, self._real(path))

    def rmdir(self, path):
        return self._do(os.rmdir, self._real(path))

    def chattr(self, path, attr):
        return SFTP_OK


class SftpSim:
    def __init__(self, root, username, password, keys_dir):
        self.root = root
        self.key = paramiko.RSAKey.generate(2048)
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.known_hosts = os.path.join(keys_dir, "known_hosts")
        with open(self.known_hosts, "w", encoding="ascii") as f:
            f.write(f"[127.0.0.1]:{self.port} {self.key.get_name()} {self.key.get_base64()}\n")
        self._auth = _Auth(username, password)
        self._transports = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:  # socket closed by stop()
                return
            transport = paramiko.Transport(conn)
            transport.add_server_key(self.key)
            transport.set_subsystem_handler("sftp", SFTPServer, _Folder, self.root)
            try:
                transport.start_server(server=self._auth)
            except (paramiko.SSHException, EOFError, OSError):
                transport.close()
                continue
            self._transports.append(transport)

    def stop(self):
        self.sock.close()
        for transport in self._transports:
            transport.close()
