"""
bg.py - lets slow work (scanning, reading the registry...) run in the background so the
window never freezes. Results come back to the main thread through a queue, because
tkinter widgets must only be touched from the main thread.
"""
import queue
import threading
import traceback


class BackgroundMixin:
    def init_background(self):
        self._jobs = queue.Queue()
        self._poll_jobs()

    def run_bg(self, work, on_done):
        """Run work() in a thread, then call on_done(result, error) on the main thread."""
        def worker():
            try:
                self._jobs.put((on_done, work(), None))
            except Exception as error:      # noqa: BLE001 - report ANY failure to the UI
                self._jobs.put((on_done, None, error))
        threading.Thread(target=worker, daemon=True).start()

    def _poll_jobs(self):
        try:
            while True:
                callback, value, error = self._jobs.get_nowait()
                try:
                    callback(value, error)
                except Exception:           # noqa: BLE001 - one bad callback must not stop the loop
                    traceback.print_exc()
        except queue.Empty:
            pass
        try:
            self.after(100, self._poll_jobs)
        except Exception:                   # widget was closed
            pass
