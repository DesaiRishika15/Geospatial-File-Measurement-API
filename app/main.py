from __future__ import annotations
import io, logging, re, uuid, zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
import shapefile
from fastapi import FastAPI, File, HTTPException, UploadFile
from pyproj import CRS, Transformer
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import transform as shapely_transform

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI(title="Geospatial File Measurement API", description="Upload a zipped Shapefile or KML and inspect feature metadata and measurements.", version="1.0.0")
FILES: dict[str, dict[str, Any]] = {}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

def _safe_filename(filename: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "upload").name)[:180] or "upload"

def _normalize_features(features, source_crs):
    wgs84 = CRS.from_epsg(4326)
    transformer = None if source_crs == wgs84 else Transformer.from_crs(source_crs, wgs84, always_xy=True)
    result = []
    for feature in features:
        geom = feature["geometry"]
        if geom is not None and transformer:
            geom = shapely_transform(transformer.transform, geom)
        result.append({"id": feature["id"], "geometry": geom, "properties": feature["properties"]})
    return result

def _parse_shapefile(data: bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if not n.endswith("/")]
            shp_name = next((n for n in names if Path(n).suffix.lower() == ".shp"), None)
            if not shp_name:
                raise ValueError("ZIP does not contain a .shp file.")
            stem = Path(shp_name).stem.lower()
            def component(ext):
                n = next((n for n in names if Path(n).stem.lower() == stem and Path(n).suffix.lower() == ext), None)
                if not n:
                    raise ValueError(f"Shapefile ZIP is missing the matching {ext} component.")
                return archive.read(n)
            shp, shx, dbf = component(".shp"), component(".shx"), component(".dbf")
            prj_name = next((n for n in names if Path(n).stem.lower() == stem and Path(n).suffix.lower() == ".prj"), None)
            prj = archive.read(prj_name).decode("utf-8", errors="replace") if prj_name else None
        reader = shapefile.Reader(shp=io.BytesIO(shp), shx=io.BytesIO(shx), dbf=io.BytesIO(dbf), encoding="utf-8")
        fields = [f[0] for f in reader.fields[1:]]
        features = []
        for index, sr in enumerate(reader.iterShapeRecords()):
            geom = shape(sr.shape.__geo_interface__) if sr.shape.shapeType != shapefile.NULL else None
            features.append({"id": index, "geometry": geom, "properties": dict(zip(fields, list(sr.record)))})
        source_crs = CRS.from_wkt(prj) if prj else CRS.from_epsg(4326)
        return _normalize_features(features, source_crs), source_crs.to_string()
    except (zipfile.BadZipFile, shapefile.ShapefileException, OSError) as exc:
        raise ValueError(f"Could not read Shapefile ZIP: {exc}") from exc

def _local_name(tag):
    return tag.rsplit("}", 1)[-1]

def _descendant_text(element, wanted):
    for child in element.iter():
        if _local_name(child.tag) == wanted and child.text:
            return child.text.strip()
    return None

def _coords(text):
    coords = []
    if not text:
        return coords
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) >= 2:
            coords.append((float(parts[0]), float(parts[1])))
    return coords

def _kml_geometry(element):
    tag = _local_name(element.tag)
    if tag == "Point":
        c = _coords(_descendant_text(element, "coordinates"))
        return Point(c[0]) if c else None
    if tag == "LineString":
        c = _coords(_descendant_text(element, "coordinates"))
        return LineString(c) if len(c) >= 2 else None
    if tag == "Polygon":
        outer, holes = [], []
        for child in element.iter():
            name = _local_name(child.tag)
            if name == "outerBoundaryIs":
                outer = _coords(_descendant_text(child, "coordinates"))
            elif name == "innerBoundaryIs":
                hole = _coords(_descendant_text(child, "coordinates"))
                if hole: holes.append(hole)
        return Polygon(outer, holes) if len(outer) >= 4 else None
    if tag == "MultiGeometry":
        geoms = [g for child in element for g in [_kml_geometry(child)] if g is not None]
        if not geoms: return None
        if all(isinstance(g, (Polygon, MultiPolygon)) for g in geoms):
            return MultiPolygon([p for g in geoms for p in (list(g.geoms) if isinstance(g, MultiPolygon) else [g])])
        if all(isinstance(g, (LineString, MultiLineString)) for g in geoms):
            return MultiLineString([p for g in geoms for p in (list(g.geoms) if isinstance(g, MultiLineString) else [g])])
        return GeometryCollection(geoms)
    return None

def _parse_kml(data):
    try: root = ET.fromstring(data)
    except ET.ParseError as exc: raise ValueError(f"Invalid KML/XML: {exc}") from exc
    features = []
    for pm in [e for e in root.iter() if _local_name(e.tag) == "Placemark"]:
        geom = None
        for child in pm:
            if _local_name(child.tag) in {"Point", "LineString", "Polygon", "MultiGeometry"}:
                geom = _kml_geometry(child)
                if geom is not None: break
        props = {}
        for child in pm:
            name = _local_name(child.tag)
            if name in {"name", "description"} and child.text:
                props[name] = child.text.strip()
            if name == "ExtendedData":
                for item in child.iter():
                    if _local_name(item.tag) == "Data":
                        key, value = item.attrib.get("name"), _descendant_text(item, "value")
                        if key: props[key] = value
                    elif _local_name(item.tag) == "SimpleData" and item.attrib.get("name"):
                        props[item.attrib["name"]] = item.text
        features.append({"id": len(features), "geometry": geom, "properties": props})
    return _normalize_features(features, CRS.from_epsg(4326)), "EPSG:4326"

def _measurement(geom):
    if geom is None or geom.is_empty:
        return {"area_m2": None, "length_m": None, "measurement_status": "NO_GEOMETRY"}
    kind = geom.geom_type
    if kind in {"Polygon", "MultiPolygon"}:
        center = geom.representative_point()
        local = CRS.from_proj4(f"+proj=laea +lat_0={center.y:.10f} +lon_0={center.x:.10f} +datum=WGS84 +units=m +no_defs")
        trans = Transformer.from_crs(CRS.from_epsg(4326), local, always_xy=True)
        projected = shapely_transform(trans.transform, geom)
        return {"area_m2": round(abs(projected.area), 3), "length_m": None, "measurement_status": "COMPLETED"}
    if kind in {"LineString", "MultiLineString", "LinearRing"}:
        center = geom.interpolate(0.5, normalized=True) if kind == "LineString" and geom.length else geom.representative_point()
        local = CRS.from_proj4(f"+proj=aeqd +lat_0={center.y:.10f} +lon_0={center.x:.10f} +datum=WGS84 +units=m +no_defs")
        trans = Transformer.from_crs(CRS.from_epsg(4326), local, always_xy=True)
        projected = shapely_transform(trans.transform, geom)
        return {"area_m2": None, "length_m": round(projected.length, 3), "measurement_status": "COMPLETED"}
    if kind in {"Point", "MultiPoint"}:
        return {"area_m2": None, "length_m": None, "measurement_status": "NOT_REQUIRED"}
    return {"area_m2": None, "length_m": None, "measurement_status": "UNSUPPORTED_GEOMETRY"}

def _feature_output(feature):
    geom = feature["geometry"]
    return {"feature_id": feature["id"], "geometry_type": geom.geom_type if geom is not None else None,
            "geometry": mapping(geom) if geom is not None else None, "crs": "EPSG:4326",
            "properties": feature["properties"], **_measurement(geom)}

@app.get("/health")
def health():
    return {"status": "ok", "service": "Geospatial File Measurement API"}

@app.post("/api/files/", status_code=201)
async def upload_file(file: UploadFile = File(...)):
    filename = _safe_filename(file.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".zip", ".kml"}:
        raise HTTPException(status_code=415, detail="Upload a .kml file or a .zip containing a Shapefile.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MiB upload limit.")
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        features, source_crs = _parse_shapefile(data) if suffix == ".zip" else _parse_kml(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected file parsing error")
        raise HTTPException(status_code=400, detail=f"Unable to process file: {exc}") from exc
    file_id = uuid.uuid4().hex[:12]
    FILES[file_id] = {"id": file_id, "filename": filename, "feature_count": len(features),
                      "source_crs": source_crs, "status": "COMPLETED", "features": features}
    return {"id": file_id, "filename": filename, "feature_count": len(features), "crs": source_crs,
            "status": "COMPLETED", "message": "File uploaded and processed successfully."}

@app.get("/api/files/{file_id}")
def file_information(file_id: str):
    record = FILES.get(file_id)
    if not record: raise HTTPException(status_code=404, detail="File not found. Upload it first; data is stored in memory.")
    return {k: record[k] for k in ("id", "filename", "feature_count", "source_crs", "status")} | {"crs": record["source_crs"]}

@app.get("/api/files/{file_id}/measurements/")
def file_measurements(file_id: str):
    record = FILES.get(file_id)
    if not record: raise HTTPException(status_code=404, detail="File not found. Upload it first; data is stored in memory.")
    return {"file_id": file_id, "filename": record["filename"], "feature_count": record["feature_count"],
            "source_crs": record["source_crs"],
            "measurement_crs": "Feature-local projected CRS (LAEA for polygon area; AEQD for line length)",
            "features": [_feature_output(f) for f in record["features"]]}
