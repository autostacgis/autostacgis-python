"""Composable output constraints compiled against an initial inventory."""

from copy import deepcopy

from .contracts import test, value
from .errors import AutoSTACGISError, NoPlan
from .spatial import covers, geometry


def selected(record, selector):
    if selector.get("kind", record.kind) != record.kind:
        return False
    if test(record, selector.get("require", selector.get("metadata", {}))) is not True:
        return False
    if "bbox" in selector or "intersects" in selector:
        area = geometry(selector.get("bbox", selector.get("intersects")))
        if area is None:
            raise AutoSTACGISError("Invalid area-of-interest geometry")
        extent = geometry(record.item.get("geometry"))
        if extent is None:
            extent = geometry(record.item.get("bbox"))
        if extent is None or not extent.intersects(area):
            return False
    if "projected" in selector:
        from pyproj import CRS

        asset = record.item.get("assets", {}).get(record.runtime.get("selected_asset", "data"), {})
        props = record.item.get("properties", {})
        crs = next(
            (
                asset.get(p, props.get(p))
                for p in ("proj:code", "proj:wkt2", "proj:projjson")
                if asset.get(p, props.get(p))
            ),
            None,
        )
        if crs is None or CRS.from_user_input(crs).is_projected != selector["projected"]:
            return False
    return True


def combine(alternatives):
    result = [[]]
    for choices in alternatives:
        result = [left + right for left in result for right in choices]
    merged = []
    for constraints in result:
        names = {}
        for constraint in constraints:
            name = constraint["name"]
            if name not in names:
                names[name] = deepcopy(constraint)
                continue
            existing = names[name]
            existing["require"] = {"all": [existing.get("require", {}), constraint.get("require", {})]}
            for key, val in constraint.items():
                if key in ("name", "require"):
                    continue
                if key in existing and existing[key] != val:
                    raise AutoSTACGISError(f"Conflicting constraints for output {name}: {key}")
                existing[key] = val
        merged.append(list(names.values()))
    return merged


def compile_goal(goal, records, context, active=()):
    if "plugin" in goal:
        from .solvers import expand_goal

        return compile_goal(expand_goal(goal, records, context, active), records, context)
    if "all_of" in goal:
        return combine([compile_goal(g, records, context, active) for g in goal["all_of"]])
    if "any_of" in goal:
        alternatives = []
        for child in goal["any_of"]:
            try:
                alternatives.extend(compile_goal(child, records, context, active))
            except NoPlan:
                continue
        if not alternatives:
            raise NoPlan("No goal alternative has an eligible input selection")
        return alternatives
    if "for_each" in goal:
        spec = goal["for_each"]
        selection = spec.get("select", {})
        if not any(
            k in selection for k in ("kind", "require", "metadata", "bbox", "intersects", "projected")
        ):
            selection = {"require": selection}
        chosen = [r for r in records if selected(r, selection)]
        if not chosen:
            raise NoPlan("for_each selected no initial datasets")
        return [
            [
                {
                    "name": spec.get("name", "result") + ":" + r.key,
                    "kind": spec.get("kind", r.kind),
                    "require": spec.get("require", {}),
                    "member": r.key,
                    **({"publish": spec["publish"]} if "publish" in spec else {}),
                    **({"coverage": spec["coverage"]} if "coverage" in spec else {}),
                }
                for r in chosen
            ]
        ]
    if "output" in goal:
        spec = deepcopy(goal["output"])
        spec.setdefault("name", "result")
        select = spec.pop("select", None)
        if select is not None:
            if not any(
                k in select for k in ("kind", "require", "metadata", "bbox", "intersects", "projected")
            ):
                select = {"require": select}
            spec["from"] = [r.key for r in records if selected(r, select)]
            if not spec["from"]:
                raise NoPlan("Output source selection is empty")
        return [[spec]]
    raise AutoSTACGISError("Goal must be plugin, output, for_each, all_of, or any_of")


def satisfies(record, constraint):
    if (
        constraint.get("kind", record.kind) != record.kind
        or test(record, constraint.get("require", {})) is not True
    ):
        return False
    if not set(constraint.get("from", [])) <= set(record.runtime.get("lineage", [record.key])):
        return False
    if "member" in constraint and record.runtime.get(
        "member_sources", record.runtime.get("lineage", [record.key])
    ) != [constraint["member"]]:
        return False
    if "coverage" in constraint:
        rule = constraint["coverage"]
        mode = rule.get("mode", "footprint")
        if mode not in ("footprint", "bbox") and "path" not in rule:
            raise AutoSTACGISError(f"{mode} coverage requires a metadata geometry path")
        extent = (
            record.item.get("geometry")
            if mode == "footprint"
            else record.item.get("bbox")
            if mode == "bbox"
            else value(record, rule["path"])
        )
        if covers(extent, rule["geometry"]) is not True:
            return False
    return True


def select_outputs(records, alternatives):
    for constraints in alternatives:
        result = {}
        for constraint in constraints:
            candidates = [r for r in records if satisfies(r, constraint)]
            count = constraint.get("count", 1)
            if len(candidates) < count:
                break
            result[constraint["name"]] = [r.key for r in candidates[:count]]
        else:
            return result
    return None
