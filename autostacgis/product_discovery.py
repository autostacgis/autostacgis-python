"""Resolve wcmatch matches to the roots expected by EOReader.

Candidate markers cover EOReader 0.24.3's native optical and SAR readers.
EOReader's own MTD_REGEX nesting rules and valid_mtd checks decide whether a
candidate is a product. A filename match alone never declares a product valid.
"""

import re
from importlib import import_module
from pathlib import Path


def local_path(value):
    return Path(value)


def optional_import(name, extra):
    return import_module(name)


# Broad candidate patterns, not a second implementation of satellite recognition.
# Keep the corresponding multiline YAML example and coverage fixtures in sync.
PRODUCT_MARKERS = (
    "*.SAFE",
    "*.SEN3",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "manifest.json",
    "MTD*.xml",
    "s1*.xml",
    "S2*.json",
    "Oa??_radiance.nc",
    "S?_radiance_an.nc",
    "L*_MTL.txt",
    "*metadata*.xml",
    "*metadata*.json",
    "CSK*.h5",
    "CSG*.h5",
    "TSX1_SAR__*.xml",
    "TDX1_SAR__*.xml",
    "PAZ1_SAR__*.xml",
    "rs2prod*.xsd",
    "DIM_*.xml",
    "product.xml",
    "*.TIL",
    "ICEYE_*.xml",
    "*.xemt",
    "CAPELLA_*.json",
    "*UMBRA*.tif",
    "SV1*.xml",
    "HLS.*.Fmask.tif",
    "DE2_*.dim",
    "METADATA.DIM",
    "S1*_RTC*.kmz",
    "*_TOA.vrt",
    "*_MTD_ALL.xml",
)
DEFAULT_SEARCH_GLOB = "**/@(" + "|".join(PRODUCT_MARKERS) + ")"
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def _patterns(search_glob):
    patterns = [search_glob] if isinstance(search_glob, str) else list(search_glob)
    if not patterns or any(not isinstance(value, str) or not value.strip() for value in patterns):
        raise ValueError("search_glob must contain nonempty wcmatch patterns")
    # YAML double-quoted backslash continuation already joins lines. A literal
    # block may alternatively contain one complete pattern on each line.
    return [line.strip() for value in patterns for line in value.splitlines() if line.strip()]


def _rules(module, constellations):
    try:
        selected = module.Constellation.convert_from(constellations) if constellations else None
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid EOReader constellations: {constellations}") from exc
    if selected and any(value not in module.MTD_REGEX for value in selected):
        raise ValueError(
            "Select native filesystem constellations; STAC-only and custom products need other inputs"
        )
    rules = []
    for constellation, specification in module.MTD_REGEX.items():
        if selected is not None and constellation not in selected:
            continue
        nested = specification.get("nested", 0) if isinstance(specification, dict) else 0
        regex = specification["regex"] if isinstance(specification, dict) else specification
        expressions = regex if isinstance(regex, list) else [regex]
        rules.append((constellation, nested, [re.compile(value) for value in expressions]))
    return rules


def _manifest_root(path):
    # Planet orders may place imagery below a manifest-bearing product directory.
    for parent in path.parents:
        if (parent / "manifest.json").is_file():
            return parent
    return path.parent


def discover_products(search_glob=DEFAULT_SEARCH_GLOB, *, root_dir=".", constellations=None):
    """Return sorted, unique local product directories/archives, without opening pixels.

    Patterns may select product roots, supported archives, or metadata markers.
    Relative patterns use root_dir; absolute patterns are also accepted. Lists
    support exclusions beginning with ! across the entire pattern set. The
    filesystem must be local or mounted; wcmatch does not list cloud buckets.
    """
    glob = optional_import("wcmatch.glob", "eoreader")
    module = optional_import("eoreader.reader", "eoreader")
    root = local_path(root_dir).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    flags = glob.GLOBSTAR | glob.BRACE | glob.EXTGLOB | glob.GLOBTILDE | glob.NEGATE | glob.IGNORECASE
    matches = sorted(
        {
            (root / match).resolve(strict=True)
            for match in glob.iglob(_patterns(search_glob), root_dir=str(root), flags=flags)
        }
    )
    rules = _rules(module, constellations)
    candidates = set()
    for match in matches:
        if match.is_dir() or str(match).lower().endswith(ARCHIVE_SUFFIXES):
            candidates.add(match)
        elif match.name.lower() == "manifest.json":
            candidates.add(match.parent)
        else:
            for constellation, nested, expressions in rules:
                if any(expression.search(match.name) for expression in expressions):
                    if nested < 0:
                        candidates.add(_manifest_root(match))
                    elif len(match.parents) > nested:
                        candidates.add(match.parents[nested])
    products = []
    for candidate in sorted(candidates):
        # A fresh reader bounds its internal directory-listing cache to one
        # candidate, including directories that turn out not to be products.
        reader = module.Reader()
        for constellation, nested, expressions in rules:
            if candidate.is_dir() and nested < 0:
                # Planet's recursive recognizer would also accept the parent
                # of an entire collection. Require a product-level marker.
                direct_metadata = any(
                    expression.search(child.name)
                    for child in candidate.iterdir()
                    if child.is_file()
                    for expression in expressions
                )
                if not (candidate / "manifest.json").is_file() and not direct_metadata:
                    continue
            if reader.valid_mtd(candidate, constellation):
                products.append(candidate)
                break
    # Prefer a recognized enclosing product to its internal auxiliary archive
    # or nested metadata directory (notably SAOCOM and Planet delivery layouts).
    products = [p for p in products if not any(parent in products for parent in p.parents)]
    from eoreader.stac.worldview import worldview_pair

    # Both component matches identify one acquisition-level STAC export. Use
    # MUL as the canonical inspection path, including when the glob selects PAN.
    canonical = set()
    for path in products:
        pair = worldview_pair(path)
        canonical.add(pair[0] if pair else path)
    return sorted(canonical)
