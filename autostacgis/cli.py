import json
from pathlib import Path

import click

from .app import Application


def emit(value):
    click.echo(json.dumps(value, indent=2, allow_nan=False))


@click.group()
def main():
    """Discover STAC inputs, plan products, and execute AutoSTACGIS workflows."""


@main.command("validate")
@click.argument("configuration", type=click.Path(exists=True))
def validate_command(configuration):
    app = Application.from_file(configuration)
    app._execution()
    click.echo("Valid")


@main.group("plugins", invoke_without_command=True)
@click.pass_context
def plugins_command(ctx):
    """List installed plugins, browse published versions, or install Python files."""
    if ctx.invoked_subcommand is None:
        from .plugin_metadata import inventory
        from .registry import Registry

        emit(inventory(Registry()))


@plugins_command.command("list")
def list_plugins():
    from .plugin_metadata import inventory
    from .registry import Registry

    emit(inventory(Registry()))


@plugins_command.command("available")
@click.option("--registry", default=None)
def available_plugins(registry):
    from .plugin_manager import REGISTRY_URL, PluginManager

    emit(PluginManager(registry_url=registry or REGISTRY_URL).catalog())


@plugins_command.command("install")
@click.argument("name")
@click.option("--version")
@click.option("--registry", default=None)
def install_plugin(name, version, registry):
    from .plugin_manager import REGISTRY_URL, PluginManager

    emit(PluginManager(registry_url=registry or REGISTRY_URL).install(name, version))


@plugins_command.command("install-file")
@click.argument("filename", type=click.Path(exists=True, path_type=Path))
def install_file(filename):
    from .plugin_manager import PluginManager

    emit(PluginManager().install_file(filename.name, filename.read_text()))


@main.group("workflows")
def workflows_command():
    """Browse published workflow JSON and download it to a folder."""


@workflows_command.command("available")
@click.option("--registry", default=None)
def available_workflows(registry):
    from .workflow_manager import REGISTRY_URL, WorkflowManager

    emit(WorkflowManager(registry or REGISTRY_URL).catalog())


@workflows_command.command("download")
@click.argument("name")
@click.argument("directory", type=click.Path(file_okay=False))
@click.option("--version")
@click.option("--registry", default=None)
@click.option("--overwrite", is_flag=True)
def download_workflow(name, directory, version, registry, overwrite):
    from .workflow_manager import REGISTRY_URL, WorkflowManager

    click.echo(
        WorkflowManager(registry or REGISTRY_URL).download(name, directory, version, overwrite=overwrite)
    )


def operation(name):
    @main.command(name)
    @click.argument("configuration", type=click.Path(exists=True))
    def command(configuration):
        app = Application.from_file(configuration)
        if name == "discover":
            emit([r.item for r in app.discover()])
        elif name == "plan":
            emit(app.plan().to_dict())
        elif name == "available":
            emit(app.available())
        else:
            result = app.trigger()
            if hasattr(result, "close"):
                with result:
                    emit([r.item for r in result])
            elif hasattr(result, "to_dict"):
                emit(result.to_dict())

    return command


for name in ("discover", "plan", "available", "run", "trigger"):
    operation(name)
