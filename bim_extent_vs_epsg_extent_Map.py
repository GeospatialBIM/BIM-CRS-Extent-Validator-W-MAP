"""
BIM Georeferencing Validation (TXT → JSON + GDB)
-----------------------------------------------
Outputs:
1. GDB: Feature Classes (suffixed with _Empty if out of bounds).
2. JSON: A full export of all 14 metadata fields for every record,
         including per-record EPSG bounds and an Inside/Outside status.
"""

import os
import json
import re
import time
from collections import defaultdict

# ── Dependency Checks ────────────────────────────────────────────────────────

try:
    from pyproj import CRS, Transformer
    PYPROJ_AVAILABLE = True
except Exception:
    PYPROJ_AVAILABLE = False

try:
    import arcpy
    ARCPY_AVAILABLE = True
except Exception:
    ARCPY_AVAILABLE = False

# ──────────────────────────────────────────────────────────────────────────────
# Utilities & Parser
# ──────────────────────────────────────────────────────────────────────────────

def _sanitise_fc_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if safe and (safe[0].isdigit() or safe.startswith("_")):
        safe = "EPSG_" + safe
    return safe[:64]

def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0

def get_crs_bounds(epsg: int) -> dict | None:
    if not PYPROJ_AVAILABLE: return None
    try:
        crs = CRS.from_epsg(epsg)
        aou = crs.area_of_use
        if not aou: return None
        lon_min, lat_min, lon_max, lat_max = aou.west, aou.south, aou.east, aou.north
        if crs.is_geographic:
            return {"XMin": lon_min, "YMin": lat_min, "XMax": lon_max, "YMax": lat_max}
        t = Transformer.from_crs(4326, epsg, always_xy=True)
        corners = [(lon_min, lat_min), (lon_min, lat_max), (lon_max, lat_min), (lon_max, lat_max)]
        xs, ys = [], []
        for lon, lat in corners:
            x, y = t.transform(lon, lat)
            if abs(x) < 1e15 and abs(y) < 1e15:
                xs.append(x)
                ys.append(y)
        return {"XMin": min(xs), "YMin": min(ys), "XMax": max(xs), "YMax": max(ys)} if xs else None
    except: return None

def check_extent_within_bounds(record: dict, bounds: dict | None) -> str:
    """
    Returns 'Inside', 'Outside', or 'Unknown' based on whether the
    BIM extent origin (XMin, YMin) falls within the EPSG CRS bounds.
    'Unknown' is returned when bounds could not be determined.
    """
    if bounds is None:
        return "Unknown"
    xmin = record.get("XMin", 0)
    ymin = record.get("YMin", 0)
    if (bounds["XMin"] <= xmin <= bounds["XMax"] and
            bounds["YMin"] <= ymin <= bounds["YMax"]):
        return "Inside"
    return "Outside"

def parse_bim_report(txt_path: str) -> list[dict]:
    """Parses all 14 fields from the BIM report."""
    if not os.path.exists(txt_path): return []
    records, current = [], {}
    KEY_MAP = {
        "DataType": "DataType",
        "Georeference Status": "Geo_Status",
        "SpatialReference": "Spatial_Ref",
        "ExteriorShell Extent (XMin)": "XMin",
        "ExteriorShell Extent (YMin)": "YMin",
        "ExteriorShell Extent (XMax)": "XMax",
        "ExteriorShell Extent (YMax)": "YMax",
        "ExteriorShell Extent (ZMin)": "ZMin",
        "ExteriorShell Extent (ZMax)": "ZMax",
        "EPSG Code": "EPSG_Code",
        "LengthDisplayUnit": "Length_Unit",
        "DisplayUnitSystem": "Unit_System",
        "ModelLengthUnit": "Model_Len_Unit",
    }
    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("BIM File"):
                if current: records.append(current)
                current = {"BIM_File": line.split(":", 1)[1].strip()}
            elif ":" in line:
                k, v = [x.strip() for x in line.split(":", 1)]
                if k in KEY_MAP:
                    m = KEY_MAP[k]
                    current[m] = _safe_float(v) if any(x in m for x in ["Min", "Max", "Code"]) else v
    if current: records.append(current)
    return records

# ──────────────────────────────────────────────────────────────────────────────
# GDB Logic
# ──────────────────────────────────────────────────────────────────────────────

def create_geodatabase_feature_classes(records: list[dict], gdb_path: str):
    if not ARCPY_AVAILABLE: return
    arcpy.env.overwriteOutput = True

    grouped = defaultdict(list)
    for rec in records:
        e = int(_safe_float(rec.get("EPSG_Code")))
        if e > 0: grouped[e].append(rec)

    if not arcpy.Exists(gdb_path):
        arcpy.management.CreateFileGDB(os.path.dirname(gdb_path), os.path.basename(gdb_path))

    # All 14 fields for the schema
    field_definitions = [
        ["BIM_File",       "TEXT",   "BIM File",            255],
        ["DataType",       "TEXT",   "Data Type",           100],
        ["Geo_Status",     "TEXT",   "Georeference Status", 50],
        ["Spatial_Ref",    "TEXT",   "Spatial Reference",   100],
        ["EPSG_Code",      "LONG",   "EPSG Code",           None],
        ["XMin",           "DOUBLE", "Extent XMin",         None],
        ["YMin",           "DOUBLE", "Extent YMin",         None],
        ["XMax",           "DOUBLE", "Extent XMax",         None],
        ["YMax",           "DOUBLE", "Extent YMax",         None],
        ["ZMin",           "DOUBLE", "Extent ZMin",         None],
        ["ZMax",           "DOUBLE", "Extent ZMax",         None],
        ["Length_Unit",    "TEXT",   "Length Display Unit", 50],
        ["Unit_System",    "TEXT",   "Display Unit System", 50],
        ["Model_Len_Unit", "TEXT",   "Model Length Unit",   50]
    ]

    for epsg, group in grouped.items():
        base_name = f"EPSG_{epsg}"
        sr = arcpy.SpatialReference(epsg)
        bounds = get_crs_bounds(epsg)

        # 1. Coordinate Validation
        valid_records = []
        for r in group:
            xmin, ymin = r.get("XMin", 0), r.get("YMin", 0)
            if not bounds or (bounds["XMin"] <= xmin <= bounds["XMax"] and bounds["YMin"] <= ymin <= bounds["YMax"]):
                valid_records.append(r)

        # 2a. Polygon feature class for valid (in-bounds) records
        if valid_records:
            final_fc_name = _sanitise_fc_name(base_name)
            fc_path = os.path.join(gdb_path, final_fc_name)

            if arcpy.Exists(fc_path): arcpy.management.Delete(fc_path)
            time.sleep(0.5)

            # Create polygon schema
            arcpy.management.CreateFeatureclass(gdb_path, final_fc_name, "POLYGON", spatial_reference=sr)
            for f_name, f_type, f_alias, f_len in field_definitions:
                arcpy.management.AddField(fc_path, f_name, f_type, field_alias=f_alias, field_length=f_len)

            cursor_fields = ["SHAPE@"] + [f[0] for f in field_definitions]
            with arcpy.da.InsertCursor(fc_path, cursor_fields) as cursor:
                for r in valid_records:
                    xmin, ymin, xmax, ymax = r["XMin"], r["YMin"], r["XMax"], r["YMax"]
                    poly = arcpy.Polygon(arcpy.Array([
                        arcpy.Point(xmin, ymin), arcpy.Point(xmin, ymax),
                        arcpy.Point(xmax, ymax), arcpy.Point(xmax, ymin),
                        arcpy.Point(xmin, ymin)
                    ]), sr)
                    row = [poly] + [r.get(f[0], "") for f in field_definitions]
                    cursor.insertRow(row)
            print(f"  ✅ {final_fc_name}: {len(valid_records)} polygon rows added.")

        # 2b. Standalone table for invalid (out-of-bounds) records
        invalid_records = [r for r in group if r not in valid_records]
        if invalid_records:
            table_name = _sanitise_fc_name(base_name + "_OutOfBounds")
            table_path = os.path.join(gdb_path, table_name)

            if arcpy.Exists(table_path): arcpy.management.Delete(table_path)
            time.sleep(0.5)

            # Create standalone table (no geometry)
            arcpy.management.CreateTable(gdb_path, table_name)
            for f_name, f_type, f_alias, f_len in field_definitions:
                arcpy.management.AddField(table_path, f_name, f_type, field_alias=f_alias, field_length=f_len)

            cursor_fields = [f[0] for f in field_definitions]
            with arcpy.da.InsertCursor(table_path, cursor_fields) as cursor:
                for r in invalid_records:
                    row = [r.get(f[0], "") for f in field_definitions]
                    cursor.insertRow(row)
            print(f"  ⚠️  {table_name}: {len(invalid_records)} out-of-bounds rows saved as table.")

# ──────────────────────────────────────────────────────────────────────────────
# Main Execution
# ──────────────────────────────────────────────────────────────────────────────

def main(report_path: str, gdb_path: str | None = None):
    print("\n--- BIM DATA PROCESSING ---")

    # 1. Parse Records (All 14 fields)
    records = parse_bim_report(report_path)
    if not records:
        print("No records found in report.")
        return

    # 2. Build per-EPSG bounds cache (avoids repeated CRS lookups)
    bounds_cache: dict[int, dict | None] = {}
    for rec in records:
        epsg = int(_safe_float(rec.get("EPSG_Code", 0)))
        if epsg > 0 and epsg not in bounds_cache:
            bounds_cache[epsg] = get_crs_bounds(epsg)

    # 3. Enrich each record with EPSG bounds + Inside/Outside status
    enriched_records = []
    for rec in records:
        epsg = int(_safe_float(rec.get("EPSG_Code", 0)))
        bounds = bounds_cache.get(epsg) if epsg > 0 else None

        # Build the bounds block for JSON (null values when unavailable)
        epsg_bounds_block = None
        if bounds is not None:
            epsg_bounds_block = {
                "XMin": bounds["XMin"],
                "YMin": bounds["YMin"],
                "XMax": bounds["XMax"],
                "YMax": bounds["YMax"],
            }

        enriched = dict(rec)  # copy all 14 original fields
        enriched["EPSG_Bounds"] = epsg_bounds_block          # bounding box of the CRS (null if unknown)
        enriched["Bounds_Status"] = check_extent_within_bounds(rec, bounds)  # "Inside" / "Outside" / "Unknown"
        enriched_records.append(enriched)

    # 4. Summary counters
    status_counts = defaultdict(int)
    for rec in enriched_records:
        status_counts[rec["Bounds_Status"]] += 1

    # 5. Output JSON
    json_path = os.path.splitext(report_path)[0] + "_BIM_Metadata.json"
    output = {
        "total_files": len(enriched_records),
        "bounds_summary": {
            "Inside":  status_counts["Inside"],
            "Outside": status_counts["Outside"],
            "Unknown": status_counts["Unknown"],
        },
        "records": enriched_records,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4)
    print(f"✅ Metadata JSON created: {os.path.basename(json_path)}")
    print(f"   Inside: {status_counts['Inside']}  |  "
          f"Outside: {status_counts['Outside']}  |  "
          f"Unknown: {status_counts['Unknown']}")

    # 6. Create GDB
    if not gdb_path:
        gdb_path = os.path.splitext(report_path)[0] + "_BIM_Extents.gdb"
    create_geodatabase_feature_classes(records, gdb_path)

    print("--- PROCESSING COMPLETE ---\n")

# --- USER CONFIGURATION ---
REPORT_PATH = r"\\\Data\BIM\IFC\\BIM_Report_20260409_155703.txt"

if __name__ == "__main__":
    main(REPORT_PATH)