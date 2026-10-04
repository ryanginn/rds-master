"""RDS Master as a Windows service.

Read this before using it: a Windows service runs in session 0, where the
per-session audio engine does not exist. MME, DirectSound and WASAPI all go
through that engine, so a service cannot open them. **Kernel streaming
(WDM-KS) does work**, because it talks to the driver underneath, which is how
StereoTool and BreakawayOne run as services.

So: pick the WDM-KS version of your output device in the Audio tab before
installing this, or use session mode instead, which runs the encoder in your
own logon where every host API works.

Needs pywin32. Installed and removed by install-service.bat.
"""
from __future__ import annotations

import os
import sys
import threading

import servicemanager
import win32event
import win32service
import win32serviceutil

PROGRAM_DIR = os.path.dirname(os.path.abspath(sys.executable))
DATA_DIR = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                        "RDS Master")


class RDSMasterService(win32serviceutil.ServiceFramework):
    _svc_name_ = "RDSMaster"
    _svc_display_name_ = "RDS Master encoder"
    _svc_description_ = ("Generates the RDS subcarrier and serves the web "
                         "interface. Needs a WDM-KS output device: a service "
                         "cannot open an MME or WASAPI one.")

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        os.environ.setdefault("RDS_DATA_DIR", DATA_DIR)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ''))
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
        except OSError:
            pass
        # The encoder is a long-running server, so it goes on its own thread
        # and this one waits for the stop signal.
        worker = threading.Thread(target=self._serve, daemon=True)
        worker.start()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)

    def _serve(self):
        try:
            sys.path.insert(0, PROGRAM_DIR)
            import app
            app.socketio.run(app.app, host=app.http_host, port=app.http_port,
                             debug=False, log_output=False,
                             allow_unsafe_werkzeug=True)
        except Exception as exc:                       # noqa: BLE001
            servicemanager.LogErrorMsg(f"RDS Master stopped: {exc}")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(RDSMasterService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(RDSMasterService)
