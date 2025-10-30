"""
DXF file parser using ezdxf library.

Extracts geometric entities and metadata from DXF files with fail-fast validation.
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
from loguru import logger

try:
    import ezdxf
    from ezdxf.document import Drawing
    from ezdxf.entities import Line, LWPolyline, Polyline, Circle, Arc, Text
except ImportError:
    raise ImportError(
        "ezdxf is required for DXF parsing. Install with: pip install ezdxf"
    )

from d23d.core.models import (
    DrawingMetadata,
    ValidationResult,
    Line2D,
    Point2D,
    BoundingBox,
)


class DXFParser:
    """Parser for DXF files with fail-fast validation."""

    def __init__(self, file_path: str):
        """
        Initialize DXF parser.

        Args:
            file_path: Path to DXF file
        """
        self.file_path = Path(file_path)
        self.doc: Optional[Drawing] = None
        self.metadata: Optional[DrawingMetadata] = None

    def parse(self) -> ValidationResult:
        """
        Parse DXF file with fail-fast validation.

        Returns:
            ValidationResult indicating if file is suitable for processing

        Raises:
            FileNotFoundError: If DXF file doesn't exist
            ezdxf.DXFError: If file is not valid DXF
        """
        logger.info(f"Parsing DXF file: {self.file_path}")

        if not self.file_path.exists():
            raise FileNotFoundError(f"DXF file not found: {self.file_path}")

        # Load DXF document
        try:
            self.doc = ezdxf.readfile(str(self.file_path))
        except ezdxf.DXFError as e:
            logger.error(f"Failed to parse DXF file: {e}")
            raise

        # Extract metadata
        self.metadata = self._extract_metadata()

        # Run fail-fast validation
        validation = self._validate()

        if validation.should_abort():
            logger.error(
                f"DXF validation failed with {len(validation.critical_errors)} "
                f"critical errors"
            )
            for error in validation.critical_errors:
                logger.error(f"  - {error}")
        else:
            logger.success(
                f"DXF validation passed (with {len(validation.warnings)} warnings)"
            )

        return validation

    def _extract_metadata(self) -> DrawingMetadata:
        """Extract metadata from DXF document."""
        assert self.doc is not None

        # Get layer names
        layers = [layer.dxf.name for layer in self.doc.layers]

        # Get drawing units from header variables
        units = self._get_units()

        # Calculate bounding box
        bbox = self._calculate_bounding_box()

        # Detect scale definition
        has_scale = self._has_scale_definition()

        metadata = DrawingMetadata(
            file_path=str(self.file_path),
            file_format="DXF",
            units=units,
            layers=layers,
            bounding_box=bbox,
            has_scale_definition=has_scale,
            has_layer_structure=len(layers) > 1,
        )

        logger.debug(f"Extracted metadata: {len(layers)} layers, units={units}")
        return metadata

    def _get_units(self) -> str:
        """Get drawing units from DXF header."""
        assert self.doc is not None

        # $INSUNITS header variable indicates drawing units
        # 1=inches, 2=feet, 4=mm, 5=cm, 6=m, 14=decimeters
        insunits = self.doc.header.get("$INSUNITS", 4)  # default to mm

        units_map = {
            1: "inches",
            2: "feet",
            4: "mm",
            5: "cm",
            6: "m",
            14: "dm",
        }

        return units_map.get(insunits, "mm")

    def _has_scale_definition(self) -> bool:
        """Check if drawing has scale definition."""
        assert self.doc is not None

        # Check for common scale text patterns in TEXT entities
        msp = self.doc.modelspace()
        for entity in msp.query("TEXT"):
            text = entity.dxf.text.lower()
            if "scale" in text or "1:" in text:
                return True

        return False

    def _calculate_bounding_box(self) -> BoundingBox:
        """Calculate bounding box of all entities."""
        assert self.doc is not None

        msp = self.doc.modelspace()

        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')

        for entity in msp:
            try:
                if hasattr(entity.dxf, 'start'):
                    min_x = min(min_x, entity.dxf.start.x)
                    min_y = min(min_y, entity.dxf.start.y)
                    max_x = max(max_x, entity.dxf.start.x)
                    max_y = max(max_y, entity.dxf.start.y)
                if hasattr(entity.dxf, 'end'):
                    min_x = min(min_x, entity.dxf.end.x)
                    min_y = min(min_y, entity.dxf.end.y)
                    max_x = max(max_x, entity.dxf.end.x)
                    max_y = max(max_y, entity.dxf.end.y)
                if hasattr(entity.dxf, 'insert'):
                    min_x = min(min_x, entity.dxf.insert.x)
                    min_y = min(min_y, entity.dxf.insert.y)
                    max_x = max(max_x, entity.dxf.insert.x)
                    max_y = max(max_y, entity.dxf.insert.y)
            except AttributeError:
                continue

        if min_x == float('inf'):
            # Empty drawing
            return BoundingBox(min_x=0, min_y=0, max_x=0, max_y=0)

        return BoundingBox(
            min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y
        )

    def _validate(self) -> ValidationResult:
        """
        Run fail-fast validation checks.

        Critical checks (will abort):
        - Building grid must be detectable
        - Drawing scale must be defined

        Warnings (proceed with low confidence):
        - Layer names not following standard conventions
        - Few entities in drawing
        """
        assert self.doc is not None
        assert self.metadata is not None

        critical_errors = []
        warnings = []

        # Check for empty drawing
        msp = self.doc.modelspace()
        entity_count = len(list(msp))
        if entity_count == 0:
            critical_errors.append("Drawing contains no entities")

        # Check for layer structure
        if len(self.metadata.layers) <= 1:
            warnings.append(
                "Drawing has minimal layer structure - classification may be less reliable"
            )

        # Check for scale definition
        # NOTE: Temporarily downgraded to warning for direct RVT exports
        if not self.metadata.has_scale_definition:
            warnings.append(
                "Drawing scale not detected - assuming 1:1 real-world dimensions. "
                "Add scale notation (e.g., 'SCALE 1:100') to drawing for better accuracy."
            )

        # Check drawing size (too small or too large is suspicious)
        if self.metadata.bounding_box:
            area = self.metadata.bounding_box.area()
            if area < 1000:  # Less than 1m² in mm units
                warnings.append("Drawing bounding box is very small - check units")
            elif area > 1e9:  # More than 1000m x 1000m
                warnings.append("Drawing bounding box is very large - check units")

        # Grid detection check (will be updated after actual grid detection)
        # For now, just check if there are LINE entities
        line_count = len(msp.query("LINE"))
        if line_count < 2:
            critical_errors.append(
                "Insufficient LINE entities to detect building grid (found {line_count})"
            )

        # Update metadata with grid detection status (preliminary)
        self.metadata.has_detectable_grid = line_count >= 2 and len(critical_errors) == 0

        return ValidationResult(
            is_valid=len(critical_errors) == 0,
            critical_errors=critical_errors,
            warnings=warnings,
            metadata=self.metadata,
        )

    def extract_lines(self, layers: Optional[List[str]] = None) -> List[Line2D]:
        """
        Extract LINE entities from specified layers.

        Args:
            layers: List of layer names to extract from (None = all layers)

        Returns:
            List of Line2D objects
        """
        assert self.doc is not None

        msp = self.doc.modelspace()
        lines = []

        for entity in msp.query("LINE"):
            if layers and entity.dxf.layer not in layers:
                continue

            line = Line2D(
                start=Point2D(x=entity.dxf.start.x, y=entity.dxf.start.y),
                end=Point2D(x=entity.dxf.end.x, y=entity.dxf.end.y),
                layer=entity.dxf.layer,
            )
            lines.append(line)

        logger.debug(f"Extracted {len(lines)} lines from {len(layers) if layers else 'all'} layers")
        return lines

    def get_layer_names(self) -> List[str]:
        """Get all layer names in the drawing."""
        assert self.metadata is not None
        return self.metadata.layers

    def get_lines_by_layer(self) -> Dict[str, List[Line2D]]:
        """Get lines grouped by layer name."""
        lines_by_layer: Dict[str, List[Line2D]] = {}

        for layer in self.get_layer_names():
            lines_by_layer[layer] = self.extract_lines([layer])

        return lines_by_layer


def parse_dxf(file_path: str, config: Optional[str] = None, require_scale: bool = True) -> DXFParser:
    """
    Parse DXF file with fail-fast validation.

    Args:
        file_path: Path to DXF file
        config: Path to configuration JSON (not yet implemented)
        require_scale: If True, require scale notation in drawing. Set False for Revit exports.

    Returns:
        DXFParser instance with parsed document

    Raises:
        FileNotFoundError: If file doesn't exist
        ezdxf.DXFError: If file is not valid DXF
        ValueError: If validation fails (critical errors)
    """
    parser = DXFParser(file_path)
    validation = parser.parse()

    # Filter out scale errors if not required (e.g., for Revit exports with correct units)
    if not require_scale:
        filtered_errors = [
            err for err in validation.critical_errors
            if "scale" not in err.lower()
        ]
        # Create new validation result with filtered errors
        from d23d.core.models import ValidationResult
        validation = ValidationResult(
            is_valid=len(filtered_errors) == 0,
            critical_errors=filtered_errors,
            warnings=validation.warnings,
            metadata=validation.metadata
        )

    if validation.should_abort():
        error_msg = "\\n".join(validation.critical_errors)
        raise ValueError(
            f"DXF validation failed with critical errors:\\n{error_msg}\\n\\n"
            f"Unable to proceed. Please fix the drawing and try again."
        )

    return parser
