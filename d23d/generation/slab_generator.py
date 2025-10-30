"""
Slab generation from building boundaries.

Generates floor slabs based on building perimeter and grid extents.
"""

from typing import List, Optional
from loguru import logger

from d23d.core.models import (
    Slab,
    BuildingGrid,
    Point2D,
    Wall,
)


class SlabGenerator:
    """
    Generates floor slabs from building boundaries.

    Strategy: Use building grid extents as slab boundary, or use
    perimeter walls if available.
    """

    def __init__(
        self,
        default_thickness: float = 150.0,  # mm
        default_elevation: float = 0.0,  # mm
        expand_beyond_grid: float = 500.0,  # mm - extend slab beyond grid
    ):
        """
        Initialize slab generator.

        Args:
            default_thickness: Slab thickness (mm)
            default_elevation: Slab Z elevation (mm)
            expand_beyond_grid: Distance to extend slab beyond grid (mm)
        """
        self.default_thickness = default_thickness
        self.default_elevation = default_elevation
        self.expand_beyond_grid = expand_beyond_grid

    def generate_from_grid(self, grid: BuildingGrid) -> Slab:
        """
        Generate a rectangular slab based on grid extents.

        Args:
            grid: Building grid

        Returns:
            Slab object covering grid area
        """
        logger.info("Generating slab from grid extents")

        bbox = grid.bounding_box

        # Expand slightly beyond grid
        boundary = [
            Point2D(x=bbox.min_x - self.expand_beyond_grid, y=bbox.min_y - self.expand_beyond_grid),
            Point2D(x=bbox.max_x + self.expand_beyond_grid, y=bbox.min_y - self.expand_beyond_grid),
            Point2D(x=bbox.max_x + self.expand_beyond_grid, y=bbox.max_y + self.expand_beyond_grid),
            Point2D(x=bbox.min_x - self.expand_beyond_grid, y=bbox.max_y + self.expand_beyond_grid),
        ]

        slab = Slab(
            boundary=boundary,
            thickness=self.default_thickness,
            elevation=self.default_elevation,
            is_roof=False,
            confidence=grid.confidence * 0.9,  # Slightly lower than grid confidence
        )

        area_m2 = (
            (bbox.max_x - bbox.min_x + 2 * self.expand_beyond_grid)
            * (bbox.max_y - bbox.min_y + 2 * self.expand_beyond_grid)
        ) / 1_000_000

        logger.success(f"Generated slab: {area_m2:.1f} m²")

        return slab

    def generate_from_walls(
        self, walls: List[Wall], grid: Optional[BuildingGrid] = None
    ) -> List[Slab]:
        """
        Generate slabs from perimeter walls (more accurate than grid-based).

        Args:
            walls: List of classified walls
            grid: Optional grid for confidence scoring

        Returns:
            List of Slab objects (typically one for building perimeter)
        """
        logger.info("Generating slabs from wall boundaries")

        # TODO: Implement wall-based slab generation
        # - Find exterior walls
        # - Trace perimeter
        # - Create slab boundary from perimeter

        logger.warning("Wall-based slab generation not yet implemented")
        logger.info("Falling back to grid-based generation")

        if grid:
            return [self.generate_from_grid(grid)]
        else:
            logger.error("Cannot generate slab: no grid or walls available")
            return []


def generate_slabs(
    grid: Optional[BuildingGrid] = None,
    walls: Optional[List[Wall]] = None,
    slab_thickness: float = 150.0,
) -> List[Slab]:
    """
    Convenience function to generate floor slabs.

    Args:
        grid: Building grid (fallback method)
        walls: Classified walls (preferred method)
        slab_thickness: Slab thickness in mm

    Returns:
        List of Slab objects
    """
    generator = SlabGenerator(default_thickness=slab_thickness)

    if walls and len(walls) > 0:
        return generator.generate_from_walls(walls, grid)
    elif grid:
        return [generator.generate_from_grid(grid)]
    else:
        logger.error("Cannot generate slabs: no input provided")
        return []
