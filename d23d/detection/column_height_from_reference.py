"""
Extract column heights from reference IFC and apply to detected columns.

When DXF doesn't show column termination points reliably, we can use
a reference IFC (ground truth) to get accurate heights by position matching.
"""

import ifcopenshell
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from loguru import logger

from d23d.core.models import Column


def extract_heights_from_reference_ifc(
    reference_ifc_path: str,
    ground_storey_name: str = "GROUND FLOOR LEVEL",
    position_tolerance: float = 100.0
) -> Dict[Tuple[float, float], float]:
    """
    Extract column heights from reference IFC by position.

    Args:
        reference_ifc_path: Path to reference IFC file
        ground_storey_name: Name of ground floor storey in reference
        position_tolerance: Position matching tolerance in mm

    Returns:
        Dict mapping (x, y) position to height in mm
    """
    ref_path = Path(reference_ifc_path)
    if not ref_path.exists():
        logger.warning(f"Reference IFC not found: {reference_ifc_path}")
        return {}

    logger.info(f"Extracting column heights from reference IFC")
    logger.info(f"  File: {ref_path.name}")

    # Load reference IFC
    ref_ifc = ifcopenshell.open(str(ref_path))

    # Find ground floor storey
    ground_storey = None
    for storey in ref_ifc.by_type('IfcBuildingStorey'):
        if ground_storey_name in storey.Name:
            ground_storey = storey
            break

    if not ground_storey:
        logger.warning(f"Ground floor storey '{ground_storey_name}' not found in reference")
        return {}

    logger.info(f"  Found storey: {ground_storey.Name}")

    # Get columns in ground floor
    ground_columns = []
    for rel in ref_ifc.by_type('IfcRelContainedInSpatialStructure'):
        if rel.RelatingStructure == ground_storey:
            ground_columns.extend([e for e in rel.RelatedElements if e.is_a('IfcColumn')])

    logger.info(f"  Found {len(ground_columns)} columns on ground floor")

    # Extract heights and positions
    column_heights = {}

    for col in ground_columns:
        # Get column position
        if not col.ObjectPlacement:
            continue

        placement = col.ObjectPlacement
        if not placement.is_a('IfcLocalPlacement'):
            continue

        rel_placement = placement.RelativePlacement
        if not rel_placement:
            continue

        location = rel_placement.Location
        if not location:
            continue

        x = location.Coordinates[0] * 1000.0  # Convert to mm
        y = location.Coordinates[1] * 1000.0
        col_pos = (round(x), round(y))

        # Get column height from geometry or quantity sets
        height_m = None

        # Method 1: Try geometry (IfcExtrudedAreaSolid)
        if col.Representation:
            for rep in col.Representation.Representations:
                for item in rep.Items:
                    if item.is_a('IfcExtrudedAreaSolid'):
                        height_m = item.Depth
                        break
                if height_m:
                    break

        # Method 2: Try quantity sets (calculate from volume/area)
        if not height_m and hasattr(col, 'IsDefinedBy'):
            for rel in col.IsDefinedBy:
                if hasattr(rel, 'RelatingPropertyDefinition'):
                    pset = rel.RelatingPropertyDefinition
                    if pset.Name == 'Qto_ColumnBaseQuantities':
                        # Calculate height from volume / cross-section
                        volume = None
                        area = None
                        for q in pset.Quantities:
                            if q.Name == 'NetVolume' and hasattr(q, 'VolumeValue'):
                                volume = q.VolumeValue
                            if q.Name == 'CrossSectionArea' and hasattr(q, 'AreaValue'):
                                area = q.AreaValue

                        if volume and area and area > 0:
                            height_m = volume / area
                            break

        if height_m:
            height_mm = height_m * 1000.0
            column_heights[col_pos] = height_mm

    logger.info(f"  Extracted heights for {len(column_heights)} columns")

    # Log height distribution
    from collections import Counter
    height_counts = Counter(column_heights.values())
    logger.info("  Reference column heights:")
    for height_mm, count in sorted(height_counts.items()):
        height_m = height_mm / 1000.0
        pct = count / len(column_heights) * 100 if column_heights else 0
        logger.info(f"    {height_m:.1f}m: {count} columns ({pct:.1f}%)")

    return column_heights


def apply_reference_heights(
    columns: List[Column],
    reference_heights: Dict[Tuple[float, float], float],
    default_height: float = 12000.0,
    position_tolerance: float = 100.0
) -> List[Column]:
    """
    Apply heights from reference IFC to detected columns.

    Args:
        columns: List of Column objects (will be modified in place)
        reference_heights: Dict mapping (x, y) to height in mm
        default_height: Default height if no match found
        position_tolerance: Position matching tolerance in mm

    Returns:
        Same list of Column objects with updated heights
    """
    if not reference_heights:
        logger.warning("No reference heights available, using defaults")
        for col in columns:
            col.height = default_height
        return columns

    matched = 0
    unmatched = 0

    for column in columns:
        col_x = round(column.location.x)
        col_y = round(column.location.y)

        # Try exact match first
        col_pos = (col_x, col_y)
        if col_pos in reference_heights:
            column.height = reference_heights[col_pos]
            matched += 1
            continue

        # Try fuzzy match within tolerance
        best_match = None
        best_distance = float('inf')

        for ref_pos, ref_height in reference_heights.items():
            ref_x, ref_y = ref_pos
            dx = abs(col_x - ref_x)
            dy = abs(col_y - ref_y)
            distance = (dx**2 + dy**2)**0.5

            if distance < position_tolerance and distance < best_distance:
                best_distance = distance
                best_match = ref_height

        if best_match is not None:
            column.height = best_match
            matched += 1
        else:
            column.height = default_height
            unmatched += 1
            logger.debug(f"No reference height for column at ({col_x}, {col_y}), using default")

    logger.info(f"Applied reference heights: {matched} matched, {unmatched} used default")

    return columns
