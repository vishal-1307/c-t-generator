"""
Comprehensive API Integration Test Suite for College Timetable Generator.
Tests all FastAPI endpoints via TestClient.
"""

import sys
import os
import io

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

import unittest
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.user import User
from app.core.security import get_password_hash


class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create tables
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

        # Ensure admin user exists
        db = SessionLocal()
        admin = db.query(User).filter_by(username="admin").first()
        if not admin:
            admin = User(
                username="admin",
                hashed_password=get_password_hash("admin123"),
                role="admin",
                is_active=True
            )
            db.add(admin)
            db.commit()
        db.close()

        # Login to get JWT token
        resp = cls.client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "admin123"}
        )
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        token = resp.json()["access_token"]
        cls.headers = {"Authorization": f"Bearer {token}"}

    def test_01_health_and_ready(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        resp = self.client.get("/api/v1/ready")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ready")
        print("[PASS] Health and Ready endpoints")

    def test_02_templates_download(self):
        resp = self.client.get("/api/v1/templates/load")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.content), 2000)

        resp = self.client.get("/api/v1/templates/infra")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.content), 2000)
        print("[PASS] Template downloads (Load and Infra)")

    def test_03_dashboard_summary_initial(self):
        resp = self.client.get("/api/v1/dashboard/summary", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("workflow", data)
        self.assertIn("counts", data)
        print(f"[PASS] Dashboard summary: {data['counts']}")

    def test_04_upload_infra(self):
        with open("Infra.xlsx", "rb") as f:
            resp = self.client.post(
                "/api/v1/upload/infra",
                files={"file": ("Infra.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200, f"Upload infra failed: {resp.text}")
        data = resp.json()
        self.assertEqual(data["data"]["status"], "success")
        print(f"[PASS] Upload Infra.xlsx: {data['data']['records_imported']} rooms imported")

    def test_05_upload_load(self):
        with open("Load.xlsx", "rb") as f:
            resp = self.client.post(
                "/api/v1/upload/load",
                files={"file": ("Load.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                headers=self.headers,
            )
        self.assertEqual(resp.status_code, 200, f"Upload load failed: {resp.text}")
        data = resp.json()
        self.assertEqual(data["data"]["status"], "success")
        print(f"[PASS] Upload Load.xlsx: {data['data']['records_imported']} assignments imported")

    def test_06_validation_endpoint(self):
        resp = self.client.post("/api/v1/validate/", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertTrue(data["is_ready"])
        self.assertEqual(len(data["errors"]), 0)
        print(f"[PASS] Validation endpoint: is_ready={data['is_ready']}, periods={data['summary']['total_weekly_periods']}")

    def test_07_generate_and_status(self):
        # Trigger generation
        resp = self.client.post("/api/v1/generate/", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        version_id = resp.json()["version_id"]
        print(f"[PASS] Timetable generation started: version_id={version_id}")

        # In TestClient, background tasks run synchronously during the request
        # Check status
        resp = self.client.get(f"/api/v1/generate/status/{version_id}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        status_data = resp.json()
        self.assertIn(status_data["status"], ["Draft", "Published", "Running", "Queued"])
        print(f"[PASS] Generation status: status={status_data['status']}, solver={status_data.get('solver_status')}")

    def test_08_timetable_views(self):
        # Get sections dropdown
        resp = self.client.get("/api/v1/timetable/sections", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        sections = resp.json()
        self.assertGreater(len(sections), 0)

        # Section timetable
        sec_code = sections[0]["code"]
        resp = self.client.get(f"/api/v1/timetable/section/{sec_code}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        sec_data = resp.json()
        self.assertIn("assignments", sec_data)
        print(f"[PASS] Section timetable view for {sec_code}: {len(sec_data['assignments'])} slots")

        # Faculty dropdown
        resp = self.client.get("/api/v1/timetable/faculties", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        faculties = resp.json()
        self.assertGreater(len(faculties), 0)

        # Faculty timetable
        fac_id = faculties[0]["id"]
        resp = self.client.get(f"/api/v1/timetable/faculty/{fac_id}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        fac_data = resp.json()
        self.assertIn("assignments", fac_data)
        print(f"[PASS] Faculty timetable view for {faculties[0]['name']}: {len(fac_data['assignments'])} slots")

        # Rooms dropdown
        resp = self.client.get("/api/v1/timetable/rooms", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        rooms = resp.json()
        self.assertGreater(len(rooms), 0)

        # Master timetable
        resp = self.client.get("/api/v1/timetable/master", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        master_data = resp.json()
        self.assertIn("assignments", master_data)
        self.assertIn("total", master_data)
        print(f"[PASS] Master timetable view: {master_data['total']} total assignments")

    def test_09_export_excel(self):
        resp = self.client.get("/api/v1/export/excel", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers["content-type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.assertGreater(len(resp.content), 5000)
        print(f"[PASS] Excel export endpoint: {len(resp.content)} bytes downloaded")

    def test_10_ai_assistant(self):
        # Test summary query
        resp = self.client.post(
            "/api/v1/assistant/chat",
            json={"message": "Give me a summary of the timetable"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("answer", data)
        self.assertIn("tool_used", data)
        print(f"[PASS] AI Assistant summary query: tool={data['tool_used']}")

        # Test free rooms query
        resp = self.client.post(
            "/api/v1/assistant/chat",
            json={"message": "Which rooms are available on Monday at period 3?"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("answer", data)
        print(f"[PASS] AI Assistant free rooms query: tool={data['tool_used']}")

    def test_11_data_clear_preview(self):
        resp = self.client.get("/api/v1/data/clear/preview/load", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("counts", data)
        self.assertIn("preserved", data)
        print(f"[PASS] Clear Load preview: counts={data['counts']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
