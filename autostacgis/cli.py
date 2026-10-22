import json

import click

from .app import Application
from .config import read


def emit(value):
    click.echo(json.dumps(value, indent=2, allow_nan=False))


@click.group()
def main():
    """Discover STAC inputs, plan products, and execute AutoSTACGIS workflows."""


@main.command("validate")
@click.argument("catalog", type=click.Path(exists=True))
@click.option("--request", type=click.Path(exists=True))
def validate_command(catalog, request):
    app = Application.from_file(catalog)
    if request or app.default_request is not None:
        app.request(request)
    click.echo("Valid")


@main.command("plugins")
@click.argument("catalog", type=click.Path(exists=True))
def plugins_command(catalog):
    app = Application.from_file(catalog)
    emit(
        [
            {"id": plugin["id"], "capabilities": list(plugin["capabilities"])}
            for plugin in app.plugins.values()
        ]
    )


def operation(name):
    @main.command(name)
    @click.argument("catalog", type=click.Path(exists=True))
    @click.argument("request", required=False, type=click.Path(exists=True))
    def command(catalog, request):
        app = Application.from_file(catalog)
        req = read(request) if request else app.request()
        if name == "discover":
            emit([r.item for r in app.discover(req)])
        elif name == "plan":
            emit(app.plan(req).to_dict())
        elif name == "available":
            emit(app.available(req))
        else:
            result = app.run(req)
            if hasattr(result, "close"):
                with result:
                    emit([r.item for r in result])
            else:
                emit(result.to_dict())

    return command


for name in ("discover", "plan", "available", "run"):
    operation(name)


@main.command("trigger")
@click.argument("catalog", type=click.Path(exists=True))
@click.argument("name")
def trigger_command(catalog, name):
    Application.from_file(catalog).trigger(name)
