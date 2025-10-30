"""
Core data models for 2D23D module.

All models use Pydantic for validation and serialization.
"""

from enum import Enum
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field, field_validator
import uuid


class ConfidenceLevel(str, Enum):
    """Confidence levels for provisional elements."""
    HIGH = "high"  # 0.8-1.0
    MEDIUM = "medium"  # 0.5-0.79
    LOW = "low"  # 0.0-0.49


class ElementType(str, Enum):
    """Types of building elements we can generate."""
    GRID = "grid"
    GRID_LINE = "grid_line"
    GRID_INTERSECTION = "grid_intersection"
    WALL = "wall"
    COLUMN = "column"
    SLAB = "slab"
    DOOR = "door"
    WINDOW = "window"
    BEAM = "beam"


class Point2D(BaseModel):
    """2D point in drawing space."""
    x: float
    y: float

    def distance_to(self, other: "Point2D") -> float:
        """Calculate Euclidean distance to another point."""
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5

    def __hash__(self) -> int:
        return hash((round(self.x, 6), round(self.y, 6)))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Point2D):
            return False
        return abs(self.x - other.x) < 1e-6 and abs(self.y - other.y) < 1e-6


class Point3D(BaseModel):
    """3D point in model space."""
    x: float
    y: float
    z: float = 0.0

    def distance_to(self, other: "Point3D") -> float:
        """Calculate Euclidean distance to another point."""
        return ((self.x - other.x) ** 2 +
                (self.y - other.y) ** 2 +
                (self.z - other.z) ** 2) ** 0.5


class Line2D(BaseModel):
    """2D line segment in drawing space."""
    start: Point2D
    end: Point2D
    layer: str = "0"

    def length(self) -> float:
        """Calculate line length."""
        return self.start.distance_to(self.end)

    def angle(self) -> float:
        """Calculate line angle in radians (0-2π)."""
        import math
        dx = self.end.x - self.start.x
        dy = self.end.y - self.start.y
        return math.atan2(dy, dx)

    def is_horizontal(self, tolerance: float = 0.1) -> bool:
        """Check if line is approximately horizontal (within tolerance radians)."""
        import math
        angle = abs(self.angle())
        return angle < tolerance or abs(angle - math.pi) < tolerance

    def is_vertical(self, tolerance: float = 0.1) -> bool:
        """Check if line is approximately vertical (within tolerance radians)."""
        import math
        angle = abs(self.angle())
        return abs(angle - math.pi/2) < tolerance


class BoundingBox(BaseModel):
    """Axis-aligned bounding box."""
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    min_z: float = 0.0
    max_z: float = 0.0

    def contains_point(self, point: Point2D) -> bool:
        """Check if point is inside bounding box."""
        return (self.min_x <= point.x <= self.max_x and
                self.min_y <= point.y <= self.max_y)

    def area(self) -> float:
        """Calculate 2D area of bounding box."""
        return (self.max_x - self.min_x) * (self.max_y - self.min_y)


class ProvisionalElement(BaseModel):
    """Base class for all provisional generated elements."""
    guid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    element_type: ElementType
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_layer: Optional[str] = None

    @field_validator('confidence')
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        """Ensure confidence is between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            raise ValueError('Confidence must be between 0.0 and 1.0')
        return v

    def confidence_level(self) -> ConfidenceLevel:
        """Get confidence level category."""
        if self.confidence >= 0.8:
            return ConfidenceLevel.HIGH
        elif self.confidence >= 0.5:
            return ConfidenceLevel.MEDIUM
        else:
            return ConfidenceLevel.LOW


class GridLine(ProvisionalElement):
    """Detected grid line (axis)."""
    element_type: ElementType = ElementType.GRID_LINE

    line: Line2D
    label: str  # e.g., "A", "B", "C" or "1", "2", "3"
    is_vertical: bool
    spacing_to_next: Optional[float] = None  # Distance to next parallel grid line

    def __str__(self) -> str:
        return f"GridLine({self.label}, confidence={self.confidence:.2f})"


class GridIntersection(ProvisionalElement):
    """Grid line intersection point."""
    element_type: ElementType = ElementType.GRID_INTERSECTION

    point: Point2D
    grid_h: str  # Horizontal grid label (e.g., "A")
    grid_v: str  # Vertical grid label (e.g., "1")

    def label(self) -> str:
        """Get combined label for this intersection."""
        return f"{self.grid_h}{self.grid_v}"

    def __str__(self) -> str:
        return f"GridIntersection({self.label()}, confidence={self.confidence:.2f})"


class BuildingGrid(ProvisionalElement):
    """Complete building grid system."""
    element_type: ElementType = ElementType.GRID

    horizontal_lines: List[GridLine]  # Typically labeled A, B, C...
    vertical_lines: List[GridLine]    # Typically labeled 1, 2, 3...
    intersections: List[GridIntersection]
    bounding_box: BoundingBox

    is_regular: bool = True  # True if grid spacing is uniform
    avg_h_spacing: Optional[float] = None  # Average horizontal spacing
    avg_v_spacing: Optional[float] = None  # Average vertical spacing

    def get_intersection(self, h_label: str, v_label: str) -> Optional[GridIntersection]:
        """Get intersection by grid labels."""
        for intersection in self.intersections:
            if intersection.grid_h == h_label and intersection.grid_v == v_label:
                return intersection
        return None

    def __str__(self) -> str:
        return (f"BuildingGrid({len(self.horizontal_lines)}x{len(self.vertical_lines)}, "
                f"regular={self.is_regular}, confidence={self.confidence:.2f})")


class Wall(ProvisionalElement):
    """Detected or generated wall."""
    element_type: ElementType = ElementType.WALL

    centerline: Line2D
    thickness: float  # in drawing units (typically mm)
    height: float = 3000.0  # default 3m in mm
    is_exterior: bool = False
    is_structural: bool = False


class Column(ProvisionalElement):
    """Structural column."""
    element_type: ElementType = ElementType.COLUMN

    location: Point2D
    width: float
    depth: float
    height: float = 3000.0  # default 3m in mm
    rotation: float = 0.0  # rotation in radians
    grid_reference: Optional[str] = None  # e.g., "A1" if at grid intersection


class Slab(ProvisionalElement):
    """Floor or roof slab."""
    element_type: ElementType = ElementType.SLAB

    boundary: List[Point2D]  # Closed polygon
    thickness: float
    elevation: float = 0.0  # Z coordinate
    is_roof: bool = False


class DrawingMetadata(BaseModel):
    """Metadata about the source drawing."""
    file_path: str
    file_format: str  # "DXF", "IFC", "DWG"
    drawing_scale: Optional[str] = None
    units: str = "mm"  # Drawing units
    layers: List[str] = Field(default_factory=list)
    bounding_box: Optional[BoundingBox] = None

    # Quality metrics
    has_detectable_grid: bool = False
    has_scale_definition: bool = False
    has_layer_structure: bool = False


class ValidationResult(BaseModel):
    """Result of fail-fast validation."""
    is_valid: bool
    critical_errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: DrawingMetadata

    def should_abort(self) -> bool:
        """Check if critical errors require aborting."""
        return len(self.critical_errors) > 0
