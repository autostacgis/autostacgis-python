def slurm(app, settings, request):
    """Submit an AutoSTACGIS invocation to an existing Slurm installation."""
    import json
    import shlex
    import subprocess
    from uuid import uuid4

    from ..errors import AutoSTACGISError

    directory = app.work / "slurm" / uuid4().hex
    directory.mkdir(parents=True)
    catalog = directory / "catalog.json"
    req = directory / "request.json"
    # Absolute base directory remains explicit; relative source/model paths retain their meaning.
    catalog.write_text(json.dumps(app.catalog))
    value = app.request(request or settings["request"])
    req.write_text(json.dumps(value))
    script = directory / "job.sh"
    code = 'from autostacgis import Application; from autostacgis.config import read; import sys; a=Application(read(sys.argv[1]),base_dir=sys.argv[3]); r=a.run(read(sys.argv[2])); r.close() if hasattr(r,"close") else None'
    command = [settings.get("python", "python"), "-c", code, str(catalog), str(req), str(app.base_dir)]
    script.write_text("#!/bin/sh\nset -eu\nexec " + shlex.join(command) + "\n")
    if settings.get("prepare_only", False):
        return str(script)
    options = settings.get("sbatch", {})
    allowed = {"partition", "account", "time", "cpus-per-task", "mem", "gres", "constraint", "job-name"}
    if set(options) - allowed:
        raise AutoSTACGISError("Unsupported sbatch option")
    result = subprocess.run(
        ["sbatch", "--parsable", *(f"--{key}={value}" for key, value in options.items()), str(script)],
        check=True,
        text=True,
        capture_output=True,
    )
    return {"job_id": result.stdout.strip(), "script": str(script)}


ID = "slurm"
REQUIRES = ()
IMPLEMENTATIONS = {"trigger": slurm}
