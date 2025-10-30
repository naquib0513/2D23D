"""
Wall intersection detection and corner handling.

Detects where walls meet and adjusts geometry for clean connections.
"""

from typing import List, Tuple, Optional, Set
from loguru import logger
import math

from d23d.core.models import Wall, Point2D, Line2D


class WallIntersection:
    """Represents an intersection point where walls meet."""

    def __init__(self, point: Point2D, wall_indices: List[int]):
        """
        Args:
            point: Intersection point
            wall_indices: Indices of walls that meet at this point
        """
        self.point = point
        self.wall_indices = wall_indices

    @property
    def intersection_type(self) -> str:
        """Get intersection type based on number of walls."""
        count = len(self.wall_indices)
        if count == 2:
            return "L-corner"  # Two walls meeting
        elif count == 3:
            return "T-junction"  # Three walls meeting
        elif count >= 4:
            return "X-crossing"  # Four or more walls meeting
        else:
            return "endpoint"  # Single wall endpoint


def _point_to_line_distance(point: Point2D, line_start: Point2D, line_end: Point2D) -> float:
    """
    Calculate perpendicular distance from point to line segment.

    Returns:
        Distance in mm
    """
    # Vector from line_start to line_end
    dx = line_end.x - line_start.x
    dy = line_end.y - line_start.y
    length_sq = dx * dx + dy * dy

    if length_sq < 1e-10:  # Line is essentially a point
        return point.distance_to(line_start)

    # Parameter t represents position along line (0 = start, 1 = end)
    t = max(0, min(1, ((point.x - line_start.x) * dx + (point.y - line_start.y) * dy) / length_sq))

    # Closest point on line segment
    closest_x = line_start.x + t * dx
    closest_y = line_start.y + t * dy
    closest = Point2D(x=closest_x, y=closest_y)

    return point.distance_to(closest)


def _lines_intersect(line1_start: Point2D, line1_end: Point2D,
                     line2_start: Point2D, line2_end: Point2D,
                     tolerance: float = 10.0) -> Optional[Point2D]:
    """
    Find intersection point of two line segments.

    Args:
        tolerance: Distance tolerance in mm for considering lines as intersecting

    Returns:
        Intersection point if lines intersect within tolerance, None otherwise
    """
    x1, y1 = line1_start.x, line1_start.y
    x2, y2 = line1_end.x, line1_end.y
    x3, y3 = line2_start.x, line2_start.y
    x4, y4 = line2_end.x, line2_end.y

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)

    # Lines are parallel
    if abs(denom) < 1e-10:
        return None

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    u = -((x1 - x2) * (y1 - y3) - (y1 - y2) * (x1 - x3)) / denom

    # Check if intersection is within both line segments (with tolerance)
    # Allow slightly outside segments to catch near-misses
    tolerance_param = tolerance / max(
        math.sqrt((x2 - x1)**2 + (y2 - y1)**2),
        math.sqrt((x4 - x3)**2 + (y4 - y3)**2)
    )

    if -tolerance_param <= t <= 1 + tolerance_param and -tolerance_param <= u <= 1 + tolerance_param:
        # Calculate intersection point
        ix = x1 + t * (x2 - x1)
        iy = y1 + t * (y2 - y1)
        return Point2D(x=ix, y=iy)

    return None


def detect_wall_intersections(walls: List[Wall],
                              endpoint_tolerance: float = 50.0,
                              intersection_tolerance: float = 50.0) -> List[WallIntersection]:
    """
    Detect points where walls intersect or meet.

    Args:
        walls: List of walls to analyze
        endpoint_tolerance: Distance tolerance for grouping endpoints (mm)
        intersection_tolerance: Distance tolerance for detecting line intersections (mm)

    Returns:
        List of wall intersections
    """
    if len(walls) < 2:
        return []

    logger.info(f"Detecting intersections for {len(walls)} walls")

    # Collect all potential intersection points
    intersection_candidates = []  # List of (point, wall_index, connection_type)

    # 1. Collect all wall endpoints
    for i, wall in enumerate(walls):
        intersection_candidates.append((wall.centerline.start, i, "start"))
        intersection_candidates.append((wall.centerline.end, i, "end"))

    # 2. Detect line-line intersections (T-junctions, X-crossings)
    for i, wall1 in enumerate(walls):
        for j, wall2 in enumerate(walls[i+1:], start=i+1):
            intersection_point = _lines_intersect(
                wall1.centerline.start, wall1.centerline.end,
                wall2.centerline.start, wall2.centerline.end,
                tolerance=intersection_tolerance
            )
            if intersection_point:
                # Check if this isn't just an endpoint connection (already covered)
                is_endpoint = False
                for pt, _, _ in intersection_candidates:
                    if pt.distance_to(intersection_point) < endpoint_tolerance:
                        is_endpoint = True
                        break

                if not is_endpoint:
                    intersection_candidates.append((intersection_point, i, "crossing"))
                    intersection_candidates.append((intersection_point, j, "crossing"))

    # 3. Group nearby points into intersections
    intersections = []
    processed = set()

    for idx, (point, wall_idx, conn_type) in enumerate(intersection_candidates):
        if idx in processed:
            continue

        # Find all points within tolerance
        nearby_indices = [idx]
        nearby_wall_indices = {wall_idx}

        for other_idx, (other_point, other_wall_idx, _) in enumerate(intersection_candidates):
            if other_idx != idx and other_idx not in processed:
                if point.distance_to(other_point) < endpoint_tolerance:
                    nearby_indices.append(other_idx)
                    nearby_wall_indices.add(other_wall_idx)

        # Mark all nearby points as processed
        processed.update(nearby_indices)

        # Create intersection if multiple walls meet here
        if len(nearby_wall_indices) > 1:
            # Use average position of all nearby points
            avg_x = sum(intersection_candidates[i][0].x for i in nearby_indices) / len(nearby_indices)
            avg_y = sum(intersection_candidates[i][0].y for i in nearby_indices) / len(nearby_indices)
            avg_point = Point2D(x=avg_x, y=avg_y)

            intersection = WallIntersection(avg_point, sorted(list(nearby_wall_indices)))
            intersections.append(intersection)

    logger.success(f"Detected {len(intersections)} wall intersections")

    # Log intersection types
    type_counts = {}
    for intersection in intersections:
        itype = intersection.intersection_type
        type_counts[itype] = type_counts.get(itype, 0) + 1

    for itype, count in sorted(type_counts.items()):
        logger.debug(f"  {itype}: {count}")

    return intersections


def adjust_walls_at_intersections(walls: List[Wall],
                                  intersections: List[WallIntersection],
                                  snap_tolerance: float = 50.0,
                                  extend_for_thickness: bool = True) -> List[Wall]:
    """
    Adjust wall endpoints to snap to intersection points and extend/trim for wall thickness.

    Args:
        walls: List of walls
        intersections: Detected intersections
        snap_tolerance: Distance within which to snap endpoints (mm)
        extend_for_thickness: Whether to extend/trim walls to account for thickness at corners

    Returns:
        List of adjusted walls (new Wall objects)
    """
    if not intersections:
        return walls

    logger.info(f"Adjusting {len(walls)} walls at {len(intersections)} intersections")

    adjusted_walls = []
    adjustment_count = 0

    for i, wall in enumerate(walls):
        new_start = wall.centerline.start
        new_end = wall.centerline.end
        adjusted = False

        # Check if this wall's endpoints should snap to any intersection
        for intersection in intersections:
            if i not in intersection.wall_indices:
                continue

            # Snap endpoints to intersection first
            start_at_intersection = wall.centerline.start.distance_to(intersection.point) < snap_tolerance
            end_at_intersection = wall.centerline.end.distance_to(intersection.point) < snap_tolerance

            if start_at_intersection:
                new_start = intersection.point
                adjusted = True

            if end_at_intersection:
                new_end = intersection.point
                adjusted = True

            # Then extend for thickness at L-corners only
            if extend_for_thickness and len(intersection.wall_indices) == 2:
                # L-corner: extend wall by half of OTHER wall's thickness
                wall_index_in_intersection = intersection.wall_indices.index(i)
                other_wall_idx = intersection.wall_indices[1 - wall_index_in_intersection]
                other_wall = walls[other_wall_idx]

                # Extension distance (half of other wall's thickness)
                extension = other_wall.thickness / 2.0

                if start_at_intersection:
                    # Extend start point AWAY from end point
                    dx = new_start.x - wall.centerline.end.x
                    dy = new_start.y - wall.centerline.end.y
                    length = math.sqrt(dx * dx + dy * dy)
                    if length > 0:
                        # Unit vector pointing from end toward start
                        ux, uy = dx / length, dy / length
                        new_start = Point2D(
                            x=new_start.x + ux * extension,
                            y=new_start.y + uy * extension
                        )

                if end_at_intersection:
                    # Extend end point AWAY from start point
                    dx = new_end.x - wall.centerline.start.x
                    dy = new_end.y - wall.centerline.start.y
                    length = math.sqrt(dx * dx + dy * dy)
                    if length > 0:
                        # Unit vector pointing from start toward end
                        ux, uy = dx / length, dy / length
                        new_end = Point2D(
                            x=new_end.x + ux * extension,
                            y=new_end.y + uy * extension
                        )

        if adjusted:
            # Create new wall with adjusted endpoints
            adjusted_centerline = Line2D(
                start=new_start,
                end=new_end,
                layer=wall.centerline.layer
            )

            adjusted_wall = Wall(
                centerline=adjusted_centerline,
                thickness=wall.thickness,
                height=wall.height,
                confidence=wall.confidence,
                source_layer=wall.source_layer,
                metadata={
                    **wall.metadata,
                    "adjusted_at_intersection": True
                }
            )
            adjusted_walls.append(adjusted_wall)
            adjustment_count += 1
        else:
            # Keep original wall
            adjusted_walls.append(wall)

    logger.success(f"Adjusted {adjustment_count} walls at intersections")

    return adjusted_walls
