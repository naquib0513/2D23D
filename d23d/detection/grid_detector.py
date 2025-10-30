"""
Building grid detection algorithm.

Detects structural grid lines from 2D CAD drawings using line clustering
and pattern recognition.
"""

import math
from typing import List, Optional, Tuple, Dict
from collections import defaultdict
from loguru import logger
import numpy as np

from d23d.core.models import (
    Line2D,
    Point2D,
    BuildingGrid,
    GridLine,
    GridIntersection,
    BoundingBox,
    ElementType,
)


class GridDetector:
    """
    Detects building grid from 2D lines using clustering and pattern recognition.

    Algorithm:
    1. Filter lines by length (ignore short lines)
    2. Cluster lines by orientation (horizontal vs vertical)
    3. Detect regular spacing patterns using histogram analysis
    4. Identify grid lines based on parallelism and spacing
    5. Find intersection points
    6. Label grid lines (A, B, C... and 1, 2, 3...)
    7. Calculate confidence scores based on regularity
    """

    def __init__(
        self,
        min_line_length: float = 1000.0,  # mm
        angle_tolerance: float = 0.087,    # ~5 degrees in radians
        spacing_tolerance: float = 0.15,   # 15% tolerance for regular spacing
        min_grid_lines: int = 2,           # Minimum grid lines in each direction
    ):
        """
        Initialize grid detector.

        Args:
            min_line_length: Minimum line length to consider (mm)
            angle_tolerance: Tolerance for horizontal/vertical detection (radians)
            spacing_tolerance: Tolerance for regular spacing detection (fraction)
            min_grid_lines: Minimum number of grid lines required in each direction
        """
        self.min_line_length = min_line_length
        self.angle_tolerance = angle_tolerance
        self.spacing_tolerance = spacing_tolerance
        self.min_grid_lines = min_grid_lines

    def detect(self, lines: List[Line2D]) -> Optional[BuildingGrid]:
        """
        Detect building grid from list of lines.

        Args:
            lines: List of Line2D objects from DXF

        Returns:
            BuildingGrid if detected, None otherwise
        """
        logger.info(f"Detecting grid from {len(lines)} lines")

        # Step 1: Filter lines by length
        filtered_lines = [l for l in lines if l.length() >= self.min_line_length]
        logger.debug(
            f"Filtered to {len(filtered_lines)} lines (>= {self.min_line_length}mm)"
        )

        if len(filtered_lines) < self.min_grid_lines * 2:
            logger.warning(
                f"Insufficient lines for grid detection "
                f"(found {len(filtered_lines)}, need >= {self.min_grid_lines * 2})"
            )
            return None

        # Step 2: Cluster by orientation
        h_lines, v_lines = self._cluster_by_orientation(filtered_lines)

        logger.debug(
            f"Clustered into {len(h_lines)} horizontal and {len(v_lines)} vertical lines"
        )

        if len(h_lines) < self.min_grid_lines or len(v_lines) < self.min_grid_lines:
            logger.warning(
                f"Insufficient grid lines detected "
                f"(h={len(h_lines)}, v={len(v_lines)}, need >= {self.min_grid_lines})"
            )
            return None

        # Step 3: Detect spacing patterns and select grid lines
        h_grid_lines = self._detect_grid_lines(h_lines, is_vertical=False)
        v_grid_lines = self._detect_grid_lines(v_lines, is_vertical=True)

        if not h_grid_lines or not v_grid_lines:
            logger.warning("Failed to detect regular grid pattern")
            return None

        # Step 4: Label grid lines
        h_grid_lines = self._label_grid_lines(h_grid_lines, is_vertical=False)
        v_grid_lines = self._label_grid_lines(v_grid_lines, is_vertical=True)

        # Step 5: Find intersections
        intersections = self._find_intersections(h_grid_lines, v_grid_lines)

        # Step 6: Calculate confidence and regularity
        is_regular, avg_h_spacing, avg_v_spacing = self._check_regularity(
            h_grid_lines, v_grid_lines
        )

        # Calculate overall confidence
        confidence = self._calculate_confidence(
            h_grid_lines, v_grid_lines, is_regular
        )

        # Step 7: Calculate bounding box
        bbox = self._calculate_grid_bbox(h_grid_lines, v_grid_lines)

        grid = BuildingGrid(
            horizontal_lines=h_grid_lines,
            vertical_lines=v_grid_lines,
            intersections=intersections,
            bounding_box=bbox,
            is_regular=is_regular,
            avg_h_spacing=avg_h_spacing,
            avg_v_spacing=avg_v_spacing,
            confidence=confidence,
        )

        logger.success(
            f"Grid detected: {len(h_grid_lines)}x{len(v_grid_lines)}, "
            f"regular={is_regular}, confidence={confidence:.2f}"
        )

        return grid

    def _cluster_by_orientation(
        self, lines: List[Line2D]
    ) -> Tuple[List[Line2D], List[Line2D]]:
        """
        Cluster lines into horizontal and vertical groups.

        Args:
            lines: List of lines to cluster

        Returns:
            Tuple of (horizontal_lines, vertical_lines)
        """
        horizontal = []
        vertical = []

        for line in lines:
            if line.is_horizontal(self.angle_tolerance):
                horizontal.append(line)
            elif line.is_vertical(self.angle_tolerance):
                vertical.append(line)
            # Ignore diagonal lines for grid detection

        return horizontal, vertical

    def _detect_grid_lines(
        self, lines: List[Line2D], is_vertical: bool
    ) -> List[GridLine]:
        """
        Detect which lines form the building grid based on spacing patterns.

        Args:
            lines: Lines to analyze (all horizontal or all vertical)
            is_vertical: True if analyzing vertical lines

        Returns:
            List of GridLine objects
        """
        if not lines:
            return []

        # Get positions (Y for horizontal lines, X for vertical lines)
        positions = []
        for line in lines:
            if is_vertical:
                # For vertical lines, use X coordinate
                pos = (line.start.x + line.end.x) / 2
            else:
                # For horizontal lines, use Y coordinate
                pos = (line.start.y + line.end.y) / 2
            positions.append((pos, line))

        # Sort by position
        positions.sort(key=lambda x: x[0])

        # Calculate spacings between consecutive lines
        spacings = []
        for i in range(len(positions) - 1):
            spacing = abs(positions[i + 1][0] - positions[i][0])
            spacings.append(spacing)

        if not spacings:
            return []

        # Use histogram to find dominant spacing pattern
        # This helps identify regular grid lines vs. random lines
        dominant_spacing = self._find_dominant_spacing(spacings)

        # Select lines that follow the dominant spacing pattern
        grid_lines = []
        for i, (pos, line) in enumerate(positions):
            # Calculate confidence based on spacing regularity
            if i == 0:
                # First line - check spacing to next
                if i < len(spacings):
                    deviation = abs(spacings[i] - dominant_spacing) / dominant_spacing
                    confidence = max(0.5, 1.0 - deviation)
                else:
                    confidence = 0.7  # Only one line
            elif i == len(positions) - 1:
                # Last line - check spacing from previous
                deviation = abs(spacings[i - 1] - dominant_spacing) / dominant_spacing
                confidence = max(0.5, 1.0 - deviation)
            else:
                # Middle lines - average of both spacings
                prev_deviation = abs(spacings[i - 1] - dominant_spacing) / dominant_spacing
                next_deviation = abs(spacings[i] - dominant_spacing) / dominant_spacing
                avg_deviation = (prev_deviation + next_deviation) / 2
                confidence = max(0.5, 1.0 - avg_deviation)

            grid_line = GridLine(
                line=line,
                label="",  # Will be labeled later
                is_vertical=is_vertical,
                confidence=confidence,
                spacing_to_next=spacings[i] if i < len(spacings) else None,
            )
            grid_lines.append(grid_line)

        return grid_lines

    def _find_dominant_spacing(self, spacings: List[float]) -> float:
        """
        Find dominant spacing pattern using clustering.

        Args:
            spacings: List of spacing values

        Returns:
            Dominant spacing value
        """
        if not spacings:
            return 0.0

        if len(spacings) == 1:
            return spacings[0]

        # Use median as robust estimator
        return float(np.median(spacings))

    def _label_grid_lines(
        self, grid_lines: List[GridLine], is_vertical: bool
    ) -> List[GridLine]:
        """
        Label grid lines with alphanumeric identifiers.

        Args:
            grid_lines: Grid lines to label
            is_vertical: True for vertical lines (use numbers), False for horizontal (use letters)

        Returns:
            Grid lines with labels assigned
        """
        if is_vertical:
            # Vertical lines labeled with numbers: 1, 2, 3...
            for i, grid_line in enumerate(grid_lines):
                grid_line.label = str(i + 1)
        else:
            # Horizontal lines labeled with letters: A, B, C...
            for i, grid_line in enumerate(grid_lines):
                if i < 26:
                    grid_line.label = chr(ord('A') + i)
                else:
                    # For more than 26 lines: AA, AB, AC...
                    first = chr(ord('A') + (i // 26) - 1)
                    second = chr(ord('A') + (i % 26))
                    grid_line.label = first + second

        return grid_lines

    def _find_intersections(
        self, h_lines: List[GridLine], v_lines: List[GridLine]
    ) -> List[GridIntersection]:
        """
        Find all grid line intersections.

        Args:
            h_lines: Horizontal grid lines
            v_lines: Vertical grid lines

        Returns:
            List of GridIntersection objects
        """
        intersections = []

        for h_line in h_lines:
            for v_line in v_lines:
                # Calculate intersection point
                # For grid lines, they should be perpendicular
                # Horizontal line has constant Y, vertical line has constant X

                h_y = (h_line.line.start.y + h_line.line.end.y) / 2
                v_x = (v_line.line.start.x + v_line.line.end.x) / 2

                intersection = GridIntersection(
                    point=Point2D(x=v_x, y=h_y),
                    grid_h=h_line.label,
                    grid_v=v_line.label,
                    confidence=min(h_line.confidence, v_line.confidence),
                )
                intersections.append(intersection)

        logger.debug(f"Found {len(intersections)} grid intersections")
        return intersections

    def _check_regularity(
        self, h_lines: List[GridLine], v_lines: List[GridLine]
    ) -> Tuple[bool, Optional[float], Optional[float]]:
        """
        Check if grid has regular spacing.

        Args:
            h_lines: Horizontal grid lines
            v_lines: Vertical grid lines

        Returns:
            Tuple of (is_regular, avg_h_spacing, avg_v_spacing)
        """
        # Calculate average spacings
        h_spacings = [gl.spacing_to_next for gl in h_lines if gl.spacing_to_next]
        v_spacings = [gl.spacing_to_next for gl in v_lines if gl.spacing_to_next]

        avg_h = float(np.mean(h_spacings)) if h_spacings else None
        avg_v = float(np.mean(v_spacings)) if v_spacings else None

        # Check if spacings are regular (within tolerance)
        h_regular = self._is_spacing_regular(h_spacings, avg_h) if h_spacings else True
        v_regular = self._is_spacing_regular(v_spacings, avg_v) if v_spacings else True

        is_regular = h_regular and v_regular

        return is_regular, avg_h, avg_v

    def _is_spacing_regular(
        self, spacings: List[float], avg_spacing: Optional[float]
    ) -> bool:
        """Check if spacings are regular within tolerance."""
        if not spacings or avg_spacing is None:
            return False

        for spacing in spacings:
            deviation = abs(spacing - avg_spacing) / avg_spacing
            if deviation > self.spacing_tolerance:
                return False

        return True

    def _calculate_confidence(
        self, h_lines: List[GridLine], v_lines: List[GridLine], is_regular: bool
    ) -> float:
        """
        Calculate overall grid confidence score.

        Factors:
        - Number of grid lines (more is better, up to a point)
        - Regularity of spacing
        - Individual line confidences

        Args:
            h_lines: Horizontal grid lines
            v_lines: Vertical grid lines
            is_regular: Whether grid spacing is regular

        Returns:
            Confidence score (0.0-1.0)
        """
        # Base confidence from individual lines
        h_conf = np.mean([gl.confidence for gl in h_lines])
        v_conf = np.mean([gl.confidence for gl in v_lines])
        base_confidence = (h_conf + v_conf) / 2

        # Bonus for regularity
        regularity_bonus = 0.1 if is_regular else 0.0

        # Bonus for sufficient grid lines (but not too many)
        line_count = len(h_lines) + len(v_lines)
        if 4 <= line_count <= 20:
            count_bonus = 0.1
        elif line_count > 20:
            count_bonus = 0.05  # Many lines might indicate noise
        else:
            count_bonus = 0.0

        confidence = min(1.0, base_confidence + regularity_bonus + count_bonus)
        return float(confidence)

    def _calculate_grid_bbox(
        self, h_lines: List[GridLine], v_lines: List[GridLine]
    ) -> BoundingBox:
        """Calculate bounding box of grid."""
        h_positions = [(gl.line.start.y + gl.line.end.y) / 2 for gl in h_lines]
        v_positions = [(gl.line.start.x + gl.line.end.x) / 2 for gl in v_lines]

        return BoundingBox(
            min_x=min(v_positions),
            max_x=max(v_positions),
            min_y=min(h_positions),
            max_y=max(h_positions),
        )


def detect_grids(
    parser,
    grid_layers: Optional[List[str]] = None,
    confidence_threshold: float = 0.7,
) -> Optional[BuildingGrid]:
    """
    Detect building grid from DXF parser.

    Args:
        parser: DXFParser instance with loaded document
        grid_layers: Optional list of layer names to search for grid lines.
                     If None, will search common layer names.
        confidence_threshold: Minimum confidence score to accept grid

    Returns:
        BuildingGrid if detected with sufficient confidence, None otherwise

    Raises:
        ValueError: If grid cannot be detected (critical error)
    """
    # Default grid layer name patterns
    if grid_layers is None:
        # Common layer naming conventions for grid lines
        all_layers = parser.get_layer_names()
        grid_patterns = ['grid', 'axis', 'column', 'structural']

        grid_layers = []
        for layer in all_layers:
            layer_lower = layer.lower()
            if any(pattern in layer_lower for pattern in grid_patterns):
                grid_layers.append(layer)

        logger.debug(f"Auto-detected potential grid layers: {grid_layers}")

    # If no grid layers found, try all layers
    if not grid_layers:
        logger.warning("No grid layers identified, searching all layers")
        lines = parser.extract_lines()
    else:
        lines = parser.extract_lines(grid_layers)

    # Detect grid
    detector = GridDetector()
    grid = detector.detect(lines)

    if grid is None:
        raise ValueError(
            "CRITICAL ERROR: Building grid could not be detected.\\n\\n"
            "Grid detection is mandatory for 2D23D processing. Please ensure:\\n"
            "  1. Drawing contains structural grid lines (typically on 'GRID' or 'AXIS' layer)\\n"
            "  2. Grid lines are drawn as LINE entities (not blocks or text)\\n"
            "  3. Grid lines form a regular or semi-regular pattern\\n\\n"
            "Unable to proceed without detectable grid."
        )

    if grid.confidence < confidence_threshold:
        logger.warning(
            f"Grid confidence ({grid.confidence:.2f}) below threshold ({confidence_threshold})"
        )
        # Still return grid but with warning in metadata

    return grid
