"""Local stand-ins for the order API, SAP's IDoc endpoint, a Workato recipe and an SFTP-based MFT route.

They let every scenario run offline (a laptop, a pipeline agent) exactly as it would against a real landscape.
Their credentials come from config/local.toml, so the simulators and the clients always agree.
"""
import os
import shutil
import tempfile

from ..mft import AREAS
from .mft_route import MftRoute
from .sftp import SftpSim
from .web import WebSim


class Simulators:
    def __init__(self, raw):
        self.dir = tempfile.mkdtemp(prefix="e2e-sim-")
        root = os.path.join(self.dir, "sftp")
        for area in AREAS:
            os.makedirs(os.path.join(root, area))
        self.web = WebSim(raw)
        mft = raw.get("mft", {})
        self.sftp = SftpSim(root, mft.get("username", "e2e"), mft.get("password", "local-sftp-password"), self.dir)
        self.route = MftRoute(root)
        # E2E_SIM_DIR lets a config use protocol = "local" on the same folders the SFTP server serves.
        os.environ.update(E2E_SIM_URL=self.web.url, E2E_SIM_DIR=root, E2E_SFTP_PORT=str(self.sftp.port),
                          E2E_SFTP_KNOWN_HOSTS=self.sftp.known_hosts)

    def stop(self):
        self.route.stop()
        self.sftp.stop()
        self.web.stop()
        shutil.rmtree(self.dir, ignore_errors=True)
