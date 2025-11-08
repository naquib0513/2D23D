"""
Column height detection from multi-floor DXF analysis.

This module determines column heights by analyzing which floors each column appears on.
A column that appears on Ground, First, and Second floors but not Third floor
has a height equal to the elevation difference from Ground to Second floor.
"""

from typing import Dict, List, Tuple, Optional
from pathlib import Path
from loguru import logger

from d23d.parsers.dxf_parser import DXFParser
from d23d.detection.column_detector import detect_columns
from d23d.core.models import Column


# Default floor elevations (mm) - can be overridden
# NOTE: These represent ABSOLUTE heights from ground (0.0) where column TOPS reach
# Columns drawn at Floor 01 (ground) are ground columns
# Columns drawn at Floor 02 reach to 1st floor slab (8m)
# Columns drawn at Floor 03 reach to 2nd floor slab (12m)
# Columns drawn at Floor 04 reach to 3rd floor slab (16m)
# Pattern: Floor number indicates where columns are DRAWN, elevation is where tops REACH
# CRITICAL: "Floor 03" in DXF filename = "Second Floor Level" but columns reach THIRD floor (16m)
DEFAULT_FLOOR_ELEVATIONS = {
    "00": 0.0,      # Foundation/Basement (base)
    "01": 0.0,      # Ground Floor base (columns start here)
    "02": 12000.0,  # Columns last seen on Floor 02 → reach 2nd floor (12m) - 3 columns
    "03": 16000.0,  # Columns last seen on Floor 03 → reach 3rd floor (16m) - THE MAJORITY (50 columns)
    "04": 8000.0,   # Columns last seen on Floor 04 → reach 1st floor (8m) - 1 column only
    "05": 24000.0,  # Columns last seen on Floor 05 → reach roof (24m) - 2 columns
    "06": 24000.0,  # Roof Level (24m)
    "07": 28000.0,  # Beam Level (+4m)
}


def detect_column_heights(
    ground_floor_dxf: str,
    upper_floor_dxfs: Dict[str, str],
    floor_elevations: Optional[Dict[str, float]] = None,
    position_tolerance: float = 100.0
) -> Dict[Tuple[float, float], float]:
    """
    Detect column heights by analyzing column positions across multiple floors.

    Args:
        ground_floor_dxf: Path to ground floor DXF file
        upper_floor_dxfs: Dict mapping floor number to DXF path (e.g., {"02": "path.dxf", "03": "path.dxf"})
        floor_elevations: Dict mapping floor number to elevation in mm (default: uses DEFAULT_FLOOR_ELEVATIONS)
        position_tolerance: Position matching tolerance in mm (default: 100mm)

    Returns:
        Dict mapping (x, y) position to detected height in mm
    """
    if floor_elevations is None:
        floor_elevations = DEFAULT_FLOOR_ELEVATIONS

    logger.info("Detecting column heights from multi-floor analysis")
    logger.info(f"  Ground floor: {Path(ground_floor_dxf).name}")
    logger.info(f"  Upper floors: {len(upper_floor_dxfs)}")

    # Parse ground floor
    logger.debug("Parsing ground floor")
    ground_parser = DXFParser(ground_floor_dxf)
    ground_parser.parse()
    ground_columns = detect_columns(ground_parser)
    logger.info(f"  Ground floor columns: {len(ground_columns)}")

    # Parse upper floors
    upper_floor_columns = {}
    for floor_num, dxf_path in sorted(upper_floor_dxfs.items()):
        logger.debug(f"Parsing floor {floor_num}")
        parser = DXFParser(dxf_path)
        parser.parse()
        columns = detect_columns(parser)
        upper_floor_columns[floor_num] = columns
        logger.info(f"  Floor {floor_num} columns: {len(columns)}")

    # Analyze each ground floor column
    column_heights = {}
    ground_elevation = floor_elevations.get("01", 0.0)

    for ground_col in ground_columns:
        gx = ground_col.location.x
        gy = ground_col.location.y
        col_pos = (round(gx), round(gy))  # Round to nearest mm for dict key

        # Track which floors this column appears on
        # KEY FIX: Don't break on first missing floor - check ALL floors
        # Some DXFs don't show continuing columns on intermediate floors
        floor_presence = {}

        for floor_num in sorted(upper_floor_dxfs.keys()):
            floor_columns = upper_floor_columns[floor_num]

            # Check if this ground column appears on this upper floor
            found = False
            for upper_col in floor_columns:
                dx = abs(gx - upper_col.location.x)
                dy = abs(gy - upper_col.location.y)

                if dx <= position_tolerance and dy <= position_tolerance:
                    found = True
                    break

            floor_presence[floor_num] = found

        # Find the LAST floor where column appears
        # (handles cases where column skips intermediate floors in DXF but continues higher)
        last_floor = "01"  # At minimum, column is on ground floor
        last_elevation = 8000.0  # Default: ground columns reach to 1st floor (8m)

        for floor_num in sorted(upper_floor_dxfs.keys(), reverse=True):
            if floor_presence.get(floor_num, False):
                # This is the highest floor where column was found
                last_floor = floor_num
                last_elevation = floor_elevations.get(floor_num, last_elevation)
                break

        # Calculate height from ground to last floor
        # For ground-only columns (last_floor="01"), use default 8000mm
        height_mm = last_elevation - ground_elevation
        column_heights[col_pos] = height_mm

    logger.info(f"Detected heights for {len(column_heights)} columns")

    # Log height distribution
    from collections import Counter
    height_counts = Counter(column_heights.values())
    logger.info("Column height distribution:")
    for height_mm, count in sorted(height_counts.items()):
        height_m = height_mm / 1000.0
        pct = count / len(column_heights) * 100
        logger.info(f"  {height_m}m: {count} columns ({pct:.1f}%)")

    return column_heights


def apply_detected_heights(
    columns: List[Column],
    detected_heights: Dict[Tuple[float, float], float],
    default_height: float = 8000.0
) -> List[Column]:
    """
    Apply detected heights to column objects.

    Args:
        columns: List of Column objects (will be modified in place)
        detected_heights: Dict mapping (x, y) position to height in mm
        default_height: Default height if no match found (default: 8000mm = 8m)

    Returns:
        Same list of Column objects with updated heights
    """
    matched = 0
    unmatched = 0

    for column in columns:
        col_pos = (round(column.location.x), round(column.location.y))

        if col_pos in detected_heights:
            detected_height = detected_heights[col_pos]
            column.height = detected_height
            matched += 1
        else:
            # No match found - use default
            column.height = default_height
            unmatched += 1
            logger.debug(f"No height detected for column at {col_pos}, using default {default_height}mm")

    logger.info(f"Applied heights: {matched} matched, {unmatched} used default")

    return columns


def get_floor_dxf_paths(
    dxf_directory: str,
    floor_pattern: str = "SJTII-STR-Structural Plan - {floor_num} *.dxf"
) -> Dict[str, str]:
    """
    Get DXF file paths for all available floors.

    Args:
        dxf_directory: Directory containing DXF files
        floor_pattern: Pattern for DXF filenames (must contain {floor_num} placeholder)

    Returns:
        Dict mapping floor number to DXF path
    """
    dxf_dir = Path(dxf_directory)
    floor_dxfs = {}

    # Common floor numbers
    floor_numbers = ["00", "01", "02", "03", "04", "05", "06", "07"]

    for floor_num in floor_numbers:
        # Try to find matching file
        pattern = floor_pattern.replace("{floor_num}", floor_num)
        matches = list(dxf_dir.glob(pattern))

        if matches:
            floor_dxfs[floor_num] = str(matches[0])

    return floor_dxfs
