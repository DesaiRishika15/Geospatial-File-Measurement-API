# Geospatial File Measurement API

A FastAPI backend that accepts zipped ESRI Shapefiles or KML, extracts feature metadata, and calculates projected area or length.

## Features
- `POST /api/files/`: upload `.kml` or `.zip` containing `.shp`, `.shx`, `.dbf` (and preferably `.prj`).
- `GET /api/files/{id}`: file metadata and processing status.
- `GET /api/files/{id}/measurements/`: per-feature geometry, attributes, CRS, and measurements.
- Polygon/MultiPolygon area; LineString/MultiLineString length; Point/MultiPoint without measurements.
- Reprojects Shapefile data to EPSG:4326 for a consistent internal representation.
- Never measures in latitude/longitude degrees: uses local Lambert azimuthal equal-area projection for polygon area and azimuthal equidistant projection for line length.
- 25 MiB upload limit. In-memory storage keeps this assessment simple; records reset when the process restarts.

## Run locally (Windows PowerShell)
Python 3.10+ recommended (3.11 works well).

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload
```

If activation is blocked, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal, or use `.venv\Scripts\python.exe -m pip install -r requirements.txt`.

Open `http://127.0.0.1:8000/docs` for interactive Swagger docs, or `http://127.0.0.1:8000/health` for health status.

## Test it
In Swagger UI, use `POST /api/files/`, select `sample_data/sample.kml`, and click Execute. Copy the returned `id` into `GET /api/files/{id}` and `GET /api/files/{id}/measurements/`.

PowerShell examples:
```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/files/" -F "file=@sample_data/sample.kml"
curl.exe "http://127.0.0.1:8000/api/files/REPLACE_WITH_ID"
curl.exe "http://127.0.0.1:8000/api/files/REPLACE_WITH_ID/measurements/"
```

A ZIP must contain matching `.shp`, `.shx`, and `.dbf` files. Include a matching `.prj` so the source CRS is known. If `.prj` is missing, EPSG:4326 is assumed and reported as the source CRS. KML coordinates use EPSG:4326 by convention. Supported KML geometries include Point, LineString, Polygon, and common MultiGeometry placemarks.

## Response fields
Every measurement feature includes `feature_id`, `geometry_type`, `geometry`, `crs`, `properties`, `area_m2`, `length_m`, and `measurement_status`. Points return `null` measurements with `NOT_REQUIRED`. Unsupported geometries return a status instead of crashing.

## Architecture / flow
1. Validate extension, non-empty upload, and size.
2. Parse KML XML or read matching Shapefile components directly from ZIP bytes (without extracting to disk).
3. Extract feature ID, geometry, and attributes.
4. Transform source Shapefile coordinates to EPSG:4326.
5. Choose a local projected CRS for each feature's measurement.
6. Return JSON.

## Design decisions and production considerations
- **FastAPI:** automatic OpenAPI documentation and straightforward API design.
- **pyshp + Shapely + pyproj:** avoids a system-level GDAL dependency while supporting common geometry operations and CRS transformation.
- **Local projections:** suitable for typical regional features. For huge, polar, or antimeridian-spanning geometries, use geodesic calculations or a domain-specific projected CRS.
- **In-memory persistence:** suitable for an assessment demo, but data is lost on restart and not shared across workers. Production should use persistent storage and a database.
- **Security:** sanitized filename, bounded upload, no ZIP extraction. Production should add authentication, rate limits, archive resource limits, malware scanning, persistent storage, and structured audit logs.

## Tests
```powershell
pytest -q
```

## Learning and future scope
This project demonstrates API design, geospatial file processing, CRS transformation, measurement, validation, and Python backend development. Future improvements include persistent storage, background jobs, configurable units, geodesic measurements, authentication, Docker, CI, and monitoring.

## Submission checklist
- [ ] Install dependencies and run the app.
- [ ] Run tests.
- [ ] Test KML and zipped Shapefile endpoints.
- [ ] Review the implementation and understand CRS/projection decisions.
- [ ] Create a public GitHub repository, push this project, and submit the repository URL.
