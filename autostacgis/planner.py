"""Backward dataflow synthesis with memoized requirements and shared operations."""

from copy import deepcopy
from itertools import combinations, islice, product
from time import monotonic

from .contracts import canonical, cost_of, paths, requirements_hold, test
from .errors import AutoSTACGISError, NoPlan, SearchLimit
from .goals import compile_goal
from .metadata import output_metadata
from .model import Plan, Record, Step, digest
from .stac import Stac


def inventory(records):
    """Bind assets independently, preserving their containing Item metadata."""
    result = {}
    for original in records:
        selected = original.runtime.get("selected_asset")
        assets = (
            [selected]
            if selected
            else [k for k, a in original.item.get("assets", {}).items() if "data" in a.get("roles", ["data"])]
        )
        for asset in assets:
            record = original.clone()
            record.kind = record.runtime.get("asset_kinds", {}).get(asset, record.kind)
            record.key = digest([original.key, asset, original.item])[:24]
            record.runtime.update(
                selected_asset=asset,
                origin=original.key,
                lineage=[record.key],
                source_id=original.runtime.get("source_id", original.item["id"]),
            )
            if record.key in result and result[record.key].item != record.item:
                raise AutoSTACGISError("Conflicting data identity")
            result[record.key] = record
    return result


def changed_paths(contract):
    result = set()
    for output in contract["outputs"].values():
        for operation in ("set", "append"):
            result.update(canonical(next(iter(a))) for a in output.get(operation, []))
        result.update(canonical(p) for p in output.get("remove", []))
        if output.get("geometry"):
            result.add("geometry")
        if output.get("predict"):
            result.add("*")
        result.add("$kind:" + output["kind"])
    return result


def intersects(left, right):
    return (
        "*" in left
        or "*" in right
        or any(a == b or a.startswith(b + ".") or b.startswith(a + ".") for a in left for b in right)
    )


class Planner:
    def __init__(self, app, request):
        self.app, self.request = app, request
        self.modules = app.modules(request)
        self.options = {**app.catalog.get("solver", {}), **request.get("solver", {})}
        self.deadline = monotonic() + self.options.get("timeout", 120)
        self.bindings = 0
        self.evaluated = {}
        self.port_tests = {}

    def check_limit(self):
        if monotonic() > self.deadline:
            raise SearchLimit("Workflow search timed out")

    def port_choices(self, owner, port, declaration, records):
        choices = []
        for record in records.values():
            identity = (owner, port, record.key, self._token(declaration.get("require", {})))
            if identity not in self.port_tests:
                self.port_tests[identity] = (
                    declaration.get("kind", record.kind) == record.kind
                    and test(record, declaration.get("require", {})) is True
                    and self.app.transfer_supports(record, declaration, self.request)
                )
            if self.port_tests[identity]:
                choices.append(record)
        if declaration.get("asset"):
            choices = [r for r in choices if r.runtime["selected_asset"] == declaration["asset"]]
        if declaration.get("cardinality", "one") == "one":
            return [[r] for r in choices]
        # A collection contains one version of each logical member, never an
        # initial image and its derivative together. Different versions remain alternatives.
        families = {}
        for record in choices:
            family = tuple(record.runtime.get("member_sources", record.runtime.get("lineage", [record.key])))
            families.setdefault(family, []).append(record)
        groups = []
        overrides = self.request.get("sequence", {})
        grouping = (
            declaration.get("sequence")
            if declaration.get("sequence_override")
            else overrides.get(f"{owner}.{port}", declaration.get("sequence"))
        )
        minimum = declaration.get("min_items", 1)
        if grouping:
            # Define group membership before filtering by processing state. A
            # barrier waits for an eligible version of every selected member.
            seeds = [r for r in self.initial.values() if declaration.get("kind", r.kind) == r.kind]
            from .sequence import route

            calls = grouping if isinstance(grouping, list) else [grouping]
            if any(call.get("scope") == "available" for call in calls):
                seeds = [members[0] for members in families.values()]
            templates = route(
                self.app,
                calls,
                seeds,
                None if declaration.get("sequence_override") or f"{owner}.{port}" in overrides else owner,
            )
            selections = []
            for template in templates:
                domains = [
                    families.get(
                        tuple(r.runtime.get("member_sources", r.runtime.get("lineage", [r.key]))), []
                    )
                    for r in template
                ]
                if domains and all(domains):
                    selections.append(domains)
        else:
            values = list(families.values())
            selections = (
                selection for n in range(minimum, len(values) + 1) for selection in combinations(values, n)
            )
        for selection in selections:
            for members in product(*selection):
                self.check_limit()
                if len(members) < minimum:
                    continue
                groups.append(list(members))
                if len(groups) > self.options.get("max_bindings", 100000):
                    raise SearchLimit("Collection binding limit exceeded")
        return groups

    def predictions(self, name, contract, inputs, settings, step_key):
        outputs = {}
        all_lineage = sorted(
            set().union(*(set(r.runtime.get("lineage", [r.key])) for group in inputs.values() for r in group))
        )
        for port, policy in contract["outputs"].items():
            members = inputs[policy["each_from"]] if policy.get("each_from") else [None]
            for index, member in enumerate(members):
                item = output_metadata(inputs, policy, index, settings)
                if policy.get("predict"):
                    item = policy["predict"](inputs, settings, index, item)
                # Explicit changes replace stale per-band overrides too.
                for assignment in policy.get("set", []):
                    path, val = next(iter(assignment.items()))
                    parts = path.split(".")
                    if len(parts) == 3 and parts[0] == "assets":
                        for band in item.get("assets", {}).get(parts[1], {}).get("bands", []):
                            if parts[2] in band:
                                band[parts[2]] = deepcopy(val)
                if policy.get("geometry") == "union":
                    from shapely.geometry import mapping, shape
                    from shapely.ops import unary_union

                    geometries = [r.item.get("geometry") for group in inputs.values() for r in group]
                    if geometries and all(geometries):
                        geometry = unary_union([shape(g) for g in geometries])
                        item["geometry"] = mapping(geometry)
                        item["bbox"] = list(geometry.bounds)
                    else:
                        item["geometry"] = None
                        item.pop("bbox", None)
                key = digest([step_key, port, index])[:24]
                item = Stac.derived(item, key, item.get("assets", {}))
                asset = policy.get("asset", "data")
                item.setdefault("assets", {}).setdefault(asset, {})["href"] = f"autostacgis://predicted/{key}"
                Stac.extensions(item)
                anchor = member or inputs.get(contract.get("anchor", next(iter(inputs))), [None])[0]
                member_anchor = member or (
                    anchor if len(inputs.get(contract.get("anchor", next(iter(inputs))), [])) == 1 else None
                )
                member_sources = (
                    member_anchor.runtime.get(
                        "member_sources", member_anchor.runtime.get("lineage", [member_anchor.key])
                    )
                    if member_anchor
                    else all_lineage
                )
                runtime = {
                    "selected_asset": asset,
                    "lineage": all_lineage,
                    "member_sources": member_sources,
                    "producer": step_key,
                    "data_types": policy["data_types"],
                }
                if anchor:
                    runtime["source_id"] = anchor.runtime.get("source_id", anchor.item["id"])
                outputs.setdefault(port, []).append(Record(item, policy["kind"], key, runtime=runtime))
        return outputs

    def _identity(self, record):
        return tuple(record.runtime.get("member_sources", record.runtime.get("lineage", [record.key])))

    def _cost(self, records):
        steps = self._closure(records)
        return sum(self.steps[k].cost for k in steps)

    def _closure(self, records):
        seen = set()

        def visit(key):
            if key in seen:
                return
            seen.add(key)
            for dependency in self.steps[key].dependencies:
                visit(dependency)

        for record in records:
            if record.runtime.get("producer"):
                visit(record.runtime["producer"])
        return seen

    def _atoms(self, requirement):
        if not requirement:
            return []
        if isinstance(requirement, dict):
            if "all" in requirement:
                return [a for child in requirement["all"] for a in self._atoms(child)]
            if not set(requirement) & {"path", "not", "any", "jsonata"}:
                return [{"path": canonical(k), "equals": v} for k, v in requirement.items()]
        return [requirement]

    def _token(self, value):
        # Callbacks are installed objects, not serializable user expressions.
        if callable(value):
            return ("callback", id(value))
        if isinstance(value, dict):
            return tuple(sorted((k, self._token(v)) for k, v in value.items()))
        if isinstance(value, list):
            return tuple(self._token(v) for v in value)
        return (type(value).__name__, value)

    def _requirement(self, atoms):
        return {"all": list({self._token(a): a for a in atoms}.values())}

    def _effects(self, name, policy):
        from .expressions import evaluate
        from .metadata import assign

        item = {"assets": {"data": {}}, "properties": {}}
        for operation in ("set", "append"):
            for assignment in policy.get(operation, []):
                path, value = next(iter(assignment.items()))
                if operation == "append":
                    if isinstance(value, dict) and "jsonata" in value:
                        value = evaluate(
                            value["jsonata"], {"settings": self.modules[name].get("settings", {})}
                        )
                    value = [value]
                assign(item, path.replace("$asset", "data"), value)
        return Record(item, policy["kind"], name, runtime={"selected_asset": "data"})

    def _establishes(self, name, port, atom, contract=None):
        policy = (contract if contract is not None else self.modules[name])["outputs"][port]
        written = {canonical(next(iter(a))) for op in ("set", "append") for a in policy.get(op, [])}
        written.update(canonical(p) for p in policy.get("remove", []))
        return intersects(paths(atom), written) and test(self.effects[name, port], atom) is True

    def _producers(self, atoms, kind, blocked):
        candidates = {}
        for atom in atoms:
            matches = []
            for name, contract in self.modules.items():
                if name in blocked:
                    continue
                for port, policy in contract["outputs"].items():
                    if kind and policy["kind"] != kind:
                        continue
                    anchor = policy.get("each_from") or contract.get(
                        "anchor", next(iter(contract["argument_requirements"]))
                    )
                    antecedents = self._atoms(contract["argument_requirements"][anchor].get("require", {}))
                    already_required = any(self._token(a) == self._token(atom) for a in antecedents)
                    if self._establishes(name, port, atom) and not already_required:
                        matches.append((name, port))
            names = {name for name, _ in matches}
            if len(names) > 1:
                from .errors import AmbiguousProducer

                raise AmbiguousProducer(
                    f"Multiple plugins satisfy {atom}: {', '.join(sorted(names))}. "
                    "You must load only one plugin that satisfies this requirement in your YAML catalog."
                )
            for key in matches:
                candidates[key] = self.modules[key[0]]["outputs"][key[1]]
        return candidates

    def _terminal_score(self, candidate, producers):
        """Prefer effect orderings that reduce declared downstream costs.

        This is a search-order heuristic, not a new precedence constraint. Real
        costs are always evaluated again on the actual selected input versions.
        """
        from .metadata import output_metadata

        name, port = candidate
        total = 0.0
        for other, other_port in producers:
            if other == name:
                continue

            def observed(n):
                c = self.modules[n].get("cost", 1)
                if isinstance(c, dict):
                    return {canonical(v.partition(".")[2]) for v in c.get("parameters", {}).values()} or {"*"}
                return {"*"} if callable(c) else set()

            if not (
                intersects(changed_paths(self.modules[other]), observed(name))
                or intersects(changed_paths(self.modules[name]), observed(other))
            ):
                continue
            pair = (candidate, (other, other_port))
            if pair not in self.order_scores:
                differences = []
                seeds = (
                    record
                    for record in self.initial.values()
                    if record.kind == self.modules[name]["outputs"][port]["kind"]
                )
                for seed in islice(seeds, 1):

                    def apply(n, p, record):
                        c = self.modules[n]
                        inputs = {k: [record] for k in c["argument_requirements"]}
                        item = output_metadata(inputs, c["outputs"][p], 0, c.get("settings", {}))
                        return Record(
                            item, c["outputs"][p]["kind"], "cost-preview", runtime={"selected_asset": "data"}
                        )

                    def cost(n, record):
                        c = self.modules[n]
                        return cost_of(
                            c, {k: [record] for k in c["argument_requirements"]}, c.get("settings", {})
                        )

                    try:
                        differences.append(
                            cost(other, seed)
                            + cost(name, apply(other, other_port, seed))
                            - cost(name, seed)
                            - cost(other, apply(name, port, seed))
                        )
                    except (AutoSTACGISError, KeyError, TypeError, IndexError, AttributeError):
                        continue
                self.order_scores[pair] = sum(differences) / len(differences) if differences else 0
            total += self.order_scores[pair]
        return total

    def _ranked_producers(self, producers):
        """Order backward alternatives using prerequisites and cost interactions.

        The graph guides search only: a failed preferred derivation falls back
        to the other candidates. Plugin names do not encode precedence.
        """
        import networkx as nx

        graph = nx.DiGraph()
        graph.add_nodes_from(producers)
        for first in producers:
            name, port = first
            effect = self.effects[first]
            for second in producers:
                if first == second:
                    continue
                contract = self.modules[second[0]]
                anchor = contract.get("anchor", next(iter(contract["argument_requirements"])))
                arg = contract["argument_requirements"][anchor]
                if arg.get("kind", effect.kind) != effect.kind:
                    continue
                for atom in self._atoms(arg.get("require", {})):
                    if self._establishes(name, port, atom):
                        graph.add_edge(first, second)
                    elif test(effect, atom) is False:
                        policy = self.modules[name]["outputs"][port]
                        writes = {canonical(next(iter(a))) for a in policy.get("set", [])}
                        if intersects(paths(atom), writes):
                            graph.add_edge(second, first)
        scores = {key: self._terminal_score(key, producers) for key in producers}
        preferences = [
            (difference, first, second)
            for (first, second), difference in self.order_scores.items()
            if first in producers and second in producers and difference > 0
        ]
        for _, first, second in sorted(preferences, key=lambda edge: -edge[0]):
            if not nx.has_path(graph, second, first):
                graph.add_edge(first, second)
        return sorted(
            producers,
            key=lambda key: (
                graph.out_degree(key) > 0,
                -len(nx.ancestors(graph, key)) if graph.out_degree(key) == 0 else graph.out_degree(key),
                scores[key],
            ),
        )

    def _derive(self, kind, atoms, member=None, blocked=frozenset()):
        requirement = self._requirement(atoms)
        key = (
            kind,
            self._token(requirement),
            member,
            tuple(sorted(blocked)),
            tuple(sorted(r.key for r in self.leaves.values())),
        )
        if key in self.memo:
            return self.memo[key]
        if key in self.active:
            return []
        self.check_limit()
        self.expanded += 1
        if self.expanded > self.options.get("max_states", 100000):
            raise SearchLimit("Workflow subproblem limit exceeded")
        self.active.add(key)
        from .metadata import assign

        for literal in atoms:
            if not isinstance(literal, dict) or "equals" not in literal or "path" not in literal:
                continue
            path = canonical(literal["path"])
            item = {"assets": {"data": {}}, "properties": {}}
            assign(item, path.replace("$asset", "data"), literal["equals"])
            witness = Record(item, kind or "raster", "constraint", runtime={"selected_asset": "data"})
            if any(paths(other) == {path} and test(witness, other) is False for other in atoms):
                self.active.remove(key)
                self.memo[key] = []
                return []
        # Reject unreachable antecedents before exploring operation orderings.
        for atom in atoms:
            if callable(atom) or (isinstance(atom, dict) and "jsonata" in atom):
                continue
            leaves = [r for r in self.leaves.values() if not kind or r.kind == kind]
            effects = [
                r for (n, _), r in self.effects.items() if n not in blocked and (not kind or r.kind == kind)
            ]
            if not any(test(r, atom) is True for r in [*leaves, *effects]):
                self.active.remove(key)
                self.memo[key] = []
                return []
        candidates = [
            r
            for r in self.leaves.values()
            if (not kind or r.kind == kind)
            and test(r, requirement) is True
            and (member is None or self._identity(r) == member)
        ]
        # A zero-cost existing member is already the best way to satisfy this requirement.
        if candidates and (member is not None or self.request.get("steps")):
            self.active.remove(key)
            self.memo[key] = candidates
            return candidates
        producers = self._producers(atoms, kind, blocked)
        # Opaque predicates and kind-only goals can be checked on declared outputs.
        if (
            not producers
            and not candidates
            and (not atoms or any(callable(a) or "jsonata" in a for a in atoms))
        ):
            producers = {
                (n, port): policy
                for n, c in self.modules.items()
                if n not in blocked
                for port, policy in c["outputs"].items()
                if not kind or policy["kind"] == kind
            }
        # A requested aggregation is the terminal operation; propagate its other
        # required states into its members instead of transforming a finished aggregate.
        aggregates = {
            key: policy
            for key, policy in producers.items()
            if not policy.get("each_from")
            and self.modules[key[0]]["argument_requirements"][
                self.modules[key[0]].get("anchor", next(iter(self.modules[key[0]]["argument_requirements"])))
            ].get("cardinality")
            == "many"
            and policy["kind"]
            == self.modules[key[0]]["argument_requirements"][
                self.modules[key[0]].get("anchor", next(iter(self.modules[key[0]]["argument_requirements"])))
            ].get("kind")
        }
        if aggregates:
            producers = aggregates
        ranked = self._ranked_producers(producers)
        for name, output_port in ranked:
            before = len(candidates)
            policy = producers[name, output_port]
            contract = self.modules[name]
            anchor = policy.get("each_from") or contract.get(
                "anchor", next(iter(contract["argument_requirements"]))
            )
            effect = self.effects[name, output_port]
            remainder = [a for a in atoms if not self._establishes(name, output_port, a)]
            # If an explicit replacement contradicts a desired value, this cannot
            # be the final producer. Appended histories may preserve earlier entries.
            replaced = [canonical(next(iter(a))) for a in policy.get("set", [])] + [
                canonical(p) for p in policy.get("remove", [])
            ]
            if any(intersects(paths(a), set(replaced)) and test(effect, a) is False for a in remainder):
                continue
            inherited = {r.partition(".")[0] for r in policy.get("inherit_metadata", [])}
            if remainder and anchor not in inherited:
                continue
            options = []
            ports = [anchor, *[p for p in contract["argument_requirements"] if p != anchor]]
            previous_leaves = self.leaves
            for port in ports:
                declaration = contract["argument_requirements"][port]
                desired = self._atoms(declaration.get("require", {}))
                if port == anchor:
                    desired += remainder
                target = member if port == anchor and declaration.get("cardinality", "one") == "one" else None
                try:
                    values = self._derive(declaration.get("kind"), desired, target, blocked | {name})
                except BaseException:
                    self.leaves = previous_leaves
                    raise
                if port == anchor and values:
                    # Auxiliary products (masks, footprints, summaries) must be
                    # derived from these selected input versions when possible.
                    self.leaves = dict(previous_leaves)
                    for record in reversed(values):
                        self.leaves[self._identity(record)] = record
                if declaration.get("asset"):
                    values = [r for r in values if r.runtime.get("selected_asset") == declaration["asset"]]
                if declaration.get("cardinality", "one") == "one":
                    options.append([[r] for r in values])
                else:
                    options.append(
                        self.port_choices(
                            name,
                            port,
                            {**declaration, "require": self._requirement(desired)},
                            {r.key: r for r in values},
                        )
                    )
                if not options[-1]:
                    break
            self.leaves = previous_leaves
            if len(options) != len(ports) or not all(options):
                continue
            for combination in product(*options):
                self.check_limit()
                inputs = dict(zip(ports, combination, strict=True))
                input_keys = {port: [r.key for r in group] for port, group in inputs.items()}
                step_key = digest(
                    [name, contract.get("version", "1.0.0"), input_keys, contract.get("settings", {})]
                )[:24]
                if step_key not in self.evaluated:
                    self.bindings += 1
                    if self.bindings > self.options.get("max_bindings", 100000):
                        raise SearchLimit("Workflow binding limit exceeded")
                    settings = contract.get("settings", {})
                    if not requirements_hold(self.app, name, contract, inputs, settings):
                        self.evaluated[step_key] = None
                        continue
                    predictions = self.predictions(name, contract, inputs, settings, step_key)
                    cost = cost_of(contract, inputs, settings)
                    dependencies = tuple(
                        sorted(
                            {
                                r.runtime["producer"]
                                for group in inputs.values()
                                for r in group
                                if "producer" in r.runtime
                            }
                        )
                    )
                    self.steps[step_key] = Step(
                        step_key,
                        name,
                        input_keys,
                        {p: [r.key for r in group] for p, group in predictions.items()},
                        settings,
                        cost,
                        dependencies,
                    )
                    self.evaluated[step_key] = predictions
                    self.objects.update({r.key: r for group in predictions.values() for r in group})
                predictions = self.evaluated[step_key]
                if predictions is None:
                    continue
                for record in predictions[output_port]:
                    if test(record, requirement) is True and (
                        member is None or self._identity(record) == member
                    ):
                        if len(self._closure([record])) <= self.options.get("max_steps", 64):
                            candidates.append(record)
            if len(candidates) > before:
                # Unique producers resolve algorithm choice. Once an ordering
                # proves this requirement, reuse it rather than enumerate every
                # permutation of independently satisfiable metadata changes.
                break
        # Keep input identities distinct. Within a member/state retain the cheapest
        # derivation, with complete cost-observed metadata in the equivalence key.
        best = {}
        for record in candidates:
            from .contracts import value
            from .metadata import MISSING

            evidence = []
            for path in self.cost_paths:
                v = record.item if path == "*" else value(record, path)
                evidence.append((path, None if v is MISSING else self._token(v)))
            family = (self._identity(record), record.kind, tuple(evidence))
            previous = best.get(family)
            if previous is None or (self._cost([record]), record.key) < (
                self._cost([previous]),
                previous.key,
            ):
                best[family] = record
        answer = sorted(best.values(), key=lambda r: (self._cost([r]), r.key))
        self.active.remove(key)
        self.memo[key] = answer
        return answer

    def plan(self, records):
        self.initial = inventory(records)
        if not self.initial:
            raise NoPlan("No candidate datasets")
        goals = compile_goal(self.request["goal"], list(self.initial.values()), self.app)
        self.memo, self.active, self.steps = {}, set(), {}
        self.order_scores = {}
        self.objects, self.expanded = dict(self.initial), 0
        self.leaves = {self._identity(r): r for r in self.initial.values()}
        self.effects = {
            (name, port): self._effects(name, policy)
            for name, contract in self.modules.items()
            for port, policy in contract["outputs"].items()
        }
        self.cost_paths = set()
        for contract in self.modules.values():
            cost = contract.get("cost")
            if isinstance(cost, dict):
                self.cost_paths.update(
                    canonical(p.partition(".")[2]) for p in cost.get("parameters", {}).values()
                )
            elif callable(cost):
                self.cost_paths.update(contract.get("cost_dependencies") or ["*"])
        from .workflow import stages

        stages(self)
        solutions = []
        from .goals import satisfies

        for constraints in goals:
            choices = []
            for constraint in constraints:
                member = (constraint["member"],) if "member" in constraint else None
                candidates = self._derive(
                    constraint.get("kind"), self._atoms(constraint.get("require", {})), member
                )
                candidates = [r for r in candidates if satisfies(r, constraint)]
                count = constraint.get("count", 1)
                choices.append(list(combinations(candidates, count)))
            if not all(choices):
                continue
            for chosen in product(*choices):
                self.check_limit()
                outputs = {
                    c["name"]: [r.key for r in group] for c, group in zip(constraints, chosen, strict=True)
                }
                records = [r for group in chosen for r in group]
                closure = self._closure(records) | self.required_steps
                for key in tuple(closure):
                    closure.update(
                        self._closure(
                            [self.objects[k] for keys in self.steps[key].outputs.values() for k in keys]
                        )
                    )
                if len(closure) <= self.options.get("max_steps", 64):
                    solutions.append(
                        (sum(self.steps[k].cost for k in closure), outputs, closure, constraints)
                    )
        if not solutions:
            raise NoPlan("No dataflow satisfies the requested requirements with the selected plugins")
        cost, outputs, closure, constraints = min(solutions, key=lambda s: s[0])
        self.output_policies = {c["name"]: c.get("publish", {}) for c in constraints}
        for name, keys in outputs.items():
            for key in keys:
                record = self.objects[key]
                types = record.runtime.get("data_types")
                if types is None and record.payload is not None:
                    from .artifacts import artifact

                    types = [artifact(record.payload).type]
                if types is not None:
                    self.app.validate_publication(types, self.output_policies[name])
        cost = sum(self.steps[k].cost for k in closure)
        self.modules = self.app.modules(self.request)
        if self.request.get("steps"):
            for key in closure - self.workflow_existing:
                self.steps[key].dependencies = tuple(
                    sorted(set(self.steps[key].dependencies) | self.workflow_barrier)
                )
        # Topological order is for display; the executor schedules by dependencies.
        ordered, seen = [], set()

        def visit(key):
            if key in seen:
                return
            for dependency in self.steps[key].dependencies:
                visit(dependency)
            seen.add(key)
            ordered.append(self.steps[key])

        for key in sorted(closure):
            visit(key)
        return Plan(
            ordered,
            self.initial,
            self.objects,
            outputs,
            cost,
            "synthesized",
            {
                "engine": "backward-dataflow",
                "expanded_subproblems": self.expanded,
                "evaluated_bindings": self.bindings,
                "candidate_modules": list(self.modules),
                "step_bound": self.options.get("max_steps", 64),
            },
        )
