"""
Spatial index using SQLite with R-tree extension.

Stores building elements with spatial indexing for proximity queries
and clash detection.
"""

import sqlite3
from typing import List, Optional, Tuple, Any
from pathlib import Path
from loguru import logger

from d23d.core.models import (
    ProvisionalElement,
    BoundingBox,
    Point2D,
    GridLine,
    GridIntersection,
    Wall,
    Column,
    Slab,
)


class SpatialIndex:
    """
    Spatial index using SQLite R-tree for fast geometric queries.

    Schema:
    - elements: Stores element metadata (GUID, type, confidence, etc.)
    - element_geometry: R-tree virtual table for spatial indexing
    """

    def __init__(self, db_path: str = ":memory:"):
        """
        Initialize spatial index.

        Args:
            db_path: Path to SQLite database (":memory:" for in-memory)
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._initialize_database()

    def _initialize_database(self) -> None:
        """Create database schema with R-tree index."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Access columns by name

        cursor = self.conn.cursor()

        # Create elements table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS elements (
                guid TEXT PRIMARY KEY,
                element_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                source_layer TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Create R-tree spatial index
        # R-tree stores: (id, min_x, max_x, min_y, max_y, min_z, max_z)
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS element_geometry
            USING rtree(
                id INTEGER PRIMARY KEY,
                min_x REAL, max_x REAL,
                min_y REAL, max_y REAL,
                min_z REAL, max_z REAL
            )
            """
        )

        # Create mapping table (guid -> rtree id)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS element_geometry_mapping (
                guid TEXT PRIMARY KEY,
                rtree_id INTEGER UNIQUE,
                FOREIGN KEY (guid) REFERENCES elements(guid)
            )
            """
        )

        self.conn.commit()
        logger.debug(f"Initialized spatial index database: {self.db_path}")

    def insert_element(self, element: ProvisionalElement) -> None:
        """
        Insert element into spatial index.

        Args:
            element: ProvisionalElement to index
        """
        if self.conn is None:
            raise RuntimeError("Database not initialized")

        cursor = self.conn.cursor()

        # Calculate bounding box
        bbox = self._calculate_bbox(element)

        if bbox is None:
            logger.warning(f"Cannot calculate bbox for element {element.guid}, skipping")
            return

        # Insert into elements table
        cursor.execute(
            """
            INSERT OR REPLACE INTO elements
            (guid, element_type, confidence, source_layer, metadata)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                element.guid,
                element.element_type.value,
                element.confidence,
                element.source_layer,
                str(element.metadata),  # JSON serialization
            ),
        )

        # Get or create rtree id
        cursor.execute(
            "SELECT rtree_id FROM element_geometry_mapping WHERE guid = ?",
            (element.guid,),
        )
        row = cursor.fetchone()

        if row:
            rtree_id = row[0]
            # Update existing entry
            cursor.execute(
                """
                UPDATE element_geometry
                SET min_x=?, max_x=?, min_y=?, max_y=?, min_z=?, max_z=?
                WHERE id=?
                """,
                (bbox.min_x, bbox.max_x, bbox.min_y, bbox.max_y, bbox.min_z, bbox.max_z, rtree_id),
            )
        else:
            # Insert new entry
            cursor.execute(
                """
                INSERT INTO element_geometry
                (min_x, max_x, min_y, max_y, min_z, max_z)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bbox.min_x, bbox.max_x, bbox.min_y, bbox.max_y, bbox.min_z, bbox.max_z),
            )
            rtree_id = cursor.lastrowid

            # Map guid to rtree_id
            cursor.execute(
                "INSERT INTO element_geometry_mapping (guid, rtree_id) VALUES (?, ?)",
                (element.guid, rtree_id),
            )

        self.conn.commit()

    def insert_elements(self, elements: List[ProvisionalElement]) -> None:
        """Bulk insert elements."""
        for element in elements:
            self.insert_element(element)

        logger.debug(f"Indexed {len(elements)} elements")

    def query_by_bbox(
        self, bbox: BoundingBox, element_type: Optional[str] = None
    ) -> List[dict]:
        """
        Query elements intersecting bounding box.

        Args:
            bbox: Bounding box to query
            element_type: Filter by element type (optional)

        Returns:
            List of element records (dict with keys: guid, element_type, confidence, etc.)
        """
        if self.conn is None:
            raise RuntimeError("Database not initialized")

        cursor = self.conn.cursor()

        # Query R-tree for intersecting elements
        query = """
            SELECT e.guid, e.element_type, e.confidence, e.source_layer
            FROM elements e
            JOIN element_geometry_mapping m ON e.guid = m.guid
            JOIN element_geometry g ON m.rtree_id = g.id
            WHERE g.min_x <= ? AND g.max_x >= ?
              AND g.min_y <= ? AND g.max_y >= ?
              AND g.min_z <= ? AND g.max_z >= ?
        """
        params = [
            bbox.max_x,
            bbox.min_x,
            bbox.max_y,
            bbox.min_y,
            bbox.max_z,
            bbox.min_z,
        ]

        if element_type:
            query += " AND e.element_type = ?"
            params.append(element_type)

        cursor.execute(query, params)

        results = [dict(row) for row in cursor.fetchall()]
        return results

    def query_by_point(
        self, point: Point2D, radius: float, element_type: Optional[str] = None
    ) -> List[dict]:
        """
        Query elements within radius of a point.

        Args:
            point: Center point
            radius: Search radius (mm)
            element_type: Filter by element type (optional)

        Returns:
            List of element records
        """
        bbox = BoundingBox(
            min_x=point.x - radius,
            max_x=point.x + radius,
            min_y=point.y - radius,
            max_y=point.y + radius,
            min_z=-radius,
            max_z=radius,
        )

        return self.query_by_bbox(bbox, element_type)

    def count_elements(self, element_type: Optional[str] = None) -> int:
        """
        Count elements in index.

        Args:
            element_type: Filter by element type (optional)

        Returns:
            Number of elements
        """
        if self.conn is None:
            raise RuntimeError("Database not initialized")

        cursor = self.conn.cursor()

        if element_type:
            cursor.execute(
                "SELECT COUNT(*) FROM elements WHERE element_type = ?", (element_type,)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM elements")

        return cursor.fetchone()[0]

    def get_element(self, guid: str) -> Optional[dict]:
        """
        Get element by GUID.

        Args:
            guid: Element GUID

        Returns:
            Element record or None
        """
        if self.conn is None:
            raise RuntimeError("Database not initialized")

        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT guid, element_type, confidence, source_layer, metadata
            FROM elements
            WHERE guid = ?
            """,
            (guid,),
        )

        row = cursor.fetchone()
        return dict(row) if row else None

    def clear(self) -> None:
        """Clear all elements from index."""
        if self.conn is None:
            return

        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM elements")
        cursor.execute("DELETE FROM element_geometry")
        cursor.execute("DELETE FROM element_geometry_mapping")
        self.conn.commit()

        logger.debug("Cleared spatial index")

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def _calculate_bbox(self, element: ProvisionalElement) -> Optional[BoundingBox]:
        """
        Calculate bounding box for element.

        Args:
            element: Element to calculate bbox for

        Returns:
            BoundingBox or None if cannot calculate
        """
        if isinstance(element, GridLine):
            return BoundingBox(
                min_x=min(element.line.start.x, element.line.end.x),
                max_x=max(element.line.start.x, element.line.end.x),
                min_y=min(element.line.start.y, element.line.end.y),
                max_y=max(element.line.start.y, element.line.end.y),
                min_z=0.0,
                max_z=0.0,
            )

        elif isinstance(element, GridIntersection):
            # Point with small buffer
            buffer = 10.0  # mm
            return BoundingBox(
                min_x=element.point.x - buffer,
                max_x=element.point.x + buffer,
                min_y=element.point.y - buffer,
                max_y=element.point.y + buffer,
                min_z=0.0,
                max_z=0.0,
            )

        elif isinstance(element, Wall):
            return BoundingBox(
                min_x=min(element.centerline.start.x, element.centerline.end.x) - element.thickness / 2,
                max_x=max(element.centerline.start.x, element.centerline.end.x) + element.thickness / 2,
                min_y=min(element.centerline.start.y, element.centerline.end.y) - element.thickness / 2,
                max_y=max(element.centerline.start.y, element.centerline.end.y) + element.thickness / 2,
                min_z=0.0,
                max_z=element.height,
            )

        elif isinstance(element, Column):
            return BoundingBox(
                min_x=element.location.x - element.width / 2,
                max_x=element.location.x + element.width / 2,
                min_y=element.location.y - element.depth / 2,
                max_y=element.location.y + element.depth / 2,
                min_z=0.0,
                max_z=element.height,
            )

        elif isinstance(element, Slab):
            xs = [p.x for p in element.boundary]
            ys = [p.y for p in element.boundary]
            return BoundingBox(
                min_x=min(xs),
                max_x=max(xs),
                min_y=min(ys),
                max_y=max(ys),
                min_z=element.elevation,
                max_z=element.elevation + element.thickness,
            )

        logger.warning(f"Unknown element type for bbox calculation: {type(element)}")
        return None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
