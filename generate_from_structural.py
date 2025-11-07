#!/usr/bin/env python3
"""
Generate IFC from single structural DXF file
"""

import sys
from pathlib import Path
import time
import json

from d23d.parsers.dxf_parser import parse_dxf
from d23d.detection.grid_detector import detect_grids
from d23d.detection.wall_detector import detect_walls
from d23d.detection.column_detector import detect_columns
from d23d.detection.slab_detector import detect_slabs
from d23d.detection.beam_detector import detect_beams
from d23d.detection.foundation_detector import detect_foundations
from d23d.detection.spatial_reference import create_spatial_reference
from d23d.generation.ifc_generator import IFCGenerator


# Load structural standards
def load_structural_standards():
    """Load structural element sizing standards from config"""
    config_path = Path("config/structural_standards.json")
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    return None


def get_storey_heights_from_reference():
    """
    Load reference IFC and extract floor-to-floor heights.

    Returns:
        Dict mapping floor names to heights in mm, or None if reference not available
    """
    ref_path = Path("test_exports/source_files/Terminal_1_IFC4/SJTII-STR-S-TER1-00-R0-Clean.ifc")

    if not ref_path.exists():
        return None

    try:
        import ifcopenshell
        ifc = ifcopenshell.open(str(ref_path))
        storeys = sorted(ifc.by_type('IfcBuildingStorey'), key=lambda s: s.Elevation or 0)

        heights = {}
        for i, storey in enumerate(storeys[:-1]):  # Exclude top storey (no next floor)
            next_storey = storeys[i + 1]
            # Elevations are in mm in the reference IFC
            floor_to_floor = next_storey.Elevation - storey.Elevation
            heights[storey.Name] = floor_to_floor

        return heights
    except Exception as e:
        print(f"  Warning: Could not load reference IFC: {e}")
        return None


STRUCTURAL_STANDARDS = load_structural_standards()


def generate_from_structural(dxf_path: Path, output_path: Path, floor_name: str):
    """Generate IFC from single structural DXF"""

    print(f"\n{'='*80}")
    print(f"Processing Structural DXF: {floor_name}")
    print(f"{'='*80}")
    print(f"Input: {dxf_path.name}")
    print(f"Output: {output_path}")

    if not dxf_path.exists():
        print(f"[ERROR] DXF file not found!")
        return 1

    start_time = time.time()

    # Parse DXF
    print(f"\n[1/5] Parsing DXF...")
    parser = parse_dxf(str(dxf_path), require_scale=False)
    print(f"  Parsed DXF successfully")

    # Detect grid
    print(f"[2/5] Detecting grid...")
    grid = detect_grids(parser, enable_fallback=False)

    if grid is not None:
        print(f"  Grid detected: {len(grid.horizontal_lines)}x{len(grid.vertical_lines)}")
        spatial_ref = create_spatial_reference(grid=grid)
    else:
        print(f"  No grid detected, will use mock grid")
        spatial_ref = None

    # Detect columns
    print(f"[3/5] Detecting columns...")

    # Determine column height from reference IFC or config
    # TESTING: User wants to see 8m version to compare visually
    storey_heights = get_storey_heights_from_reference()
    if storey_heights and "GROUND FLOOR LEVEL" in storey_heights:
        ref_height = storey_heights["GROUND FLOOR LEVEL"]
        # TEMPORARILY USING REFERENCE HEIGHT FOR VISUAL COMPARISON
        column_height = ref_height
        print(f"  Using reference IFC height for comparison: {column_height:.0f}mm = {column_height/1000:.1f}m")
    else:
        # Fallback to config default
        column_height = 5500.0  # mm
        print(f"  Using config height: {column_height:.0f}mm (reference IFC not available)")

    # Detect columns - pass floor_to_floor_height so detector can set heights properly
    columns = detect_columns(
        parser,
        grid_system=grid,
        spatial_ref=spatial_ref,
        floor_to_floor_height_mm=column_height
    )

    print(f"  Detected {len(columns)} columns (height: {column_height:.0f}mm)")

    # Detect slabs (from structural drawing)
    print(f"[4/5] Detecting slabs...")
    slabs = detect_slabs(parser, grid=grid, spatial_ref=spatial_ref)

    # Set structural slab properties
    structural_thickness = 200.0  # mm (structural concrete slab)
    for slab in slabs:
        slab.thickness = structural_thickness
        slab.slab_type = "STRUCTURAL"

    print(f"  Detected {len(slabs)} structural slabs ({structural_thickness}mm thick)")

    # Detect beams with standard sizing
    print(f"[5/6] Detecting beams...")

    # Use structural standards for beam sizing
    if STRUCTURAL_STANDARDS:
        beam_defaults = STRUCTURAL_STANDARDS["beams"]["default_size"]
        beam_width = beam_defaults["width_mm"]
        beam_depth = beam_defaults["depth_mm"]
        print(f"  Using structural standards: {beam_width}mm × {beam_depth}mm beams")
    else:
        beam_width = 300.0
        beam_depth = 600.0
        print(f"  Using fallback sizing: {beam_width}mm × {beam_depth}mm beams")

    beams = detect_beams(
        parser,
        default_width=beam_width,
        default_depth=beam_depth,
        min_length=100.0  # Capture even small beams
    )

    # Apply span-based sizing from standards
    # For very short "beams" (< 2m), use default commercial size as they're likely symbols
    if STRUCTURAL_STANDARDS and beams:
        standard_sizes = STRUCTURAL_STANDARDS["beams"]["standard_sizes"]
        default_beam = STRUCTURAL_STANDARDS["beams"]["default_size"]

        for beam in beams:
            beam_length = ((beam.end.x - beam.start.x)**2 + (beam.end.y - beam.start.y)**2)**0.5

            # Very short lines (<2m) are likely symbols/annotations, use default size
            if beam_length < 2000:
                beam.width = default_beam["width_mm"]
                beam.depth = default_beam["depth_mm"]
            else:
                # Find appropriate beam size based on actual span
                for size_spec in standard_sizes:
                    if size_spec["min_span_mm"] <= beam_length < size_spec["max_span_mm"]:
                        beam.width = size_spec["width_mm"]
                        beam.depth = size_spec["depth_mm"]
                        break

    print(f"  Detected {len(beams)} beams")

    # Detect walls (structural might have shear walls)
    print(f"[6/7] Detecting walls...")
    walls = detect_walls(parser)
    print(f"  Detected {len(walls)} walls")

    # Detect foundations (pile caps)
    print(f"[7/7] Detecting foundations...")
    foundations = detect_foundations(parser, default_foundation_depth=10000.0)  # 10m deep pile caps
    print(f"  Detected {len(foundations)} foundations (pile caps)")

    # Generate IFC
    print(f"\nGenerating IFC...")
    generator = IFCGenerator(project_name=f"Terminal 1 - {floor_name}")
    generator.create_project()

    if grid is not None:
        generator.add_grid(grid)

    generator.add_columns(columns)
    generator.add_beams(beams)
    generator.add_slabs(slabs)
    generator.add_foundations(foundations)
    generator.add_walls(walls)

    generator.write(str(output_path))

    elapsed = time.time() - start_time
    file_size_mb = output_path.stat().st_size / 1_048_576

    print(f"\n{'='*80}")
    print(f"IFC GENERATION COMPLETE")
    print(f"{'='*80}")
    print(f"File: {output_path}")
    print(f"Size: {file_size_mb:.2f} MB")
    print(f"Time: {elapsed:.2f}s")
    print(f"Elements:")
    print(f"  - Walls: {len(walls)}")
    print(f"  - Columns: {len(columns)}")
    print(f"  - Beams: {len(beams)}")
    print(f"  - Slabs: {len(slabs)}")
    print(f"  - Foundations: {len(foundations)}")

    return 0


def main():
    """Main entry point"""

    if len(sys.argv) < 2:
        print("Usage: python generate_from_structural.py <floor_number>")
        print("Example: python generate_from_structural.py 01")
        print("\nAvailable floors:")
        print("  00 - Aras Asas (Foundation)")
        print("  01 - Ground Floor Level")
        print("  02 - First Floor Level")
        print("  03 - Second Floor Level")
        print("  04 - Third Floor Level")
        print("  05 - Fourth Floor Level (Observatory)")
        print("  06 - Roof Level")
        print("  07 - Beam Level (Observatory)")
        return 1

    floor_num = sys.argv[1]

    # Map floor numbers to files
    floor_mapping = {
        "00": ("SJTII-STR-Structural Plan - 00 Aras Asas.dxf", "Aras Asas"),
        "01": ("SJTII-STR-Structural Plan - 01 GROUND FLOOR LEVEL.dxf", "Ground Floor"),
        "02": ("SJTII-STR-Structural Plan - 02 FIRST FLOOR LEVEL.dxf", "First Floor"),
        "03": ("SJTII-STR-Structural Plan - 03 SECOND FLOOR LEVEL.dxf", "Second Floor"),
        "04": ("SJTII-STR-Structural Plan - 04 THIRD FLOOR LEVEL.dxf", "Third Floor"),
        "05": ("SJTII-STR-Structural Plan - 05 FOURTH FLOOR LEVEL (OBSERVATORY DECK).dxf", "Fourth Floor"),
        "06": ("SJTII-STR-Structural Plan - 06 ROOF LEVEL.dxf", "Roof Level"),
        "07": ("SJTII-STR-Structural Plan - 07 BEAM LEVEL (OBSERVATORY).dxf", "Beam Level"),
    }

    if floor_num not in floor_mapping:
        print(f"[ERROR] Unknown floor number: {floor_num}")
        print("Valid options: 00, 01, 02, 03, 04, 05, 06, 07")
        return 1

    dxf_filename, floor_name = floor_mapping[floor_num]
    dxf_path = Path(f"test_exports/STR_DXF/{dxf_filename}")
    output_path = Path(f"test_exports/STR_Floor_{floor_num}.ifc")

    return generate_from_structural(dxf_path, output_path, floor_name)


if __name__ == "__main__":
    sys.exit(main())
