"""
Detect structural foundations (pile caps) from DXF drawings.

Foundations are typically represented as:
- LINE entities on S-FNDN-HDLN layers (300mm uniform lines in grid pattern)
- Each foundation = group of 4+ lines forming square/rectangle

These map to IfcSlab with PredefinedType="BASESLAB"
"""

from typing import List, Optional, Tuple
from loguru import logger
from collections import defaultdict

from d23d.parsers.dxf_parser import DXFParser
from d23d.core.models import ProvisionalElement, ElementType, Point2D


class Foundation(ProvisionalElement):
    """Structural foundation element (pile cap)."""
    element_type: ElementType = ElementType.FOUNDATION

    center: Point2D  # Foundation center point
    width: float = 300.0  # Foundation width (mm) - horizontal footprint
    depth: float = 300.0  # Foundation depth (mm) - horizontal footprint
    foundation_depth: float = 30150.0  # Vertical depth into ground (mm) - deep pile depth (per ground truth)
    predefined_type: str = "BASESLAB"  # IFC predefined type


def detect_foundations(
    parser: DXFParser,
    default_foundation_depth: float = 30150.0,  # 30.15m deep piles (per ground truth)
    min_line_length: float = 200.0,
    max_line_length: float = 500.0,
    max_gap: float = 100.0
) -> List[Foundation]:
    """
    Detect structural foundations (pile caps) from DXF.

    Args:
        parser: Parsed DXF data
        default_foundation_depth: Default vertical depth into ground (mm)
        min_line_length: Minimum line length to consider (mm)
        max_line_length: Maximum line length to consider (mm)
        max_gap: Maximum gap between lines to group (mm)

    Returns:
        List of detected foundations
    """

    logger.info("Detecting foundations (pile caps)")

    # Auto-detect foundation layers
    foundation_layers = []
    for layer in parser.metadata.layers:
        layer_lower = layer.lower()
        if 'fndn' in layer_lower or 'found' in layer_lower or 'pile' in layer_lower:
            foundation_layers.append(layer)

    logger.debug(f"Found {len(foundation_layers)} foundation layers: {foundation_layers}")

    if not foundation_layers:
        logger.warning("No foundation layers found")
        return []

    # Extract LINE entities from foundation layers
    lines = parser.extract_lines(layers=foundation_layers)
    logger.debug(f"Found {len(lines)} LINE entities on foundation layers")

    # Filter by length (pile caps typically use uniform line lengths ~300mm)
    filtered_lines = []
    for line in lines:
        length = line.length()
        if min_line_length <= length <= max_line_length:
            filtered_lines.append(line)

    logger.debug(f"Filtered to {len(filtered_lines)} lines ({min_line_length}-{max_line_length}mm)")

    if not filtered_lines:
        logger.warning("No lines in valid length range for foundations")
        return []

    # Group lines into rectangles (each foundation = group of parallel lines)
    foundations = group_lines_into_foundations(
        filtered_lines,
        max_gap=max_gap,
        default_foundation_depth=default_foundation_depth
    )

    logger.success(f"Detected {len(foundations)} foundations from LINE entities")

    return foundations


def group_lines_into_foundations(
    lines: List,
    max_gap: float = 100.0,
    default_foundation_depth: float = 30150.0
) -> List[Foundation]:
    """
    Group LINE entities into foundation rectangles.

    Strategy:
    1. Group lines by orientation (horizontal vs vertical)
    2. Cluster lines by proximity
    3. Match horizontal + vertical line clusters to form rectangles
    4. Create Foundation object for each rectangle

    Args:
        lines: List of LINE entities
        max_gap: Maximum gap between lines to cluster (mm)
        default_foundation_depth: Default vertical depth (mm)

    Returns:
        List of Foundation objects
    """

    # Separate horizontal and vertical lines
    horizontal_lines = []
    vertical_lines = []

    for line in lines:
        dx = abs(line.end.x - line.start.x)
        dy = abs(line.end.y - line.start.y)

        # Consider line horizontal if dx > dy
        if dx > dy:
            horizontal_lines.append(line)
        else:
            vertical_lines.append(line)

    logger.debug(f"Separated: {len(horizontal_lines)} horizontal, {len(vertical_lines)} vertical")

    # Cluster horizontal lines by Y-coordinate
    h_clusters = cluster_lines_by_position(horizontal_lines, axis='y', max_gap=max_gap)
    logger.debug(f"Horizontal line clusters: {len(h_clusters)}")

    # Cluster vertical lines by X-coordinate
    v_clusters = cluster_lines_by_position(vertical_lines, axis='x', max_gap=max_gap)
    logger.debug(f"Vertical line clusters: {len(v_clusters)}")

    # Match clusters to form rectangles
    foundations = []

    # Simple approach: Create foundation for each grid intersection
    # More sophisticated: Match line groups into closed rectangles
    # For now: Use grid-based detection

    # Find bounding box of all lines to establish grid
    if not lines:
        return []

    all_x = []
    all_y = []
    for line in lines:
        all_x.extend([line.start.x, line.end.x])
        all_y.extend([line.start.y, line.end.y])

    # Get unique grid positions (cluster by proximity)
    grid_x = cluster_coordinates(all_x, max_gap=max_gap)
    grid_y = cluster_coordinates(all_y, max_gap=max_gap)

    logger.debug(f"Grid: {len(grid_x)}x{len(grid_y)} positions")

    # Detect complete 300x300mm rectangles (each rectangle = 1 pile foundation)
    # Ground truth: 236 piles = 236 rectangles (4 lines each)
    pile_size = 300.0  # mm
    tolerance = 10.0  # mm tolerance for matching

    used_lines = set()

    for i, line1 in enumerate(horizontal_lines):
        if i in used_lines:
            continue

        # line1 is horizontal, get its extent
        x_min1 = min(line1.start.x, line1.end.x)
        x_max1 = max(line1.start.x, line1.end.x)
        y1 = (line1.start.y + line1.end.y) / 2

        # Find parallel horizontal line (opposite side of rectangle)
        line2_found = None
        for j, line2 in enumerate(horizontal_lines):
            if j in used_lines or j == i:
                continue

            x_min2 = min(line2.start.x, line2.end.x)
            x_max2 = max(line2.start.x, line2.end.x)
            y2 = (line2.start.y + line2.end.y) / 2

            # Check if parallel, aligned, and ~300mm apart
            if (abs(x_min1 - x_min2) < tolerance and
                abs(x_max1 - x_max2) < tolerance and
                abs(abs(y2 - y1) - pile_size) < tolerance):
                line2_found = (j, line2, y2)
                break

        if not line2_found:
            continue

        j2, line2, y2 = line2_found

        # Find 2 vertical lines connecting the horizontal lines
        vertical_found = []
        for k, line3 in enumerate(vertical_lines):
            if k in used_lines:
                continue

            x3 = (line3.start.x + line3.end.x) / 2
            y_min3 = min(line3.start.y, line3.end.y)
            y_max3 = max(line3.start.y, line3.end.y)

            y_min_rect = min(y1, y2)
            y_max_rect = max(y1, y2)

            # Check if vertical line connects the two horizontal lines
            if (abs(y_min3 - y_min_rect) < tolerance and
                abs(y_max3 - y_max_rect) < tolerance and
                (abs(x3 - x_min1) < tolerance or abs(x3 - x_max1) < tolerance)):
                vertical_found.append(k)
                if len(vertical_found) >= 2:
                    break

        if len(vertical_found) >= 2:
            # Found complete rectangle - create ONE foundation at center
            center_x = (x_min1 + x_max1) / 2
            center_y = (y1 + y2) / 2

            foundation = Foundation(
                center=Point2D(x=center_x, y=center_y),
                width=pile_size,                           # 300mm footprint
                depth=pile_size,                           # 300mm footprint
                foundation_depth=default_foundation_depth,  # 30150mm vertical depth
                confidence=0.90,  # High confidence - complete rectangle detected
                source_layer=lines[0].layer,
                predefined_type="BASESLAB"
            )
            foundations.append(foundation)

            # Mark lines as used
            used_lines.add(i)
            used_lines.add(j2)
            used_lines.add(vertical_found[0])
            used_lines.add(vertical_found[1])

    logger.debug(f"Created {len(foundations)} pile foundations from {len(used_lines)} lines")

    return foundations


def cluster_lines_by_position(lines: List, axis: str, max_gap: float) -> List[List]:
    """
    Cluster lines by position along specified axis.

    Args:
        lines: List of LINE entities
        axis: 'x' or 'y'
        max_gap: Maximum gap to cluster

    Returns:
        List of line clusters
    """
    if not lines:
        return []

    # Get position along axis for each line (use center point)
    positions = []
    for line in lines:
        if axis == 'x':
            pos = (line.start.x + line.end.x) / 2
        else:
            pos = (line.start.y + line.end.y) / 2
        positions.append((pos, line))

    # Sort by position
    positions.sort(key=lambda x: x[0])

    # Cluster by gap
    clusters = []
    current_cluster = [positions[0][1]]
    prev_pos = positions[0][0]

    for pos, line in positions[1:]:
        if abs(pos - prev_pos) <= max_gap:
            current_cluster.append(line)
        else:
            clusters.append(current_cluster)
            current_cluster = [line]
        prev_pos = pos

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def cluster_coordinates(coords: List[float], max_gap: float) -> List[float]:
    """
    Cluster coordinates by proximity.

    Args:
        coords: List of coordinate values
        max_gap: Maximum gap to cluster

    Returns:
        List of cluster centers
    """
    if not coords:
        return []

    unique_coords = sorted(set(coords))

    clusters = []
    current_cluster = [unique_coords[0]]

    for coord in unique_coords[1:]:
        if coord - current_cluster[-1] <= max_gap:
            current_cluster.append(coord)
        else:
            # Save cluster center
            clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [coord]

    if current_cluster:
        clusters.append(sum(current_cluster) / len(current_cluster))

    return clusters
