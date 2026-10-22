import shutil
from pathlib import Path
from uuid import uuid4


class Staging:
    settings_schema = {
        "type": "object",
        "properties": {"directory": {"type": "string"}},
        "additionalProperties": False,
    }

    def __init__(self, settings, services):
        self.root = Path(settings.get("directory", services.directory / "staged"))
        self.root.mkdir(parents=True, exist_ok=True)

    def copy(self, path):
        source = Path(path)
        target = self.root / (uuid4().hex + "-" + source.name)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        return target


ID = "staging"
REQUIRES = ()
IMPLEMENTATIONS = {"services": Staging}
