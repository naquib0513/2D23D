"""
Wall detection from DXF using configured layer mappings.
"""

from typing import List, Tuple
from loguru import logger
import math

from d23d.parsers.dxf_parser import DXFParser
from d23d.core.models import Wall, Line2D, Point2D
from d23d.core.config import get_default_config, Config
from d23d.detection.wall_intersections import detect_wall_intersections, adjust_walls_at_intersections


def _points_close(p1: Point2D, p2: Point2D, tolerance: float = 10.0) -> bool:
    """Check if two points are within tolerance distance (mm)."""
    return p1.distance_to(p2) < tolerance


def _angles_close(angle1: float, angle2: float, tolerance: float = 0.017) -> bool:
    """
    Check if two angles are close (within tolerance radians).

    tolerance=0.017 radians ≈ 1 degree
    """
    # Normalize angles to [0, π] since lines are bidirectional
    a1 = abs(angle1) % math.pi
    a2 = abs(angle2) % math.pi

    diff = abs(a1 - a2)
    # Handle wrap-around (e.g., 179° and 1° are close)
    if diff > math.pi / 2:
        diff = math.pi - diff

    return diff < tolerance


def _points_collinear(p1: Point2D, p2: Point2D, p3: Point2D, tolerance: float = 50.0) -> bool:
    """
    Check if three points are collinear (on the same line).

    Uses perpendicular distance from p2 to line p1-p3.
    tolerance in mm.
    """
    # Vector from p1 to p3
    dx = p3.x - p1.x
    dy = p3.y - p1.y
    length = math.sqrt(dx * dx + dy * dy)

    if length < 1e-6:
        return True  # p1 and p3 are same point

    # Perpendicular distance from p2 to line p1-p3
    dist = abs((dy * p2.x - dx * p2.y + p3.x * p1.y - p3.y * p1.x)) / length

    return dist < tolerance


def _can_merge_walls(w1: Wall, w2: Wall,
                     angle_tolerance: float = 0.017,
                     point_tolerance: float = 10.0,
                     collinear_tolerance: float = 50.0) -> Tuple[bool, str]:
    """
    Check if two walls can be merged.

    Returns:
        (can_merge, connection_type) where connection_type is:
        - "end_to_start": w1.end connects to w2.start
        - "start_to_end": w1.start connects to w2.end
        - "end_to_end": w1.end connects to w2.end (need to reverse w2)
        - "start_to_start": w1.start connects to w2.start (need to reverse w1)
        - "": cannot merge
    """
    # Must have same thickness and height
    if abs(w1.thickness - w2.thickness) > 1.0 or abs(w1.height - w2.height) > 1.0:
        return False, ""

    # Must have similar angles (collinear)
    angle1 = w1.centerline.angle()
    angle2 = w2.centerline.angle()

    if not _angles_close(angle1, angle2, angle_tolerance):
        return False, ""

    # Check endpoint connections
    w1_start = w1.centerline.start
    w1_end = w1.centerline.end
    w2_start = w2.centerline.start
    w2_end = w2.centerline.end

    # Check all possible connection combinations
    if _points_close(w1_end, w2_start, point_tolerance):
        # Verify collinearity
        if _points_collinear(w1_start, w1_end, w2_end, collinear_tolerance):
            return True, "end_to_start"

    if _points_close(w1_start, w2_end, point_tolerance):
        if _points_collinear(w1_end, w1_start, w2_start, collinear_tolerance):
            return True, "start_to_end"

    if _points_close(w1_end, w2_end, point_tolerance):
        if _points_collinear(w1_start, w1_end, w2_start, collinear_tolerance):
            return True, "end_to_end"

    if _points_close(w1_start, w2_start, point_tolerance):
        if _points_collinear(w1_end, w1_start, w2_end, collinear_tolerance):
            return True, "start_to_start"

    return False, ""


def _merge_two_walls(w1: Wall, w2: Wall, connection_type: str) -> Wall:
    """
    Merge two walls into one continuous wall.

    Args:
        w1: First wall
        w2: Second wall
        connection_type: How they connect (from _can_merge_walls)

    Returns:
        New merged wall
    """
    # Determine the new start and end points based on connection type
    if connection_type == "end_to_start":
        # w1 -> w2: w1.start to w2.end
        new_start = w1.centerline.start
        new_end = w2.centerline.end
    elif connection_type == "start_to_end":
        # w2 -> w1: w2.start to w1.end
        new_start = w2.centerline.start
        new_end = w1.centerline.end
    elif connection_type == "end_to_end":
        # w1.start to w2.start (w2 reversed)
        new_start = w1.centerline.start
        new_end = w2.centerline.start
    elif connection_type == "start_to_start":
        # w1.end to w2.end (w1 reversed)
        new_start = w1.centerline.end
        new_end = w2.centerline.end
    else:
        raise ValueError(f"Invalid connection type: {connection_type}")

    # Create merged wall
    merged_centerline = Line2D(
        start=new_start,
        end=new_end,
        layer=w1.centerline.layer
    )

    # Use average confidence
    avg_confidence = (w1.confidence + w2.confidence) / 2.0

    return Wall(
        centerline=merged_centerline,
        thickness=w1.thickness,
        height=w1.height,
        confidence=avg_confidence,
        source_layer=w1.source_layer,
        metadata={
            **w1.metadata,
            "merged_from": f"{w1.guid},{w2.guid}",
            "segment_count": w1.metadata.get("segment_count", 1) + w2.metadata.get("segment_count", 1)
        }
    )


def merge_wall_segments(walls: List[Wall],
                       max_iterations: int = 10,
                       angle_tolerance: float = 0.017,
                       point_tolerance: float = 10.0) -> List[Wall]:
    """
    Merge collinear wall segments into continuous walls.

    Args:
        walls: List of wall segments
        max_iterations: Maximum merge passes (prevents infinite loops)
        angle_tolerance: Angular tolerance in radians (~1 degree)
        point_tolerance: Point proximity tolerance in mm

    Returns:
        List of merged walls
    """
    if not walls:
        return []

    logger.info(f"Merging {len(walls)} wall segments")

    merged_walls = walls.copy()
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        merged_count = 0
        new_walls = []
        merged_indices = set()

        for i, w1 in enumerate(merged_walls):
            if i in merged_indices:
                continue

            # Try to find a wall to merge with w1
            found_merge = False
            for j, w2 in enumerate(merged_walls[i+1:], start=i+1):
                if j in merged_indices:
                    continue

                can_merge, connection_type = _can_merge_walls(
                    w1, w2,
                    angle_tolerance=angle_tolerance,
                    point_tolerance=point_tolerance
                )

                if can_merge:
                    # Merge w1 and w2
                    merged_wall = _merge_two_walls(w1, w2, connection_type)
                    new_walls.append(merged_wall)
                    merged_indices.add(i)
                    merged_indices.add(j)
                    merged_count += 1
                    found_merge = True
                    break

            if not found_merge:
                # Keep w1 as-is
                new_walls.append(w1)
                merged_indices.add(i)

        logger.debug(f"Iteration {iteration}: merged {merged_count} wall pairs, {len(new_walls)} walls remaining")

        # If no merges happened, we're done
        if merged_count == 0:
            break

        merged_walls = new_walls

    logger.success(f"Merged {len(walls)} segments into {len(merged_walls)} continuous walls")

    return merged_walls


def detect_walls(
    parser: DXFParser,
    config: Config = None,
    min_length_mm: float = None,
    merge_segments: bool = True,
    fix_intersections: bool = True,
) -> List[Wall]:
    """
    Detect walls from DXF using configured layer patterns.

    Args:
        parser: DXF parser with loaded document
        config: Configuration (uses default if None)
        min_length_mm: Minimum wall length (from config if None)
        merge_segments: Whether to merge collinear wall segments (default True)
        fix_intersections: Whether to adjust walls at intersections (default True)

    Returns:
        List of detected Wall objects
    """
    if config is None:
        config = get_default_config()

    if min_length_mm is None:
        min_length_mm = config.get_classification_rule("wall_detection", "min_length_mm", 200)

    logger.info("Detecting walls from configured layers")

    # Get all layers that match wall patterns
    wall_layers = []
    for layer in parser.doc.layers:
        if config.matches_layer_pattern(layer.dxf.name, "walls"):
            wall_layers.append(layer.dxf.name)

    logger.debug(f"Found {len(wall_layers)} wall layers: {wall_layers}")

    # Extract lines from wall layers
    lines = parser.extract_lines(layers=wall_layers if wall_layers else None)

    # Filter by minimum length
    filtered_lines = [line for line in lines if line.length() >= min_length_mm]

    logger.debug(f"Filtered to {len(filtered_lines)} lines (>= {min_length_mm}mm)")

    # Convert lines to walls
    walls = []
    default_thickness = config.get_geometry_default("default_wall_thickness_mm", 150)
    default_height = config.get_classification_rule("wall_detection", "default_height_mm", 3000)

    confidence_threshold = config.get_classification_rule("wall_detection", "confidence_threshold", 0.7)

    for line in filtered_lines:
        # Assign confidence based on layer and length
        # Higher confidence for walls on proper layers and reasonable lengths
        confidence = confidence_threshold
        if line.length() > 1000:  # Longer walls = higher confidence
            confidence = min(1.0, confidence + 0.1)

        wall = Wall(
            centerline=line,
            thickness=default_thickness,
            height=default_height,
            confidence=confidence,
            source_layer=line.layer,
        )
        walls.append(wall)

    logger.success(f"Detected {len(walls)} wall segments")

    # Merge collinear segments into continuous walls
    if merge_segments and len(walls) > 1:
        walls = merge_wall_segments(walls)

    # Detect and fix intersections for clean corners
    if fix_intersections and len(walls) > 1:
        intersections = detect_wall_intersections(walls)
        if intersections:
            walls = adjust_walls_at_intersections(walls, intersections)

    return walls
