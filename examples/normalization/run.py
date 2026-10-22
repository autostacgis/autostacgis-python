"""Run the collection product on a catalog with calibrated local imagery."""

from pathlib import Path

from autostacgis import Application

root = Path(__file__).resolve().parent
app = Application.from_file(root / "config.yaml")
print(app.plan().to_dict())
with app.run() as result:
    for record in result:
        print(record.item["id"], record.item["assets"])
