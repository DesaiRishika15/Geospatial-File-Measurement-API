from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)

def sample_kml():
    return b'''<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><name>Square</name><Polygon><outerBoundaryIs><LinearRing>
        <coordinates>0,0 0.01,0 0.01,0.01 0,0.01 0,0</coordinates>
      </LinearRing></outerBoundaryIs></Polygon></Placemark>
      <Placemark><name>Road</name><LineString><coordinates>0,0 0.01,0</coordinates></LineString></Placemark>
      <Placemark><name>Marker</name><Point><coordinates>0.005,0.005</coordinates></Point></Placemark>
    </Document></kml>'''

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_upload_and_measurements():
    response = client.post("/api/files/", files={"file": ("sample.kml", sample_kml(), "application/vnd.google-earth.kml+xml")})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["feature_count"] == 3
    file_id = body["id"]
    assert client.get(f"/api/files/{file_id}").status_code == 200
    result = client.get(f"/api/files/{file_id}/measurements/")
    assert result.status_code == 200
    features = result.json()["features"]
    assert features[0]["geometry_type"] == "Polygon" and features[0]["area_m2"] > 0
    assert features[1]["geometry_type"] == "LineString" and features[1]["length_m"] > 0
    assert features[2]["measurement_status"] == "NOT_REQUIRED"

def test_invalid_extension():
    assert client.post("/api/files/", files={"file": ("data.txt", b"hello", "text/plain")}).status_code == 415

def test_unknown_id():
    assert client.get("/api/files/not-real").status_code == 404

def test_empty_file():
    assert client.post("/api/files/", files={"file": ("empty.kml", b"", "application/xml")}).status_code == 400
