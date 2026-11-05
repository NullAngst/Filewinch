"""State shared by every page: settings, the dependency report, jobs, queues."""

from __future__ import annotations

import datetime as _dt
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from filewinch.core import deps
from filewinch.core.jobs import CancelToken
from filewinch.core.settings import Settings

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class Job:
    """One entry on the Jobs page: a name, every command it ran, and its log."""
    id: int
    name: str
    started: _dt.datetime
    state: str = RUNNING
    commands: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    summary: str = ""
    cancel: CancelToken | None = None

    def add_line(self, line: str) -> None:
        self.log.append(line)
        if line.startswith("$ "):
            self.commands.append(line[2:])


class Bridge(QObject):
    """Signals a worker thread can emit; Qt delivers them on the GUI thread."""
    line = pyqtSignal(str)
    event = pyqtSignal(str, object)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


def run_in_thread(fn: Callable, *args, **kwargs) -> Bridge:
    """Run fn(*args, bridge=..., **kwargs) in a thread. Connect to the bridge first,
    then call bridge.start()."""
    bridge = Bridge()

    def target():
        try:
            result = fn(*args, bridge=bridge, **kwargs)
        except Exception as exc:  # shown in the UI, never swallowed
            import traceback
            bridge.line.emit(traceback.format_exc())
            bridge.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        bridge.finished.emit(result)

    bridge._thread = threading.Thread(target=target, daemon=True)  # type: ignore[attr-defined]
    bridge.start = bridge._thread.start  # type: ignore[attr-defined]
    return bridge


class AppState(QObject):
    jobs_changed = pyqtSignal()
    job_line = pyqtSignal(int, str)
    deps_changed = pyqtSignal()
    settings_changed = pyqtSignal()
    repair_queue = pyqtSignal(list)        # list[Path] sent to the Repair page
    source_chosen = pyqtSignal(object)     # Device or Path, to the Scan page
    results_ready = pyqtSignal(object)     # ScanResult, to the Results page
    navigate = pyqtSignal(str)

    def __init__(self, settings: Settings | None = None, report: deps.Report | None = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.report = report or deps.scan()
        self.jobs: list[Job] = []
        self._next = 1

    def recheck(self) -> None:
        self.report = deps.scan()
        self.deps_changed.emit()

    def new_job(self, name: str) -> Job:
        job = Job(id=self._next, name=name, started=_dt.datetime.now(), cancel=CancelToken())
        self._next += 1
        self.jobs.insert(0, job)
        self.jobs_changed.emit()
        return job

    def job_log(self, job: Job, line: str) -> None:
        job.add_line(line)
        self.job_line.emit(job.id, line)

    def finish_job(self, job: Job, state: str, summary: str = "") -> None:
        job.state = state
        job.summary = summary
        self.jobs_changed.emit()

    def missing_reason(self, *keys: str) -> str:
        """Why a feature is greyed out, with the install command, or '' when it is not."""
        missing = [k for k in keys if not self.report.available(k)]
        if not missing:
            return ""
        tools = [self.report.tools[k] for k in missing if k in self.report.tools]
        cmd = self.report.install_command(tools)
        return f"Needs {', '.join(missing)}." + (f"\n{cmd}" if cmd else "")

    def output_dir(self) -> Path:
        p = Path(self.settings.output_dir).expanduser()
        return p
