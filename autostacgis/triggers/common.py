"""Serializable invocations and explicit trigger lifetimes."""

from threading import Event


def execute(catalog, base_dir, request):
    from autostacgis import Application

    result = Application(catalog, base_dir=base_dir).run(request)
    if hasattr(result, "close"):
        result.close()


class ManagedTrigger:
    def __init__(self):
        self.stopped = Event()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.stop()
        self.join()

    def wait(self):
        try:
            while not self.stopped.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            self.join()


def launch(controller, background):
    controller.start()
    if not background:
        controller.wait()
    return controller
