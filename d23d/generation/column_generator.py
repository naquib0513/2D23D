"""
Column generation at grid intersections.

Places structural columns at grid intersection points with configurable dimensions.
"""

from typing import List, Optional
from loguru import logger

from d23d.core.models import (
    Column,
    BuildingGrid,
    GridIntersection,
    Point2D,
)


class ColumnGenerator:
    """
    Generates columns at grid intersections.

    Follows constitutional principle: Automate the grunt work of placing
    columns at every grid intersection, with confidence scores.
    """

    def __init__(
        self,
        default_width: float = 300.0,  # mm
        default_depth: float = 300.0,  # mm
        default_height: float = 3000.0,  # mm (3 meters floor-to-floor)
        min_confidence: float = 0.7,  # Only place at high-confidence intersections
    ):
        """
        Initialize column generator.

        Args:
            default_width: Column width (mm)
            default_depth: Column depth (mm)
            default_height: Column height (mm)
            min_confidence: Minimum grid intersection confidence to place column
        """
        self.default_width = default_width
        self.default_depth = default_depth
        self.default_height = default_height
        self.min_confidence = min_confidence

    def generate(
        self,
        grid: BuildingGrid,
        exclude_perimeter: bool = False,
    ) -> List[Column]:
        """
        Generate columns at grid intersections.

        Args:
            grid: Detected building grid
            exclude_perimeter: If True, skip columns on grid perimeter
                              (useful if perimeter columns are in walls)

        Returns:
            List of Column objects with confidence scores
        """
        logger.info(
            f"Generating columns at {len(grid.intersections)} grid intersections"
        )

        columns = []

        # Get perimeter intersection labels if excluding
        perimeter_labels = set()
        if exclude_perimeter:
            perimeter_labels = self._get_perimeter_labels(grid)

        for intersection in grid.intersections:
            # Skip if confidence too low
            if intersection.confidence < self.min_confidence:
                logger.debug(
                    f"Skipping intersection {intersection.label()}: "
                    f"confidence {intersection.confidence:.2f} < {self.min_confidence}"
                )
                continue

            # Skip if on perimeter
            if exclude_perimeter and intersection.label() in perimeter_labels:
                logger.debug(f"Skipping perimeter intersection {intersection.label()}")
                continue

            # Create column at intersection
            column = Column(
                location=intersection.point,
                width=self.default_width,
                depth=self.default_depth,
                height=self.default_height,
                rotation=0.0,
                grid_reference=intersection.label(),
                confidence=intersection.confidence,
            )

            columns.append(column)

        logger.success(f"Generated {len(columns)} columns")

        return columns

    def _get_perimeter_labels(self, grid: BuildingGrid) -> set[str]:
        """
        Get labels of grid intersections on the perimeter.

        Args:
            grid: Building grid

        Returns:
            Set of perimeter intersection labels
        """
        perimeter = set()

        # Get edge grid lines
        if grid.horizontal_lines:
            first_h = grid.horizontal_lines[0].label
            last_h = grid.horizontal_lines[-1].label

            # All intersections with first or last horizontal
            for intersection in grid.intersections:
                if intersection.grid_h == first_h or intersection.grid_h == last_h:
                    perimeter.add(intersection.label())

        if grid.vertical_lines:
            first_v = grid.vertical_lines[0].label
            last_v = grid.vertical_lines[-1].label

            # All intersections with first or last vertical
            for intersection in grid.intersections:
                if intersection.grid_v == first_v or intersection.grid_v == last_v:
                    perimeter.add(intersection.label())

        return perimeter

    def generate_with_sizing(
        self,
        grid: BuildingGrid,
        column_sizes: dict[str, tuple[float, float]],
    ) -> List[Column]:
        """
        Generate columns with custom sizing per grid intersection.

        Args:
            grid: Building grid
            column_sizes: Dict mapping grid labels to (width, depth) tuples
                         e.g., {"A1": (400, 400), "B2": (300, 600)}

        Returns:
            List of Column objects with custom sizes
        """
        columns = self.generate(grid, exclude_perimeter=False)

        # Apply custom sizes
        for column in columns:
            if column.grid_reference in column_sizes:
                width, depth = column_sizes[column.grid_reference]
                column.width = width
                column.depth = depth
                logger.debug(
                    f"Applied custom size to column {column.grid_reference}: "
                    f"{width}x{depth}mm"
                )

        return columns


def generate_columns(
    grid: BuildingGrid,
    exclude_perimeter: bool = False,
    column_width: float = 300.0,
    column_depth: float = 300.0,
    column_height: float = 3000.0,
) -> List[Column]:
    """
    Convenience function to generate columns at grid intersections.

    Args:
        grid: Building grid
        exclude_perimeter: Skip perimeter columns if True
        column_width: Column width in mm
        column_depth: Column depth in mm
        column_height: Column height in mm

    Returns:
        List of Column objects
    """
    generator = ColumnGenerator(
        default_width=column_width,
        default_depth=column_depth,
        default_height=column_height,
    )

    return generator.generate(grid, exclude_perimeter=exclude_perimeter)
