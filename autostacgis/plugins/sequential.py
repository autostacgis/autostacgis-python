from ..runtime.executors import Pool


class Sequential(Pool):
    backend = "sequential"


ID = "sequential"
REQUIRES = ()
IMPLEMENTATIONS = {"services": Sequential}
