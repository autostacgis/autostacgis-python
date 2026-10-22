from pathlib import Path


def implementation_identity(function):
    """Include module and AutoSTACGIS code plus installed dependency versions in checkpoint identity."""
    import hashlib
    import inspect
    from importlib.metadata import distributions

    path = inspect.getsourcefile(function)
    if not path:
        return None
    files = sorted(Path(__file__).parents[1].rglob("*.py"))
    code = [
        (str(file.relative_to(Path(__file__).parents[1])), hashlib.sha256(file.read_bytes()).hexdigest())
        for file in files
    ]
    dependencies = sorted(
        (distribution.metadata.get("Name", ""), distribution.version) for distribution in distributions()
    )
    return [hashlib.sha256(Path(path).read_bytes()).hexdigest(), code, dependencies]
