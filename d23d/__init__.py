"""
2D23D - Scaffolding Generator for BIM Workflows

Converts 2D CAD drawings (DXF/IFC/DWG) to provisional 3D IFC models.
"""

__version__ = "0.1.0"

from d23d.parsers.dxf_parser import parse_dxf
from d23d.detection.grid_detector import detect_grids
from d23d.generation.ifc_generator import generate_ifc
from d23d.generation.column_generator import generate_columns
from d23d.generation.slab_generator import generate_slabs
from d23d.classification.wall_classifier import classify_walls
from d23d.parsers.polyline_extractor import extract_polylines
from d23d.spatial.spatial_index import SpatialIndex

__all__ = [
    "parse_dxf",
    "detect_grids",
    "generate_ifc",
    "generate_columns",
    "generate_slabs",
    "classify_walls",
    "extract_polylines",
    "SpatialIndex",
]
