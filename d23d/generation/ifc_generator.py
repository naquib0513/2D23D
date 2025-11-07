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
from d23d.detection.beam_detector import Beam
from d23d.detection.foundation_detector import Foundation


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

    def create_project(self, create_default_storey: bool = True) -> ifcopenshell.file:
        """
        Create new IFC4 project structure.

        Args:
            create_default_storey: If True, creates a default "Ground Floor" storey.
                                   Set to False for multi-storey projects where storeys
                                   will be added explicitly via add_storey().

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

        # Create default storey only if requested (for single-floor projects)
        if create_default_storey:
            self.storey = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcBuildingStorey",
                name="Ground Floor",
            )
        else:
            # For multi-storey projects, storey will be set via add_storey()
            self.storey = None

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

        # Assign default storey to building if created
        if create_default_storey and self.storey:
            ifcopenshell.api.run(
                "aggregate.assign_object",
                self.ifc_file,
                relating_object=self.building,
                products=[self.storey],
            )

        logger.success("Created IFC project structure")

        return self.ifc_file

    def add_storey(self, name: str, elevation_m: float):
        """
        Add a building storey at specified elevation.
        
        Args:
            name: Storey name (e.g., "Level 1")
            elevation_m: Elevation in meters
            
        Returns:
            The created IfcBuildingStorey
        """
        if not self.ifc_file or not self.building:
            raise RuntimeError("Project not created")
        
        storey = ifcopenshell.api.run(
            "root.create_entity",
            self.ifc_file,
            ifc_class="IfcBuildingStorey",
            name=name,
        )
        storey.Elevation = elevation_m
        
        # Assign to building
        ifcopenshell.api.run(
            "aggregate.assign_object",
            self.ifc_file,
            relating_object=self.building,
            products=[storey],
        )
        
        # Update self.storey for backwards compatibility
        self.storey = storey
        return storey

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

            # Set predefined type (COLUMN for structural columns)
            if hasattr(column, 'predefined_type') and column.predefined_type:
                ifc_column.PredefinedType = column.predefined_type

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

    def add_beams(self, beams: List[Beam]) -> None:
        """
        Add beams to IFC model.

        Args:
            beams: List of Beam objects
        """
        logger.info(f"Adding {len(beams)} beams to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        for beam in beams:
            # Create IfcBeam
            ifc_beam = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcBeam",
                name=f"Beam {beam.guid[:8]}",
            )

            # Set predefined type (BEAM for structural beams)
            if hasattr(beam, 'predefined_type') and beam.predefined_type:
                ifc_beam.PredefinedType = beam.predefined_type

            # Calculate beam midpoint and direction
            start_x, start_y = beam.start.x, beam.start.y
            end_x, end_y = beam.end.x, beam.end.y
            mid_x = (start_x + end_x) / 2.0
            mid_y = (start_y + end_y) / 2.0

            # Calculate beam length
            import math
            length_mm = math.sqrt((end_x - start_x)**2 + (end_y - start_y)**2)

            # Calculate rotation angle (beam orientation)
            angle = math.atan2(end_y - start_y, end_x - start_x)

            # Get floor elevation from metadata, default to 0
            floor_elevation_mm = beam.metadata.get("floor_elevation_mm", 0.0)

            # Get column height (floor-to-floor) - beams go at ceiling/column top
            # Beam elevation should be at ceiling, not ground
            # If beam.elevation is set, use it; otherwise use floor-to-floor height
            if beam.elevation > 0:
                beam_z_mm = floor_elevation_mm + beam.elevation
            else:
                # Default: place at typical ceiling height (use column height if available)
                # For ground floor: ~8000mm (8m) ceiling
                beam_z_mm = floor_elevation_mm + 8000.0  # Will be refined later with actual column height

            # Set placement at beam midpoint
            # Beam is placed at its center, then rotated
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)

            matrix = [
                [cos_a, -sin_a, 0.0, mid_x / 1000.0],
                [sin_a, cos_a, 0.0, mid_y / 1000.0],
                [0.0, 0.0, 1.0, beam_z_mm / 1000.0],  # Place at ceiling height
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_beam,
                matrix=matrix,
            )

            # Create beam geometry (rectangular profile extruded along length)
            self._add_beam_geometry(ifc_beam, beam, length_mm)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_beam],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_beam, beam)

        logger.success(f"Added {len(beams)} beams")

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

            # Set slab type based on slab_type attribute
            if hasattr(slab, 'slab_type'):
                if slab.slab_type == 'STRUCTURAL':
                    ifc_slab.PredefinedType = "FLOOR"
                    ifc_slab.Name = f"Structural Floor Slab"
                elif slab.slab_type == 'FINISH':
                    ifc_slab.PredefinedType = "FLOOR"
                    ifc_slab.Name = f"Floor Finish - {slab.material_name if hasattr(slab, 'material_name') else 'Unknown'}"

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

    def add_foundations(self, foundations: List[Foundation]) -> None:
        """
        Add foundation elements (pile caps) to IFC model as IfcSlab/BASESLAB.

        Args:
            foundations: List of Foundation objects
        """
        logger.info(f"Adding {len(foundations)} foundations to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first")

        for foundation in foundations:
            # Create IfcSlab with BASESLAB predefined type
            ifc_foundation = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcSlab",
                name=f"Foundation Pile Cap {foundation.guid[:8]}",
            )

            # CRITICAL: Set predefined type
            ifc_foundation.PredefinedType = "BASESLAB"

            # Set placement at foundation center (convert mm to meters)
            # Foundations are placed at ground level (storey elevation)
            matrix = [
                [1.0, 0.0, 0.0, foundation.center.x / 1000.0],
                [0.0, 1.0, 0.0, foundation.center.y / 1000.0],
                [0.0, 0.0, 1.0, 0.0],  # At storey level
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_foundation,
                matrix=matrix,
            )

            # Create foundation geometry (vertical extrusion DOWNWARD)
            self._add_foundation_geometry(ifc_foundation, foundation)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_foundation],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_foundation, foundation)

        logger.success(f"Added {len(foundations)} foundations")

    def add_doors(self, doors: List) -> None:
        """
        Add doors to IFC model.

        Args:
            doors: List of Door objects
        """
        from d23d.core.models import Door

        logger.info(f"Adding {len(doors)} doors to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        for door in doors:
            # Create IfcDoor
            ifc_door = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcDoor",
                name=f"Door {door.block_name or door.guid[:8]}",
            )

            # Get floor elevation from metadata
            floor_elevation_mm = door.metadata.get("floor_elevation_mm", 0.0)

            # Set placement (convert mm to meters)
            import math
            cos_a = math.cos(door.rotation)
            sin_a = math.sin(door.rotation)

            matrix = [
                [cos_a, -sin_a, 0.0, door.location.x / 1000.0],
                [sin_a,  cos_a, 0.0, door.location.y / 1000.0],
                [0.0,    0.0,   1.0, floor_elevation_mm / 1000.0],
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_door,
                matrix=matrix,
            )

            # Create simple door geometry (rectangle)
            self._add_door_geometry(ifc_door, door)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_door],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_door, door)

        logger.success(f"Added {len(doors)} doors")

    def add_windows(self, windows: List) -> None:
        """
        Add windows to IFC model.

        Args:
            windows: List of Window objects
        """
        from d23d.core.models import Window

        logger.info(f"Adding {len(windows)} windows to IFC model")

        if not self.ifc_file or not self.storey:
            raise RuntimeError("Project not created. Call create_project() first.")

        for window in windows:
            # Create IfcWindow
            ifc_window = ifcopenshell.api.run(
                "root.create_entity",
                self.ifc_file,
                ifc_class="IfcWindow",
                name=f"Window {window.block_name or window.guid[:8]}",
            )

            # Get floor elevation from metadata
            floor_elevation_mm = window.metadata.get("floor_elevation_mm", 0.0)

            # Set placement (convert mm to meters)
            # Windows are placed at sill height above floor
            import math
            cos_a = math.cos(window.rotation)
            sin_a = math.sin(window.rotation)

            window_z = (floor_elevation_mm + window.sill_height) / 1000.0

            matrix = [
                [cos_a, -sin_a, 0.0, window.location.x / 1000.0],
                [sin_a,  cos_a, 0.0, window.location.y / 1000.0],
                [0.0,    0.0,   1.0, window_z],
            ]

            ifcopenshell.api.run(
                "geometry.edit_object_placement",
                self.ifc_file,
                product=ifc_window,
                matrix=matrix,
            )

            # Create simple window geometry (rectangle)
            self._add_window_geometry(ifc_window, window)

            # Assign to storey
            ifcopenshell.api.run(
                "spatial.assign_container",
                self.ifc_file,
                relating_structure=self.storey,
                products=[ifc_window],
            )

            # Add provisional metadata
            self._add_provisional_pset(ifc_window, window)

        logger.success(f"Added {len(windows)} windows")

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

    def _add_beam_geometry(self, ifc_beam, beam: Beam, length_mm: float) -> None:
        """
        Add 3D geometry to beam (rectangular profile extruded along length).

        Beams are extruded horizontally along their length (X-axis), not vertically.
        The beam's cross-section (width x depth) is in the YZ plane, and it extends along X.

        Args:
            ifc_beam: IFC beam entity
            beam: Beam data model
            length_mm: Beam length in mm
        """
        # Convert dimensions to meters
        width_m = beam.width / 1000.0  # Beam width (horizontal, in Y direction)
        depth_m = beam.depth / 1000.0  # Beam depth (vertical, in Z direction)
        length_m = length_mm / 1000.0  # Beam length (along X direction)

        # Create rectangular profile for beam cross-section in YZ plane
        # Profile is centered at origin, facing along X-axis
        profile = self.ifc_file.createIfcRectangleProfileDef(
            "AREA",      # Profile type
            None,        # Profile name
            None,        # Position (will use placement below)
            width_m,     # XDim - beam width (horizontal, maps to Y in final orientation)
            depth_m      # YDim - beam depth (vertical, maps to Z in final orientation)
        )

        # Extrude along positive X-axis (horizontally along beam length)
        # This is different from columns/walls which extrude along Z (vertically)
        extrusion_direction = self.ifc_file.createIfcDirection((1.0, 0.0, 0.0))

        # Placement: profile in YZ plane at origin, extrude along +X
        # Z-axis points up (beam depth direction)
        # X-axis points along beam length (extrusion direction)
        placement = self.ifc_file.createIfcAxis2Placement3D(
            self.ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            self.ifc_file.createIfcDirection((1.0, 0.0, 0.0)),  # Z-axis of placement = X-axis (extrusion direction)
            self.ifc_file.createIfcDirection((0.0, 1.0, 0.0))   # X-axis of placement = Y-axis (width direction)
        )

        # Create extruded area solid
        extruded_solid = self.ifc_file.createIfcExtrudedAreaSolid(
            profile,
            placement,
            extrusion_direction,
            length_m,  # Extrude along beam length
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

        # Assign representation to beam
        ifcopenshell.api.run(
            "geometry.assign_representation",
            self.ifc_file,
            product=ifc_beam,
            representation=representation,
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
        # Profile points are in XY plane
        ifc_points = [
            self.ifc_file.createIfcCartesianPoint((p.x / 1000.0, p.y / 1000.0))
            for p in slab.boundary
        ]

        # Create closed polyline for slab boundary
        polyline = self.ifc_file.createIfcPolyline(ifc_points)

        # Create arbitrary profile with outer boundary
        profile = self.ifc_file.createIfcArbitraryClosedProfileDef(
            "AREA",  # Profile type
            None,    # Profile name
            polyline
        )

        # Extrude upward along Z-axis to create horizontal slab
        # The profile is in XY plane, we extrude it vertically by the thickness
        extrusion_direction = self.ifc_file.createIfcDirection((0.0, 0.0, 1.0))

        # Placement at origin with Z-axis up
        placement = self.ifc_file.createIfcAxis2Placement3D(
            self.ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            self.ifc_file.createIfcDirection((0.0, 0.0, 1.0)),  # Z-axis up
            self.ifc_file.createIfcDirection((1.0, 0.0, 0.0))   # X-axis forward
        )

        # Create extruded area solid - extrude the XY profile along Z by thickness
        thickness_m = slab.thickness / 1000.0
        extruded_solid = self.ifc_file.createIfcExtrudedAreaSolid(
            profile,
            placement,
            extrusion_direction,
            thickness_m,
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

        # Assign representation to slab
        ifcopenshell.api.run(
            "geometry.assign_representation",
            self.ifc_file,
            product=ifc_slab,
            representation=representation,
        )

    def _add_foundation_geometry(self, ifc_foundation, foundation: Foundation) -> None:
        """
        Add 3D geometry to foundation (pile cap) as vertical extrusion.

        Foundations are square/rectangular profiles extruded DOWNWARD.

        Args:
            ifc_foundation: IFC slab entity (with BASESLAB predefined type)
            foundation: Foundation data model
        """
        # Create rectangular profile for foundation (convert mm to meters)
        width_m = foundation.width / 1000.0
        depth_m = foundation.depth / 1000.0

        # Create rectangle profile (in XY plane at origin)
        profile = self.ifc_file.createIfcRectangleProfileDef(
            "AREA",  # Profile type
            None,    # Profile name
            None,    # Position (will use placement below)
            width_m,  # X dimension
            depth_m   # Y dimension
        )

        # Extrude DOWNWARD along negative Z-axis (into ground)
        extrusion_direction = self.ifc_file.createIfcDirection((0.0, 0.0, -1.0))

        # Placement at origin with Z-axis down
        placement = self.ifc_file.createIfcAxis2Placement3D(
            self.ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            self.ifc_file.createIfcDirection((0.0, 0.0, -1.0)),  # Z-axis down (extrusion direction)
            self.ifc_file.createIfcDirection((1.0, 0.0, 0.0))    # X-axis forward
        )

        # Create extruded area solid - extrude profile downward by foundation depth
        foundation_depth_m = foundation.foundation_depth / 1000.0
        extruded_solid = self.ifc_file.createIfcExtrudedAreaSolid(
            profile,
            placement,
            extrusion_direction,
            foundation_depth_m,  # Extrusion distance (downward)
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

        # Assign representation to foundation
        ifcopenshell.api.run(
            "geometry.assign_representation",
            self.ifc_file,
            product=ifc_foundation,
            representation=representation,
        )

    def _add_door_geometry(self, ifc_door, door) -> None:
        """
        Add 3D geometry to door (simple rectangle).
        Uses simple profile approach matching wall geometry pattern.

        Args:
            ifc_door: IFC door entity
            door: Door data model
        """
        from d23d.core.models import Door

        # Create rectangular profile for door (convert mm to meters)
        width_m = door.width / 1000.0
        height_m = door.height / 1000.0
        thickness_m = 0.05  # 50mm door thickness

        # Create rectangle profile in XY plane (like walls do)
        # XDim = width, YDim = thickness
        profile = self.ifc_file.createIfcRectangleProfileDef(
            "AREA",
            None,
            None,  # No placement - default to XY plane
            width_m,      # XDim - door width along X
            thickness_m   # YDim - door thickness along Y
        )

        # Extrude along Z for height (same as walls)
        extrusion_direction = self.ifc_file.createIfcDirection((0.0, 0.0, 1.0))

        # Placement at origin, Z-axis up, X-axis forward (same as walls)
        placement = self.ifc_file.createIfcAxis2Placement3D(
            self.ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            self.ifc_file.createIfcDirection((0.0, 0.0, 1.0)),  # Z-axis up
            self.ifc_file.createIfcDirection((1.0, 0.0, 0.0))   # X-axis forward
        )

        # Create extruded solid
        extruded_solid = self.ifc_file.createIfcExtrudedAreaSolid(
            profile,
            placement,
            extrusion_direction,
            height_m  # Extrude height
        )

        # Create shape representation
        context = self.ifc_file.by_type("IfcGeometricRepresentationContext")[0]
        shape_representation = self.ifc_file.createIfcShapeRepresentation(
            context,
            "Body",
            "SweptSolid",
            [extruded_solid]
        )

        # Assign representation
        product_shape = self.ifc_file.createIfcProductDefinitionShape(None, None, [shape_representation])
        ifc_door.Representation = product_shape

    def _add_window_geometry(self, ifc_window, window) -> None:
        """
        Add 3D geometry to window (simple rectangle).
        Uses simple profile approach matching wall geometry pattern.

        Args:
            ifc_window: IFC window entity
            window: Window data model
        """
        from d23d.core.models import Window

        # Create rectangular profile for window (convert mm to meters)
        width_m = window.width / 1000.0
        height_m = window.height / 1000.0
        thickness_m = 0.02  # 20mm window thickness

        # Create rectangle profile in XY plane (like walls do)
        # XDim = width, YDim = thickness
        profile = self.ifc_file.createIfcRectangleProfileDef(
            "AREA",
            None,
            None,  # No placement - default to XY plane
            width_m,      # XDim - window width along X
            thickness_m   # YDim - window thickness along Y
        )

        # Extrude along Z for height (same as walls)
        extrusion_direction = self.ifc_file.createIfcDirection((0.0, 0.0, 1.0))

        # Placement at origin, Z-axis up, X-axis forward (same as walls)
        placement = self.ifc_file.createIfcAxis2Placement3D(
            self.ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            self.ifc_file.createIfcDirection((0.0, 0.0, 1.0)),  # Z-axis up
            self.ifc_file.createIfcDirection((1.0, 0.0, 0.0))   # X-axis forward
        )

        # Create extruded solid
        extruded_solid = self.ifc_file.createIfcExtrudedAreaSolid(
            profile,
            placement,
            extrusion_direction,
            height_m  # Extrude height
        )

        # Create shape representation
        context = self.ifc_file.by_type("IfcGeometricRepresentationContext")[0]
        shape_representation = self.ifc_file.createIfcShapeRepresentation(
            context,
            "Body",
            "SweptSolid",
            [extruded_solid]
        )

        # Assign representation
        product_shape = self.ifc_file.createIfcProductDefinitionShape(None, None, [shape_representation])
        ifc_window.Representation = product_shape

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
