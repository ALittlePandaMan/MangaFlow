"""Development-only Uvicorn reload supervision and bounded process cleanup."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import psutil
import uvicorn
from uvicorn.supervisors import ChangeReload

GRACE_SECONDS = 30


def stop_processes(pids: list[int]) -> None:
    processes = {}
    for pid in pids:
        try:
            parent = psutil.Process(pid)
            for process in [parent, *parent.children(recursive=True)]:
                processes[process.pid] = process
        except psutil.NoSuchProcess:
            pass
    for process in processes.values():
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(list(processes.values()), timeout=GRACE_SECONDS)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=2)


class WorkerExited(Exception):
    pass


class DevelopmentReload(ChangeReload):
    """Uvicorn normally leaves its watcher alive after a failed worker startup."""

    closed = False

    def pause(self):
        super().pause()
        if self.process.exitcode is not None:
            raise WorkerExited(f"Development backend exited unexpectedly (status {self.process.exitcode})")

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=GRACE_SECONDS)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=2)
        for sock in self.sockets:
            sock.close()

    def run(self):
        try:
            super().run()
        finally:
            if hasattr(self, "process"):
                self.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", nargs="+", type=int, metavar="PID")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--app", default="app.main:app")
    args = parser.parse_args()
    if args.stop:
        stop_processes(args.stop)
        return
    sys.path.insert(0, str(Path.cwd()))
    config = uvicorn.Config(args.app, host="127.0.0.1", port=args.port, reload=True,
                            reload_dirs=[str(Path.cwd())], timeout_graceful_shutdown=25)
    server = uvicorn.Server(config)
    socket = config.bind_socket()
    try:
        DevelopmentReload(config, target=server.run, sockets=[socket]).run()
    except WorkerExited as error:
        logging.getLogger("uvicorn.error").error("%s", error)
        raise SystemExit(1) from None
    finally:
        socket.close()


if __name__ == "__main__":
    main()
