"""Deterministic spatial predicates over GeoJSON and STAC bounding-box metadata."""

from math import isfinite

from shapely.errors import ShapelyError
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import unary_union


def geometry(value):
    """Return a geometry, or unknown for missing, empty, or malformed metadata."""
    try:
        if isinstance(value, dict):
            if value.get("type") == "Feature":
                return geometry(value.get("geometry"))
            result = shape(value)
        elif isinstance(value, (list, tuple)) and len(value) in (4, 6):
            if any(type(number) not in (int, float) or not isfinite(number) for number in value):
                return None
            west, south, east, north = value if len(value) == 4 else (value[0], value[1], value[3], value[4])
            if south > north:
                return None
            if west > east:
                if not (-180 <= east <= west <= 180):
                    return None
                result = unary_union([box(west, south, 180, north), box(-180, south, east, north)])
            elif west == east and south == north:
                result = Point(west, south)
            elif west == east or south == north:
                result = LineString([(west, south), (east, north)])
            else:
                result = box(west, south, east, north)
        else:
            return None
        return result if not result.is_empty and result.is_valid else None
    except (ShapelyError, TypeError, ValueError, KeyError, AttributeError):
        return None


def intersects(left=None, right=None):
    left, right = geometry(left), geometry(right)
    return None if left is None or right is None else bool(left.intersects(right))


def covers(left=None, right=None):
    left, right = geometry(left), geometry(right)
    return None if left is None or right is None else bool(left.covers(right))


def within(left=None, right=None):
    return covers(right, left)
