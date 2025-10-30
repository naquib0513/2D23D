"""
Polyline extraction from DXF files.

Extracts LWPOLYLINE and POLYLINE entities for wall classification.
"""

from typing import List, Optional
from loguru import logger
import ezdxf
from ezdxf.entities import LWPolyline, Polyline

from d23d.core.models import Point2D


class PolylineData:
    """Extracted polyline data from DXF."""

    def __init__(
        self,
        points: List[Point2D],
        is_closed: bool,
        layer: str,
    ):
        """
        Initialize polyline data.

        Args:
            points: List of vertices
            is_closed: Whether polyline forms a closed loop
            layer: DXF layer name
        """
        self.points = points
        self.is_closed = is_closed
        self.layer = layer

    def perimeter(self) -> float:
        """Calculate perimeter length."""
        if len(self.points) < 2:
            return 0.0

        total = 0.0
        for i in range(len(self.points) - 1):
            total += self.points[i].distance_to(self.points[i + 1])

        # Add closing segment if closed
        if self.is_closed and len(self.points) > 2:
            total += self.points[-1].distance_to(self.points[0])

        return total

    def area(self) -> float:
        """
        Calculate area using shoelace formula (only for closed polylines).

        Returns:
            Area in square units (0 if not closed)
        """
        if not self.is_closed or len(self.points) < 3:
            return 0.0

        # Shoelace formula
        area = 0.0
        for i in range(len(self.points)):
            j = (i + 1) % len(self.points)
            area += self.points[i].x * self.points[j].y
            area -= self.points[j].x * self.points[i].y

        return abs(area) / 2.0

    def bounding_box(self) -> tuple[float, float, float, float]:
        """
        Get bounding box (min_x, min_y, max_x, max_y).

        Returns:
            Tuple of (min_x, min_y, max_x, max_y)
        """
        if not self.points:
            return (0.0, 0.0, 0.0, 0.0)

        min_x = min(p.x for p in self.points)
        min_y = min(p.y for p in self.points)
        max_x = max(p.x for p in self.points)
        max_y = max(p.y for p in self.points)

        return (min_x, min_y, max_x, max_y)

    def is_rectangular(self, tolerance: float = 100.0) -> bool:
        """
        Check if polyline is approximately rectangular.

        Args:
            tolerance: Maximum deviation from right angles (mm)

        Returns:
            True if polyline has 4 corners with ~90 degree angles
        """
        if not self.is_closed or len(self.points) != 4:
            return False

        # Check if all angles are approximately 90 degrees
        # For a rectangle, cross product of consecutive edges should be ~0
        import math

        for i in range(4):
            p1 = self.points[i]
            p2 = self.points[(i + 1) % 4]
            p3 = self.points[(i + 2) % 4]

            # Vectors
            v1_x = p2.x - p1.x
            v1_y = p2.y - p1.y
            v2_x = p3.x - p2.x
            v2_y = p3.y - p2.y

            # Dot product (should be ~0 for perpendicular)
            dot = v1_x * v2_x + v1_y * v2_y
            len1 = math.sqrt(v1_x ** 2 + v1_y ** 2)
            len2 = math.sqrt(v2_x ** 2 + v2_y ** 2)

            if len1 > 0 and len2 > 0:
                cos_angle = dot / (len1 * len2)
                # Should be close to 0 (cos(90°) = 0)
                if abs(cos_angle) > 0.1:  # ~84-96 degrees tolerance
                    return False

        return True


def extract_polylines(
    doc: ezdxf.document.Drawing,
    layers: Optional[List[str]] = None,
    min_points: int = 2,
) -> List[PolylineData]:
    """
    Extract polylines from DXF document.

    Args:
        doc: ezdxf Drawing object
        layers: List of layer names to extract from (None = all layers)
        min_points: Minimum number of points to consider

    Returns:
        List of PolylineData objects
    """
    msp = doc.modelspace()
    polylines = []

    # Extract LWPOLYLINE entities
    for entity in msp.query("LWPOLYLINE"):
        if layers and entity.dxf.layer not in layers:
            continue

        points = [Point2D(x=p[0], y=p[1]) for p in entity.get_points()]

        if len(points) < min_points:
            continue

        polyline = PolylineData(
            points=points,
            is_closed=entity.closed,
            layer=entity.dxf.layer,
        )
        polylines.append(polyline)

    # Extract POLYLINE entities
    for entity in msp.query("POLYLINE"):
        if layers and entity.dxf.layer not in layers:
            continue

        points = [Point2D(x=v.dxf.location.x, y=v.dxf.location.y) for v in entity.vertices]

        if len(points) < min_points:
            continue

        polyline = PolylineData(
            points=points,
            is_closed=entity.is_closed,
            layer=entity.dxf.layer,
        )
        polylines.append(polyline)

    logger.debug(
        f"Extracted {len(polylines)} polylines "
        f"from {len(layers) if layers else 'all'} layer(s)"
    )

    return polylines
