from ..runtime.executors import Pool


class Threads(Pool):
    limit_native_threads = True

    def create_pool(self):
        from concurrent.futures import ThreadPoolExecutor

        return ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="autostacgis")

    resource_group = "executor"


ID = "threads"
REQUIRES = ()
IMPLEMENTATIONS = {"services": Threads}
