#!/usr/bin/env python
"""
Generate IFC model from DXF floor plan.

Usage:
    python generate_floor.py input.dxf output.ifc
    python generate_floor.py input.dxf  # outputs to input.ifc

Example:
    python generate_floor.py test_exports/arastanahclean.dxf test_exports/Aras_Tanah.ifc
"""

import sys
from pathlib import Path
from d23d.parsers.dxf_parser import parse_dxf
from d23d.detection.grid_detector import detect_grids
from d23d.detection.wall_detector import detect_walls
from d23d.generation.column_generator import generate_columns
from d23d.generation.slab_generator import generate_slabs
from d23d.generation.ifc_generator import IFCGenerator


def main():
    # Parse command line arguments
    if len(sys.argv) < 2:
        print("Usage: python generate_floor.py input.dxf [output.ifc]")
        print()
        print("Examples:")
        print("  python generate_floor.py test_exports/arastanahclean.dxf")
        print("  python generate_floor.py test_exports/arastanahclean.dxf test_exports/output.ifc")
        sys.exit(1)

    dxf_file = sys.argv[1]

    # Determine output file
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        # Default: replace .dxf with .ifc
        output_file = str(Path(dxf_file).with_suffix('.ifc'))

    # Verify input file exists
    if not Path(dxf_file).exists():
        print(f"Error: Input file not found: {dxf_file}")
        sys.exit(1)

    print("=" * 60)
    print("2D23D - DXF to IFC Generator")
    print("=" * 60)
    print(f"Input:  {dxf_file}")
    print(f"Output: {output_file}")
    print()

    try:
        # Step 1: Parse DXF
        print("[1/6] Parsing DXF file...")
        parser = parse_dxf(dxf_file, require_scale=False)
        print(f"      [OK] Parsed {len(parser.get_layer_names())} layers")
        print(f"      [OK] Units: {parser.metadata.units}")
        print()

        # Step 2: Detect grid
        print("[2/6] Detecting building grid...")
        grid = detect_grids(parser)
        print(f"      [OK] Grid: {len(grid.horizontal_lines)}x{len(grid.vertical_lines)} lines")
        print(f"      [OK] Intersections: {len(grid.intersections)}")
        print(f"      [OK] Confidence: {grid.confidence:.2f}")
        print()

        # Step 3: Detect walls
        print("[3/6] Detecting walls...")
        walls = detect_walls(parser)
        print(f"      [OK] Walls: {len(walls)}")
        if walls:
            print(f"      [OK] Thickness: {walls[0].thickness}mm")
            print(f"      [OK] Height: {walls[0].height}mm")
        print()

        # Step 4: Generate columns at grid intersections
        print("[4/6] Generating columns...")
        columns = generate_columns(grid=grid)
        print(f"      [OK] Columns: {len(columns)}")
        if columns:
            print(f"      [OK] Size: {columns[0].width}mm × {columns[0].depth}mm")
            print(f"      [OK] Height: {columns[0].height}mm")
        print()

        # Step 5: Generate foundation slab
        print("[5/6] Generating slabs...")
        slabs = generate_slabs(grid=grid)
        print(f"      [OK] Slabs: {len(slabs)}")
        if slabs:
            print(f"      [OK] Thickness: {slabs[0].thickness}mm")
            # Calculate area from boundary points
            if hasattr(slabs[0], 'boundary') and len(slabs[0].boundary) >= 3:
                # Simple area calculation from bounding box
                xs = [p.x for p in slabs[0].boundary]
                ys = [p.y for p in slabs[0].boundary]
                area_mm2 = (max(xs) - min(xs)) * (max(ys) - min(ys))
                area_m2 = area_mm2 / 1_000_000
                print(f"      [OK] Area: {area_m2:.1f} m²")
        print()

        # Step 6: Generate IFC
        print("[6/6] Generating IFC file...")
        project_name = Path(dxf_file).stem.replace('-', ' ').title()
        gen = IFCGenerator(project_name=project_name)
        gen.create_project()
        gen.add_grid(grid)
        gen.add_walls(walls)
        gen.add_columns(columns)
        gen.add_slabs(slabs)
        gen.write(output_file)
        print(f"      [OK] Wrote IFC file")
        print()

        # Summary
        print("=" * 60)
        print("SUCCESS!")
        print("=" * 60)
        print(f"Generated: {output_file}")
        print()
        print("Summary:")
        print(f"  - {len(walls)} walls")
        print(f"  - {len(columns)} columns")
        print(f"  - {len(slabs)} slab(s)")
        print(f"  - {len(grid.horizontal_lines)}x{len(grid.vertical_lines)} grid")
        print()
        print("Next steps:")
        print(f'  1. Open Blender/Bonsai')
        print(f'  2. File -> Open IFC Project -> {output_file}')
        print(f'  3. Review provisional geometry and adjust as needed')
        print()

        file_size = Path(output_file).stat().st_size / 1024
        print(f"File size: {file_size:.1f} KB")

    except Exception as e:
        print()
        print("=" * 60)
        print("ERROR!")
        print("=" * 60)
        print(f"Failed to process DXF file: {e}")
        print()
        print("Common issues:")
        print("  - Grid not detected -> Check grid lines are horizontal/vertical")
        print("  - Wrong layer names -> Check DXF layers match config")
        print("  - File not found -> Check file path is correct")
        print()
        print("Run with Python to see full error traceback:")
        print(f"  python -c \"import traceback; exec(open('{__file__}').read())\"")
        sys.exit(1)


if __name__ == "__main__":
    main()
