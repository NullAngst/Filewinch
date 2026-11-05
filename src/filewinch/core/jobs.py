"""Run external tools.

Two ways in:

ProcessJob    QProcess-based, for the long jobs started from the GUI (imaging,
              carving, listing). Streams stdout and stderr line by line, parses
              progress, supports cancel. Resume is the tool's own: ddrescue's
              mapfile and PhotoRec's session file.
run_command   Blocking, for short commands and for the repair chain, which runs
              in a worker thread. Same environment, same logging, same cancel.

Every command passes through host_env(), which strips the AppImage's own
library and Python paths so host tools load host libraries.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Iterable

# Variables the AppImage runtime or the bundled Python may set that would make
# a host binary load the wrong libraries or the wrong Python.
_STRIP = (
    "LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH",
    "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH",
    "GDK_PIXBUF_MODULE_FILE", "GSETTINGS_SCHEMA_DIR", "PERLLIB", "GCONV_PATH",
)


def host_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    for key in _STRIP:
        env.pop(key, None)
    # Restore the PATH the user had before the AppImage prepended its own.
    appdir = env.get("APPDIR")
    if appdir and "PATH" in env:
        parts = [p for p in env["PATH"].split(":") if p and not p.startswith(appdir)]
        env["PATH"] = ":".join(parts)
    env["LC_ALL"] = "C"
    if extra:
        env.update(extra)
    return env


def show_command(argv: Iterable[str], stdout_file: str = "", stdin_file: str = "") -> str:
    """The exact command line, for the Show Command button."""
    text = " ".join(shlex.quote(a) for a in argv)
    if stdin_file:
        text += " < " + shlex.quote(stdin_file)
    if stdout_file:
        text += " > " + shlex.quote(stdout_file)
    return text


@dataclass
class Result:
    argv: list
    code: int
    stdout: str
    stderr: str
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.cancelled


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            for p in self._procs:
                if p.poll() is None:
                    p.terminate()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def _track(self, p: subprocess.Popen) -> None:
        with self._lock:
            self._procs.append(p)
        if self._event.is_set():
            p.terminate()


def run_command(
    argv: list[str],
    log: Callable[[str], None] | None = None,
    cancel: CancelToken | None = None,
    cwd: str | None = None,
    stdout_file: str = "",
    stdin_file: str = "",
    timeout: float | None = None,
    keep_stdout: bool = True,
) -> Result:
    """Run argv, stream its output to log, return everything it printed."""
    if log:
        log("$ " + show_command(argv, stdout_file, stdin_file))
    out_fh = open(stdout_file, "wb") if stdout_file else None
    in_fh = open(stdin_file, "rb") if stdin_file else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=host_env(), stdin=in_fh,
            stdout=out_fh if out_fh else subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        if out_fh:
            out_fh.close()
        if stdin_file:
            in_fh.close()
        if log:
            log(f"could not start {argv[0]}: {exc}")
        return Result(argv, 127, "", str(exc))
    if cancel:
        cancel._track(proc)

    err_chunks: list[str] = []
    out_chunks: list[str] = []

    def pump(stream, sink, to_log):
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if keep_stdout or sink is err_chunks:
                sink.append(line)
            if to_log and log:
                log(line)
        stream.close()

    threads = [threading.Thread(target=pump, args=(proc.stderr, err_chunks, True), daemon=True)]
    if not out_fh:
        threads.append(threading.Thread(target=pump, args=(proc.stdout, out_chunks, False), daemon=True))
    for t in threads:
        t.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait()
        err_chunks.append("timed out")
    for t in threads:
        t.join()
    if out_fh:
        out_fh.close()
    if stdin_file:
        in_fh.close()
    return Result(argv, code, "\n".join(out_chunks), "\n".join(err_chunks),
                  cancelled=bool(cancel and cancel.cancelled))


# QProcess job for the GUI. Imported lazily so the tests and the repair engine
# do not need a Qt event loop.
try:
    from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

    class ProcessJob(QObject):
        """One long-running tool, streamed into the log pane."""

        line = pyqtSignal(str)          # every line of output
        progress = pyqtSignal(float)    # 0.0 to 1.0, when the parser knows
        finished = pyqtSignal(int)      # exit code, -1 when cancelled

        def __init__(self, argv: list[str], parser: Callable[[str], float | None] | None = None,
                     cwd: str = "", stdout_file: str = "", parent=None):
            super().__init__(parent)
            self.argv = argv
            self.parser = parser
            self.stdout_file = stdout_file
            self.cancelled = False
            self.proc = QProcess(self)
            env = QProcessEnvironment()
            for k, v in host_env().items():
                env.insert(k, v)
            self.proc.setProcessEnvironment(env)
            if cwd:
                self.proc.setWorkingDirectory(cwd)
            if stdout_file:
                self.proc.setStandardOutputFile(stdout_file)
                self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            else:
                self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.proc.readyReadStandardOutput.connect(self._read_out)
            self.proc.readyReadStandardError.connect(self._read_err)
            self.proc.finished.connect(self._done)
            self.proc.errorOccurred.connect(self._error)
            self._buf = b""

        def command_line(self) -> str:
            return show_command(self.argv, self.stdout_file)

        def start(self) -> None:
            self.line.emit("$ " + self.command_line())
            self.proc.start(self.argv[0], self.argv[1:])

        def cancel(self) -> None:
            self.cancelled = True
            if self.proc.state() != QProcess.ProcessState.NotRunning:
                self.proc.terminate()
                if not self.proc.waitForFinished(5000):
                    self.proc.kill()

        def _feed(self, data: bytes) -> None:
            # ddrescue and PhotoRec redraw with \r; treat it as a line break.
            self._buf += data.replace(b"\r", b"\n")
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                self.line.emit(text)
                if self.parser:
                    frac = self.parser(text)
                    if frac is not None:
                        self.progress.emit(max(0.0, min(1.0, frac)))

        def _read_out(self) -> None:
            self._feed(bytes(self.proc.readAllStandardOutput()))

        def _read_err(self) -> None:
            self._feed(bytes(self.proc.readAllStandardError()))

        def _error(self, err) -> None:
            if err == QProcess.ProcessError.FailedToStart:
                self.line.emit(f"could not start {self.argv[0]}")
                self.finished.emit(127)

        def _done(self, code: int, _status) -> None:
            if self._buf:
                self._feed(b"\n")
            self.finished.emit(-1 if self.cancelled else code)

except ImportError:  # pragma: no cover - only without PyQt6
    ProcessJob = None  # type: ignore[assignment,misc]
