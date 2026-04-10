import arcpy
import os
from datetime import datetime

# Path to the folder containing BIM files
workspace = r"\Downloads\ARENA"
arcpy.env.workspace = workspace
results = []

# ------------------------------------------------------------------
# IFC Length Unit Extraction
# ------------------------------------------------------------------

def get_ifc_length_unit(ifc_path):
    try:
        with open(ifc_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                uline = line.upper()

                if "IFCSIUNIT" in uline and "LENGTHUNIT" in uline:
                    if "MILLIMETRE" in uline or ".MILLIMETRE." in uline:
                        return "Millimeter"
                    if ".METRE." in uline:   # ← exact dot-wrapped match BEFORE bare METRE
                        return "Meter"
                    if "METRE" in uline:     # ← fallback
                        return "Meter"

                if "IFCCONVERSIONBASEDUNIT" in uline and "LENGTHUNIT" in uline:
                    if "FOOT" in uline or ".FOOT." in uline:
                        return "Foot"
                    if "INCH" in uline or ".INCH." in uline:
                        return "Inch"

        return "Unknown"
    except Exception:
        return "Unknown"

# Output text file paths (saved in the workspace folder)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_txt         = os.path.join(workspace, f"BIM_Report_{timestamp}.txt")
output_ungeoref_txt = os.path.join(workspace, f"BIM_UnGeoreferenced_Report_{timestamp}.txt")

# Iterate through the folder
for dirpath, dirnames, filenames in os.walk(workspace):
    for filename in filenames:

# reliable IFC detection (filename-based, ArcGIS-safe)
        is_ifc = filename.lower().endswith((".ifc", ".ifczip"))
        is_rvt = filename.lower().endswith(".rvt")

        if not (is_ifc or is_rvt):
            continue

        full_path = os.path.join(dirpath, filename)

        # Describe the BIM file
        desc = arcpy.Describe(full_path)
        exterior_extent = None
        fversion =desc.version

        # Look for ExteriorShell feature class
        if hasattr(desc, "children"):
            for child in desc.children:
                for fc in getattr(child, "children", []):
                    fc_name = getattr(fc, "name", "").lower()

                    if "exteriorshell" in fc_name:
                        fc_path = getattr(fc, "catalogPath", None)
                        if fc_path:
                            fc_desc = arcpy.Describe(fc_path)
                            ext = fc_desc.extent

                            sr_obj = ext.spatialReference
                            exterior_extent = {
                                "XMin": ext.XMin,
                                "YMin": ext.YMin,
                                "XMax": ext.XMax,
                                "YMax": ext.YMax,
                                "ZMin": ext.ZMin,
                                "ZMax": ext.ZMax,
                                "SpatialReference": (
                                    sr_obj.name if sr_obj else None
                                )
                            }

                            epsg_code = sr_obj.factoryCode if sr_obj else None
                            
                        break  # stop once ExteriorShell is found

        # ---- GEOREFERENCE STATUS (MUST BE PER FILE) ----
        spatial_ref = (
            exterior_extent.get("SpatialReference")
            if exterior_extent else None
        )

        if not exterior_extent:
            geo_status = "UN-GEOREFERENCED (No ExteriorShell)"
        elif not spatial_ref or spatial_ref.lower() in ("unknown", "unnamed"):
            geo_status = "UN-GEOREFERENCED (No Spatial Reference)"
        else:
            geo_status = "GEOREFERENCED"


        # ---- LENGTH UNIT (IFC only) ----
        if is_ifc:
            length_unit = get_ifc_length_unit(full_path)
        else:
            length_unit = "N/A (RVT)"  # RVT files don't expose length unit via text parsing

        # ---- APPEND RESULT (PER FILE) ----
        results.append({
            "Name": desc.name,
            "DataType": desc.dataType,
            "LengthDisplayUnit": getattr(desc, "lengthDisplayUnit", None),
            "DisplayUnitSystem": getattr(desc, "displayUnitSystem", None),
            "ExteriorShellExtent": exterior_extent,
            "GeoreferenceStatus": geo_status,
            "LengthUnit": length_unit,
            "EPSG Code" :epsg_code,
            "File Version" :fversion,
        })

# ------------------------------------------------------------------
# Summary Builder
# ------------------------------------------------------------------

def build_summary(results):
    total = len(results)
    georef = sum(
        1 for r in results
        if r.get("GeoreferenceStatus") == "GEOREFERENCED"
    )
    ungeoref = total - georef

    ungeoref_files = [
        r.get("Name") for r in results
        if r.get("GeoreferenceStatus", "").startswith("UN-GEOREFERENCED")
    ]

    return {
        "Total": total,
        "Georeferenced": georef,
        "UnGeoreferenced": ungeoref,
        "UnGeoreferencedFiles": ungeoref_files
    }

# ------------------------------------------------------------------
# Shared per-file field definitions
# ------------------------------------------------------------------

REPORT_HEADERS = [
    "BIM File",
    "DataType",
    "Georeference Status",
    "Reason",
    "SpatialReference",
    "EPSG Code",
    "File Version",
    "ExteriorShell Extent (XMin)",
    "ExteriorShell Extent (YMin)",
    "ExteriorShell Extent (XMax)",
    "ExteriorShell Extent (YMax)",
    "ExteriorShell Extent (ZMin)",
    "ExteriorShell Extent (ZMax)",
    "LengthDisplayUnit",
    "DisplayUnitSystem",
    "ModelLengthUnit",
]

def build_row_values(item):
    """Return a dict of header → value for one result entry."""
    extent = item.get("ExteriorShellExtent") or {}
    geo_status = item.get("GeoreferenceStatus", "")

    # Derive a concise reason from the status string
    if "No ExteriorShell" in geo_status:
        reason = "No ExteriorShell feature class found"
    elif "No Spatial Reference" in geo_status:
        reason = "ExteriorShell present but CRS is unknown/unnamed"
    else:
        reason = "N/A"

    return {
        "BIM File"                      : item.get("Name"),
        "DataType"                      : item.get("DataType"),
        "Georeference Status"           : geo_status,
        "Reason"                        : reason,
        "SpatialReference"              : extent.get("SpatialReference"),
        "EPSG Code"                     : item.get("EPSG Code"),
        "File Version"                  : item.get("File Version"),
        "ExteriorShell Extent (XMin)"   : extent.get("XMin"),
        "ExteriorShell Extent (YMin)"   : extent.get("YMin"),
        "ExteriorShell Extent (XMax)"   : extent.get("XMax"),
        "ExteriorShell Extent (YMax)"   : extent.get("YMax"),
        "ExteriorShell Extent (ZMin)"   : extent.get("ZMin"),
        "ExteriorShell Extent (ZMax)"   : extent.get("ZMax"),
        "LengthDisplayUnit"             : item.get("LengthDisplayUnit"),
        "DisplayUnitSystem"             : item.get("DisplayUnitSystem"),
        "ModelLengthUnit"               : item.get("LengthUnit"),
    }

# ------------------------------------------------------------------
# Full BIM Report (all files)
# ------------------------------------------------------------------

def format_results(results):
    lines = []
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = build_summary(results)

    lines.append("=" * 80)
    lines.append("BIM GEOREFERENCING REPORT")
    lines.append(f"Report Generated : {report_time}")
    lines.append(f"Workspace        : {workspace}")
    lines.append("=" * 80)
    lines.append("")

    # ---- SUMMARY SECTION ----
    lines.append("=" * 80)
    lines.append("BIM GEOREFERENCING SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Total BIM Files          : {summary['Total']}")
    lines.append(f"Georeferenced Models     : {summary['Georeferenced']}")
    lines.append(f"Un-Georeferenced Models  : {summary['UnGeoreferenced']}")

    # ---- DISCLAIMER ----
    lines.append("")
    lines.append("=" * 80)
    lines.append("DISCLAIMER")
    lines.append("=" * 80)
    lines.append(
        "The report just shows the Spatial Reference (CRS) and extent "
        "that is recorded in the file."
    )
    lines.append(
        "The vendor needs to ensure, however, that the georeferenced data "
        "is correct for the project location."
    )
    lines.append("=" * 80)
    lines.append("")

    if summary["UnGeoreferencedFiles"]:
        lines.append("")
        lines.append("WARNING: UN-GEOREFERENCED FILES:")
        for name in summary["UnGeoreferencedFiles"]:
            lines.append(f"  - {name}")

    lines.append("=" * 80)
    lines.append("")

    # ---- PER-FILE VERTICAL TABLE ----
    for item in results:
        values = build_row_values(item)
        lines.append("-" * 80)
        for h in REPORT_HEADERS:
            lines.append(f"{h:<35}: {values.get(h, 'N/A')}")
        lines.append("-" * 80)
        lines.append("")

    return lines

# ------------------------------------------------------------------
# Un-Georeferenced Report (flagged files only)
# ------------------------------------------------------------------

def format_ungeoref_report(results):
    """Generate a focused report listing only un-georeferenced BIM files."""
    lines = []
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = build_summary(results)

    ungeoref_results = [
        r for r in results
        if r.get("GeoreferenceStatus", "").startswith("UN-GEOREFERENCED")
    ]

    lines.append("=" * 80)
    lines.append("BIM UN-GEOREFERENCED MODELS REPORT")
    lines.append(f"Report Generated : {report_time}")
    lines.append(f"Workspace        : {workspace}")
    lines.append("=" * 80)
    lines.append("")

    # ---- SUMMARY SECTION ----
    lines.append("=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Total BIM Files Scanned  : {summary['Total']}")
    lines.append(f"Georeferenced Models     : {summary['Georeferenced']}")
    lines.append(f"Un-Georeferenced Models  : {summary['UnGeoreferenced']}")
    lines.append("=" * 80)
    lines.append("")

    if not ungeoref_results:
        lines.append("✅ No un-georeferenced files found. All models carry a valid CRS.")
        lines.append("")
        return lines

    # ---- ACTION REQUIRED NOTE ----
    lines.append("=" * 80)
    lines.append("ACTION REQUIRED")
    lines.append("=" * 80)
    lines.append(
        "The following BIM files are missing a valid Coordinate Reference System (CRS)."
    )
    lines.append(
        "The vendor must georeference these models and resubmit before final acceptance."
    )
    lines.append("=" * 80)
    lines.append("")

    # ---- QUICK LIST ----
    lines.append("UN-GEOREFERENCED FILES:")
    for idx, item in enumerate(ungeoref_results, start=1):
        lines.append(f"  {idx}. {item.get('Name')}  —  {item.get('GeoreferenceStatus')}")
    lines.append("")
    lines.append("=" * 80)
    lines.append("")

    # ---- PER-FILE DETAIL CARDS ----
    lines.append("DETAILED FILE INFORMATION")
    lines.append("=" * 80)
    lines.append("")

    for item in ungeoref_results:
        values = build_row_values(item)
        lines.append("-" * 80)
        for h in REPORT_HEADERS:
            lines.append(f"{h:<35}: {values.get(h, 'N/A')}")
        lines.append("-" * 80)
        lines.append("")

    # ---- DISCLAIMER ----
    lines.append("=" * 80)
    lines.append("DISCLAIMER")
    lines.append("=" * 80)
    lines.append(
        "The report just shows the Spatial Reference (CRS) and extent "
        "that is recorded in the file."
    )
    lines.append(
        "The vendor needs to ensure, however, that the georeferenced data "
        "is correct for the project location."
    )
    lines.append("=" * 80)

    return lines

# ------------------------------------------------------------------
# Build output lines
# ------------------------------------------------------------------

if not results:
    output_lines = [
        "=" * 40,
        "BIM FILES REPORT",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Workspace: {workspace}",
        "No BIM (.rvt / .ifc) files found.",
        "=" * 40,
    ]
    ungeoref_lines = output_lines  # mirror the empty notice
else:
    output_lines    = format_results(results)
    ungeoref_lines  = format_ungeoref_report(results)

# ------------------------------------------------------------------
# Save – Full Report
# ------------------------------------------------------------------

save_choice = input("\nWould you like to save the FULL report to a text file? (yes/no): ").strip().lower()

if save_choice in ("yes", "y"):
    custom_path = input(
        f"Enter output path (or press Enter to use default):\n  [{output_txt}]: "
    ).strip()
    if custom_path:
        output_txt = custom_path

    try:
        os.makedirs(os.path.dirname(output_txt), exist_ok=True)
        with open(output_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))
        print(f"\n✅ Full report saved to: {output_txt}")
    except PermissionError:
        print("\n❌ Permission denied. Try saving to Documents or Desktop.")
    except Exception as e:
        print(f"\n❌ Failed to save full report: {e}")

# ------------------------------------------------------------------
# Save – Un-Georeferenced Report
# ------------------------------------------------------------------

summary_check = build_summary(results)

if summary_check["UnGeoreferenced"] > 0:
    save_ungeoref = input(
        f"\nWould you like to save the UN-GEOREFERENCED report to a text file? (yes/no): "
    ).strip().lower()

    if save_ungeoref in ("yes", "y"):
        custom_ungeoref_path = input(
            f"Enter output path (or press Enter to use default):\n  [{output_ungeoref_txt}]: "
        ).strip()
        if custom_ungeoref_path:
            output_ungeoref_txt = custom_ungeoref_path

        try:
            os.makedirs(os.path.dirname(output_ungeoref_txt), exist_ok=True)
            with open(output_ungeoref_txt, "w", encoding="utf-8") as f:
                f.write("\n".join(ungeoref_lines))
            print(f"\n✅ Un-Georeferenced report saved to: {output_ungeoref_txt}")
        except PermissionError:
            print("\n❌ Permission denied. Try saving to Documents or Desktop.")
        except Exception as e:
            print(f"\n❌ Failed to save un-georeferenced report: {e}")
else:
    print("\n✅ All models are georeferenced — no separate un-georeferenced report generated.")