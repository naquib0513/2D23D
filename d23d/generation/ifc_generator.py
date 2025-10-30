"""
IFC4 generator using IfcOpenShell.

Generates provisional IFC models with confidence metadata from detected elements.
"""

from typing import List, Optional
from pathlib import Path
from datetime import datetime
from loguru import logger

try:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.util.unit
except ImportError:
    raise ImportError(
        "IfcOpenShell is required for IFC generation. "
        "Install with: pip install ifcopenshell"
    )

from d23d.core.models import (
    BuildingGrid,
    GridLine,
    GridIntersection,
    Wall,
    Column,
    Slab,
    Point2D,
    Point3D,
)


class IFCGenerator:
    """
    Generates IFC4 models with provisional metadata.

    All elements are marked as "2D23D_PROVISIONAL" with confidence scores
    in custom property sets.
    """

    def __init__(self, project_name: str = "2D23D Generated Model"):
        """
        Initialize IFC generator.

        Args:
            project_name: Name of the IFC project
        """
        self.project_name = project_name
        self.ifc_file: Optional[ifcopenshell.file] = None
        self.project = None
        self.site = None
        self.building = None
        self.storey = None
        self.owner_history = None

    def create_project(self) -> ifcopenshell.file:
        """
        Create new IFC4 project structure.

        Returns:
            IfcOpenShell file object
        """
        logger.info(f"Creating IFC4 project: {self.project_name}")

        # Create IFC4 file
        self.ifc_file = ifcopenshell.api.run("project.create_file", version="IFC4")

        # Create project
        self.project = ifcopenshell.api.run(
            "root.create_entity",
            self.ifc_file,
            ifc_class="IfcProject",
            name=self.project_name,
        )

        # Set units (meters) - IFC standard
        # Note: Input coordinates are in mm, we'll convert during placement
        ifcopenshell.api.run(
            "unit.assign_unit",
            self.ifc_file,
            length={"is_metric": True, "raw": "METERS"},
        )

        # Create geometric representation context (required for geometry)
        ifcopenshell.api.run(
            "context.add_context",
            self.ifc_file,
            context_type="Model",
        )

        # Create spatial hierarchy
        self.site = ifcopenshell.api.run(
            "root.create_entity",
            self.ifc_file,
            ifc_class="IfcSite",
            name="Site",
        )

        self.building = ifcopenshell.api.run(
            "root.create_entity",
            self.ifc_file,
            ifc_class="IfcBuilding",
            name="Building",
        )

        self.storey = ifcopenshell.api.run(
            "root.create_entity",
            self.ifc_file,
            ifc_class="IfcBuildingStorey",
            name="Ground Floor",
        )

        # Assign spatial relationships
        ifcopenshell.api.run(
            "aggregate.assign_object",
            self.ifc_file,
            relating_object=self.project,
            products=[self.site],
        )

        ifcopenshell.api.run(
            "aggregate.assign_object",
            self.ifc_file,
            relating_object=self.site,
            products=[self.building],
        )

        ifcopenshell.api.run(
            "aggregate.assign_object",
            self.ifc_file,
            relating_object=self.building,
            products=[self.storey],
        )

        logger.success("Created IFC project structure")

        return self.ifc_file

    def add_grid(self, grid: BuildingGrid) -> None:
        """
        Add building grid to IFC model.

        Args:
            grid: Detected building grid
        """
        logger.info("Adding grid to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        # Create grid axes
        for h_line in grid.horizontal_lines:
            self._create_grid_axis(h_line, is_horizontal=True)

        for v_line in grid.vertical_lines:
            self._create_grid_axis(v_line, is_horizontal=False)

        logger.success(
            f"Added grid: {len(grid.horizontal_lines)}x{len(grid.vertical_lines)}"
        )

    def _create_grid_axis(self, grid_line: GridLine, is_horizontal: bool) -> None:
        """Create IfcGridAxis for grid line."""
        # TODO: Implement proper IfcGrid creation with axes
        # For now, create as IfcAnnotation for visualization
        pass

    def add_columns(self, columns: List[Column]) -> None:
        """
        Add columns to IFC model.

        Args:
            columns: List of Column objects
        """
        logger.info(f"Adding {len(columns)} columns to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        for column in columns:
            # Create IfcColumn
            ifc_column = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcColumn",
                name=f"Column {column.grid_reference or column.guid[:8]}",
            )

            # Set placement (convert mm to meters)
            # Get floor elevation from metadata, default to 0
            floor_elevation_mm = column.metadata.get("floor_elevation_mm", 0.0)
            matrix = [
                [1.0, 0.0, 0.0, column.location.x / 1000.0],
                [0.0, 1.0, 0.0, column.location.y / 1000.0],
                [0.0, 0.0, 1.0, floor_elevation_mm / 1000.0],  # Use floor elevation
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_column,
                matrix=matrix,
            )

            # Create rectangular profile and extrusion geometry
            self._add_column_geometry(ifc_column, column)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_column],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_column, column)

        logger.success(f"Added {len(columns)} columns")

    def add_walls(self, walls: List[Wall]) -> None:
        """
        Add walls to IFC model.

        Args:
            walls: List of Wall objects
        """
        logger.info(f"Adding {len(walls)} walls to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        for wall in walls:
            # Create IfcWall
            ifc_wall = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcWall",
                name=f"Wall {wall.guid[:8]}",
            )

            # Get floor elevation from metadata if available
            floor_elevation_mm = wall.metadata.get("floor_elevation_mm", 0.0)

            # Calculate wall direction angle (in radians) from centerline
            import math
            dx = wall.centerline.end.x - wall.centerline.start.x
            dy = wall.centerline.end.y - wall.centerline.start.y
            angle = math.atan2(dy, dx)  # Angle from X-axis

            # Create rotation matrix around Z-axis (vertical)
            # This orients the wall so it extrudes along its centerline direction
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)

            # 4x3 transformation matrix: [rotation | translation]
            # Rotation part aligns local X-axis with wall direction
            matrix = [
                [cos_a, -sin_a, 0.0, wall.centerline.start.x / 1000.0],
                [sin_a,  cos_a, 0.0, wall.centerline.start.y / 1000.0],
                [0.0,    0.0,   1.0, floor_elevation_mm / 1000.0],
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_wall,
                matrix=matrix,
            )

            # Create wall geometry
            self._add_wall_geometry(ifc_wall, wall)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_wall],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_wall, wall)

        logger.success(f"Added {len(walls)} walls")

    def add_slabs(self, slabs: List[Slab]) -> None:
        """
        Add slabs to IFC model.

        Args:
            slabs: List of Slab objects
        """
        logger.info(f"Adding {len(slabs)} slabs to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        for slab in slabs:
            # Create IfcSlab
            ifc_slab = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcSlab",
                name=f"Slab {slab.guid[:8]}",
            )

            # Set placement (convert mm to meters)
            matrix = [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, slab.elevation / 1000.0],
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_slab,
                matrix=matrix,
            )

            # Create slab geometry
            self._add_slab_geometry(ifc_slab, slab)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_slab],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_slab, slab)

        logger.success(f"Added {len(slabs)} slabs")

    def _add_provisional_pset(self, ifc_element, element) -> None:
        """
        Add provisional metadata property set to IFC element.

        Args:
            ifc_element: IFC entity
            element: ProvisionalElement with confidence score
        """
        # Create custom property set for provisional metadata
        pset = ifcopenshell.api.run(
            "pset.add_pset",
            self.ifc_file,
            product=ifc_element,
            name="2D23D_Provisional",
        )

        # Add properties
        ifcopenshell.api.run(
            "pset.edit_pset",
            self.ifc_file,
            pset=pset,
            properties={
                "IsProvisional": True,
                "ConfidenceScore": element.confidence,
                "ConfidenceLevel": element.confidence_level().value,
                "GeneratedBy": "2D23D",
                "GeneratedAt": datetime.now().isoformat(),
                "SourceGUID": element.guid,
                "SourceLayer": element.source_layer or "Unknown",
                "RequiresReview": element.confidence < 0.8,
            },
        )

    def _add_column_geometry(self, ifc_column, column: Column) -> None:
        """
        Add 3D geometry to column (rectangular extrusion).

        Args:
            ifc_column: IFC column entity
            column: Column data model
        """
        # Create profile using API then edit dimensions (convert mm to meters)
        profile = ifcopenshell.api.run(
            "profile.add_parameterized_profile",
            self.ifc_file,
            ifc_class="IfcRectangleProfileDef",
        )
        # Set profile dimensions in meters
        profile.XDim = column.width / 1000.0
        profile.YDim = column.depth / 1000.0

        # Use IfcOpenShell API to create geometry
        ifcopenshell.api.run(
            "geometry.assign_representation",
            self.ifc_file,
            product=ifc_column,
            representation=ifcopenshell.api.run(
                "geometry.add_profile_representation",
                self.ifc_file,
                context=self.ifc_file.by_type("IfcGeometricRepresentationContext")[0],
                profile=profile,
                depth=column.height / 1000.0,  # Convert mm to meters
            ),
        )

    def _add_wall_geometry(self, ifc_wall, wall: Wall) -> None:
        """
        Add 3D geometry to wall (rectangular extrusion along centerline).

        Args:
            ifc_wall: IFC wall entity
            wall: Wall data model
        """
        # V9: Simplify - use standard profile in XY, extrude along Z like columns do
        # Let the wall-level placement matrix handle ALL rotation
        # Profile XDim=thickness, YDim=length, extrude Z-direction for height

        # Calculate dimensions in meters
        thickness_m = wall.thickness / 1000.0
        height_m = wall.height / 1000.0
        length_m = wall.centerline.length() / 1000.0

        # Create simple rectangular profile centered at origin
        # No placement - let it default to XY plane
        profile = self.ifc_file.createIfcRectangleProfileDef(
            "AREA",
            None,
            None,  # No placement - default to XY plane centered at origin
            length_m,   # XDim - wall length along local X
            thickness_m  # YDim - wall thickness along local Y
        )

        # Extrude along Z for height (standard approach)
        extrusion_direction = self.ifc_file.createIfcDirection((0.0, 0.0, 1.0))

        # Simple default placement - profile at origin, extrude up
        # Wall sits ON the centerline (not centered) - centerline is the inner edge
        placement = self.ifc_file.createIfcAxis2Placement3D(
            self.ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            self.ifc_file.createIfcDirection((0.0, 0.0, 1.0)),  # Z-axis up
            self.ifc_file.createIfcDirection((1.0, 0.0, 0.0))   # X-axis forward
        )

        # Create extruded area solid
        extruded_solid = self.ifc_file.createIfcExtrudedAreaSolid(
            profile,
            placement,
            extrusion_direction,
            height_m,  # Extrude height
        )

        # Get geometric representation context
        context = self.ifc_file.by_type("IfcGeometricRepresentationContext")[0]

        # Create shape representation
        representation = self.ifc_file.createIfcShapeRepresentation(
            context,
            "Body",
            "SweptSolid",
            [extruded_solid],
        )

        # Assign representation to wall
        ifcopenshell.api.run(
            "geometry.assign_representation",
            self.ifc_file,
            product=ifc_wall,
            representation=representation,
        )

    def _add_slab_geometry(self, ifc_slab, slab: Slab) -> None:
        """
        Add 3D geometry to slab (extruded polygon).

        Args:
            ifc_slab: IFC slab entity
            slab: Slab data model
        """
        # Create arbitrary profile from boundary points (convert mm to meters)
        points = [(p.x / 1000.0, p.y / 1000.0) for p in slab.boundary]

        profile = ifcopenshell.api.run(
            "profile.add_arbitrary_profile",
            self.ifc_file,
            profile=points,
            name="Slab Profile",
        )

        # Add extruded representation
        ifcopenshell.api.run(
            "geometry.assign_representation",
            self.ifc_file,
            product=ifc_slab,
            representation=ifcopenshell.api.run(
                "geometry.add_profile_representation",
                self.ifc_file,
                context=self.ifc_file.by_type("IfcGeometricRepresentationContext")[0],
                profile=profile,
                depth=slab.thickness / 1000.0,  # Convert mm to meters
            ),
        )

    def write(self, output_path: str) -> None:
        """
        Write IFC file to disk.

        Args:
            output_path: Path to output .ifc file
        """
        if not self.ifc_file:
            raise RuntimeError("No IFC file to write. Create project first.")

        output_file = Path(output_path)
        self.ifc_file.write(str(output_file))

        file_size_kb = output_file.stat().st_size / 1024

        logger.success(
            f"Wrote IFC file: {output_file.absolute()} ({file_size_kb:.1f} KB)"
        )


def generate_ifc(
    grid: BuildingGrid,
    columns: Optional[List[Column]] = None,
    walls: Optional[List[Wall]] = None,
    slabs: Optional[List[Slab]] = None,
    output_path: str = "output.ifc",
    project_name: str = "2D23D Generated Model",
) -> ifcopenshell.file:
    """
    Convenience function to generate IFC model from detected elements.

    Args:
        grid: Building grid (required)
        columns: Optional list of columns
        walls: Optional list of walls
        slabs: Optional list of slabs
        output_path: Path to output .ifc file
        project_name: Name of IFC project

    Returns:
        IfcOpenShell file object
    """
    generator = IFCGenerator(project_name=project_name)

    # Create project structure
    ifc_file = generator.create_project()

    # Add elements
    generator.add_grid(grid)

    if columns:
        generator.add_columns(columns)

    if walls:
        generator.add_walls(walls)

    if slabs:
        generator.add_slabs(slabs)

    # Write to file
    generator.write(output_path)

    return ifc_file
