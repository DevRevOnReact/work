from fastapi.testclient import TestClient

from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session


def test_uploaded_cv_survives_ephemeral_filesystem_reset(db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    monkeypatch.setattr(settings(), "store_resume_files_in_db", True)
    app.dependency_overrides[session] = lambda: db
    content = b"Jane Doe\nSenior Frontend Engineer\nReact TypeScript JavaScript\n5 years experience"
    try:
        client = TestClient(app)
        uploaded = client.post("/resumes", files={"file": ("cv.txt", content, "text/plain")})
        assert uploaded.status_code == 200
        value = uploaded.json()
        assert "content" not in value
        (tmp_path / "resumes" / value["storage_key"]).unlink()
        response = client.get(f"/resumes/{value['id']}/file")
        assert response.status_code == 200 and response.content == content
        assert client.post(f"/resumes/{value['id']}/activate").status_code == 200
        assert "content" not in client.get("/resumes").json()[0]
    finally:
        app.dependency_overrides.clear()
