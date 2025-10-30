"""
Wall classification from closed polylines.

Classifies closed polylines as walls based on geometric properties and context.
"""

from typing import List, Optional
from loguru import logger

from d23d.core.models import (
    Wall,
    Line2D,
    Point2D,
    BuildingGrid,
    BoundingBox,
)
from d23d.parsers.polyline_extractor import PolylineData


class WallClassifier:
    """
    Classifies closed polylines as walls with confidence scoring.

    Classification criteria:
    1. Closed polyline (required)
    2. Reasonable dimensions (perimeter, area)
    3. Rectangular or near-rectangular shape (bonus confidence)
    4. On appropriate layer (if config available)
    5. Proximity to building grid (exterior walls typically on grid)
    """

    def __init__(
        self,
        min_perimeter: float = 1000.0,  # mm (1 meter)
        max_perimeter: float = 500000.0,  # mm (500 meters)
        min_area: float = 100.0,  # mm² (very small room)
        max_area: float = 1000000000.0,  # mm² (1000 m²)
        default_wall_thickness: float = 150.0,  # mm
        default_wall_height: float = 3000.0,  # mm (3 meters)
    ):
        """
        Initialize wall classifier.

        Args:
            min_perimeter: Minimum polyline perimeter to consider as wall
            max_perimeter: Maximum polyline perimeter
            min_area: Minimum enclosed area
            max_area: Maximum enclosed area
            default_wall_thickness: Default wall thickness for generated walls
            default_wall_height: Default wall height
        """
        self.min_perimeter = min_perimeter
        self.max_perimeter = max_perimeter
        self.min_area = min_area
        self.max_area = max_area
        self.default_wall_thickness = default_wall_thickness
        self.default_wall_height = default_wall_height

    def classify(
        self,
        polylines: List[PolylineData],
        grid: Optional[BuildingGrid] = None,
        wall_layers: Optional[List[str]] = None,
    ) -> List[Wall]:
        """
        Classify polylines as walls.

        Args:
            polylines: List of extracted polylines
            grid: Building grid (for exterior wall detection)
            wall_layers: List of expected wall layer names

        Returns:
            List of classified Wall objects with confidence scores
        """
        logger.info(f"Classifying {len(polylines)} polylines as walls")

        walls = []

        for polyline in polylines:
            # Must be closed
            if not polyline.is_closed:
                continue

            # Check dimensions
            perimeter = polyline.perimeter()
            area = polyline.area()

            if not (self.min_perimeter <= perimeter <= self.max_perimeter):
                logger.debug(
                    f"Skipping polyline: perimeter {perimeter:.0f}mm out of range"
                )
                continue

            if not (self.min_area <= area <= self.max_area):
                logger.debug(f"Skipping polyline: area {area:.0f}mm² out of range")
                continue

            # Calculate confidence score
            confidence = self._calculate_confidence(polyline, grid, wall_layers)

            # Convert polyline to wall segments
            wall_segments = self._polyline_to_walls(polyline)

            for wall in wall_segments:
                wall.confidence = confidence
                wall.source_layer = polyline.layer
                walls.append(wall)

        logger.success(f"Classified {len(walls)} wall segments with avg confidence")

        return walls

    def _calculate_confidence(
        self,
        polyline: PolylineData,
        grid: Optional[BuildingGrid],
        wall_layers: Optional[List[str]],
    ) -> float:
        """
        Calculate confidence score for wall classification.

        Factors:
        - Layer name match: +0.2
        - Rectangular shape: +0.15
        - Reasonable dimensions: +0.1
        - Proximity to grid: +0.15 (exterior walls)
        - Base confidence: 0.4

        Args:
            polyline: Polyline to classify
            grid: Building grid (optional)
            wall_layers: Expected wall layer names (optional)

        Returns:
            Confidence score (0.0-1.0)
        """
        confidence = 0.4  # Base confidence for closed polyline

        # Layer name bonus
        if wall_layers and polyline.layer in wall_layers:
            confidence += 0.2
        elif wall_layers:
            # Partial match for layer containing "wall"
            if "wall" in polyline.layer.lower():
                confidence += 0.1

        # Rectangular shape bonus
        if polyline.is_rectangular():
            confidence += 0.15

        # Dimension reasonableness (middle range is more confident)
        perimeter = polyline.perimeter()
        if 10000 <= perimeter <= 200000:  # 10m to 200m perimeter
            confidence += 0.1

        # Grid proximity bonus (exterior walls)
        if grid is not None:
            bbox = polyline.bounding_box()
            grid_bbox = grid.bounding_box

            # Check if polyline bbox is close to grid bbox (exterior wall indicator)
            tolerance = 500.0  # mm

            is_near_grid_boundary = (
                abs(bbox[0] - grid_bbox.min_x) < tolerance
                or abs(bbox[1] - grid_bbox.min_y) < tolerance
                or abs(bbox[2] - grid_bbox.max_x) < tolerance
                or abs(bbox[3] - grid_bbox.max_y) < tolerance
            )

            if is_near_grid_boundary:
                confidence += 0.15

        return min(1.0, confidence)

    def _polyline_to_walls(self, polyline: PolylineData) -> List[Wall]:
        """
        Convert polyline vertices to individual wall segments.

        Args:
            polyline: Closed polyline representing room/space boundary

        Returns:
            List of Wall objects (one per edge)
        """
        walls = []

        points = polyline.points
        num_points = len(points)

        for i in range(num_points):
            # Get current and next point (wrapping around for closed polyline)
            p1 = points[i]
            p2 = points[(i + 1) % num_points]

            # Create wall centerline
            centerline = Line2D(start=p1, end=p2, layer=polyline.layer)

            # Determine if exterior wall (basic heuristic: on perimeter)
            # More sophisticated detection would use spatial relationships
            is_exterior = self._is_perimeter_wall(polyline, i)

            wall = Wall(
                centerline=centerline,
                thickness=self.default_wall_thickness,
                height=self.default_wall_height,
                is_exterior=is_exterior,
                is_structural=is_exterior,  # Assume exterior walls are structural
                confidence=0.5,  # Will be updated by caller
                source_layer=polyline.layer,
            )

            walls.append(wall)

        return walls

    def _is_perimeter_wall(self, polyline: PolylineData, segment_index: int) -> bool:
        """
        Heuristic to determine if wall segment is likely exterior/perimeter.

        Args:
            polyline: The polyline containing the wall
            segment_index: Index of the wall segment

        Returns:
            True if likely exterior wall
        """
        # Simple heuristic: Check if polyline has large area
        # Larger enclosed areas are more likely to be building perimeter
        area = polyline.area()

        # Threshold: 100 m² (100,000,000 mm²)
        # Rooms larger than this are likely building perimeter
        return area > 100_000_000

    def classify_exterior_vs_interior(
        self, walls: List[Wall], grid: Optional[BuildingGrid] = None
    ) -> List[Wall]:
        """
        Refine exterior vs interior classification using spatial analysis.

        Args:
            walls: Initial wall classification
            grid: Building grid for context

        Returns:
            Walls with updated is_exterior flags
        """
        # TODO: Implement more sophisticated spatial analysis
        # - Find outermost bounding polygon
        # - Use grid boundaries as reference
        # - Analyze wall connectivity

        logger.debug("Exterior/interior classification refinement not yet implemented")
        return walls


def classify_walls(
    polylines: List[PolylineData],
    grid: Optional[BuildingGrid] = None,
    wall_layers: Optional[List[str]] = None,
) -> List[Wall]:
    """
    Convenience function to classify walls from polylines.

    Args:
        polylines: Extracted polylines from DXF
        grid: Building grid (optional, improves confidence)
        wall_layers: Expected wall layer names (optional)

    Returns:
        List of classified Wall objects
    """
    classifier = WallClassifier()
    walls = classifier.classify(polylines, grid, wall_layers)

    # Refine exterior/interior classification
    walls = classifier.classify_exterior_vs_interior(walls, grid)

    return walls
