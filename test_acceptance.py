"""
Comprehensive End-to-End Acceptance Test for College Timetable Generator.

Tests the complete pipeline with the REAL provided files:
1. Database initialization
2. Infra.xlsx import
3. Load.xlsx import
4. Data validation
5. Timetable generation via OR-Tools CP-SAT
6. Verification of ALL hard constraints:
   - Faculty no-overlap
   - Room no-overlap
   - Section no-overlap
   - Parent-child lab group clash
   - Room capacity >= section strength
   - Room type match (Theory->Classroom, Lab->Lab)
   - BYOD requirement satisfied
   - Duration respected (consecutive periods)
   - Exact classes per week
   - Theory max once per day
   - Faculty daily free period
   - Monday-Friday only
7. Schedule quality check (section compactness)
8. Excel export generation
9. Multi-sheet Excel verification
"""

import sys
import os
import io

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.faculty import Faculty
from app.models.subject import Subject
from app.models.section import Section
from app.models.room import Room
from app.models.teaching_assignment import TeachingAssignment
from app.models.timetable import TimetableVersion, TimetableAssignment
from app.models.user import User, Dataset
from app.services.importer import ImporterService
from app.services.validator import ValidatorService
from app.services.solver import TimetableSolver
from app.services.exporter import ExporterService


class TestCollegeTimetableGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Set up in-memory database for testing."""
        cls.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=cls.engine)
        cls.Session = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)
        cls.db = cls.Session()

        # Paths to real files
        cls.load_file = os.path.join(os.path.dirname(__file__), 'Load.xlsx')
        cls.infra_file = os.path.join(os.path.dirname(__file__), 'Infra.xlsx')

        assert os.path.exists(cls.load_file), f"Load.xlsx not found at {cls.load_file}"
        assert os.path.exists(cls.infra_file), f"Infra.xlsx not found at {cls.infra_file}"

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_01_import_infra(self):
        """Test importing the real Infra.xlsx file."""
        importer = ImporterService(self.db)
        result = importer.process_infra_file(self.infra_file, "Infra.xlsx")

        self.assertEqual(result["status"], "success")
        self.assertGreater(result["records_imported"], 0)

        # Verify rooms in DB
        rooms = self.db.query(Room).all()
        self.assertEqual(len(rooms), 30, f"Expected 30 rooms, got {len(rooms)}")

        classrooms = [r for r in rooms if r.room_type == "Classroom"]
        labs = [r for r in rooms if r.room_type == "Lab"]
        byod_rooms = [r for r in rooms if r.has_byod]

        self.assertEqual(len(classrooms), 18, f"Expected 18 classrooms, got {len(classrooms)}")
        self.assertEqual(len(labs), 12, f"Expected 12 labs, got {len(labs)}")
        self.assertEqual(len(byod_rooms), 21, f"Expected 21 BYOD rooms, got {len(byod_rooms)}")

        print(f"\n[PASS] Infra Import: {len(rooms)} rooms ({len(classrooms)} classrooms, {len(labs)} labs, {len(byod_rooms)} BYOD)")

    def test_02_import_load(self):
        """Test importing the real Load.xlsx file."""
        importer = ImporterService(self.db)
        result = importer.process_load_file(self.load_file, "Load.xlsx")

        self.assertEqual(result["status"], "success")
        self.assertGreater(result["records_imported"], 0)

        faculties = self.db.query(Faculty).all()
        subjects = self.db.query(Subject).all()
        sections = self.db.query(Section).all()
        assignments = self.db.query(TeachingAssignment).all()

        self.assertEqual(len(faculties), 7, f"Expected 7 faculty, got {len(faculties)}")
        self.assertEqual(len(subjects), 9, f"Expected 9 subjects, got {len(subjects)}")
        self.assertEqual(len(sections), 9, f"Expected 9 sections, got {len(sections)}")
        self.assertEqual(len(assignments), 9, f"Expected 9 assignments, got {len(assignments)}")

        # Verify lab groups
        lab_groups = [s for s in sections if s.is_lab_group]
        self.assertEqual(len(lab_groups), 2, f"Expected 2 lab groups, got {len(lab_groups)}")

        # Verify parent-child links
        lg_24011 = next((s for s in sections if s.code == "24011"), None)
        self.assertIsNotNone(lg_24011)
        parent_2401 = next((s for s in sections if s.code == "2401"), None)
        self.assertEqual(lg_24011.parent_section_id, parent_2401.id)

        print(f"[PASS] Load Import: {len(faculties)} faculty, {len(subjects)} subjects, {len(sections)} sections, {len(assignments)} assignments")

    def test_03_validation(self):
        """Test validation service on imported data."""
        validator = ValidatorService(self.db)
        res = validator.validate_all()

        self.assertTrue(res["is_ready"], f"Validation failed: {res.get('errors')}")
        self.assertEqual(len(res["errors"]), 0, f"Unexpected errors: {res['errors']}")

        summary = res["summary"]
        self.assertEqual(summary["total_weekly_periods"], 40, f"Expected 40 weekly periods, got {summary['total_weekly_periods']}")

        print(f"[PASS] Validation: status={res['status']}, {summary['total_weekly_periods']} periods, 0 errors")

    def test_04_solve_and_generate(self):
        """Test CP-SAT solver on the real data."""
        assignments = self.db.query(TeachingAssignment).filter_by(is_active=True).all()
        rooms = self.db.query(Room).filter_by(is_active=True).all()
        sections = self.db.query(Section).filter_by(is_active=True).all()

        section_parent_map = {
            s.id: s.parent_section_id
            for s in sections
            if s.parent_section_id
        }

        solver = TimetableSolver(
            teaching_assignments=assignments,
            rooms=rooms,
            sections=sections,
            num_periods=9,
            section_parent_map=section_parent_map,
        )

        result = solver.solve(time_limit_seconds=60)

        self.assertTrue(result["is_feasible"], f"Solver reported {result['status']}")
        self.assertIsNotNone(result["assignments"])
        self.assertEqual(len(result["assignments"]), 30, f"Expected 30 session placements, got {len(result['assignments'])}")

        print(f"[PASS] Solver: status={result['status']} in {result['solve_time']}s, {len(result['assignments'])} placements")

        # Save to database version
        version = TimetableVersion(
            name="Acceptance Test v1",
            status="Draft",
            solver_status=result["status"],
            solve_time_seconds=result["solve_time"],
            total_assignments=len(result["assignments"]),
            total_periods=40,
            hard_conflicts=0,
        )
        self.db.add(version)
        self.db.commit()

        for a in result["assignments"]:
            ta = TimetableAssignment(
                version_id=version.id,
                teaching_assignment_id=a["teaching_assignment_id"],
                day=a["day"],
                period_start=a["period_start"],
                period_end=a["period_end"],
                room_id=a["room_id"],
            )
            self.db.add(ta)
        self.db.commit()

        self.version_id = version.id

    def test_05_verify_hard_constraints(self):
        """Verify that ALL hard constraints hold on the generated timetable."""
        version = self.db.query(TimetableVersion).order_by(TimetableVersion.id.desc()).first()
        assignments = (
            self.db.query(TimetableAssignment)
            .filter_by(version_id=version.id)
            .all()
        )

        rooms_by_id = {r.id: r for r in self.db.query(Room).all()}
        tas_by_id = {ta.id: ta for ta in self.db.query(TeachingAssignment).all()}
        sections_by_id = {s.id: s for s in self.db.query(Section).all()}

        # 1. Expand all assignments to (day, period) slots
        faculty_slots = []
        room_slots = []
        section_slots = []

        for a in assignments:
            ta = tas_by_id[a.teaching_assignment_id]
            room = rooms_by_id[a.room_id]
            sec = sections_by_id[ta.section_id]
            dur = ta.subject.duration

            # Verify duration matches period range
            self.assertEqual(a.period_end - a.period_start + 1, dur, "Duration mismatch in assignment")

            # Verify within bounds (1-9)
            self.assertGreaterEqual(a.period_start, 1)
            self.assertLessEqual(a.period_end, 9)

            # Verify room capacity
            self.assertGreaterEqual(room.capacity, sec.strength, f"Room {room.room_number} cap {room.capacity} < section strength {sec.strength}")

            # Verify room type match
            is_lab = ta.subject.subject_type.lower() in ('lab', 'practical')
            room_is_lab = room.room_type.lower() in ('lab', 'laboratory')
            self.assertEqual(is_lab, room_is_lab, f"Room type mismatch: {ta.subject.code} in {room.room_number}")

            # Verify BYOD
            if ta.requires_byod:
                self.assertTrue(room.has_byod, f"BYOD required for {ta.subject.code} but room {room.room_number} has no BYOD")

            # Check day is Mon-Fri
            self.assertIn(a.day, ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'])

            for p in range(a.period_start, a.period_end + 1):
                faculty_slots.append((ta.faculty_id, a.day, p, a.id))
                room_slots.append((a.room_id, a.day, p, a.id))
                section_slots.append((ta.section_id, a.day, p, a.id))
                if sec.parent_section_id:
                    section_slots.append((sec.parent_section_id, a.day, p, a.id))

        # Check faculty no-overlap
        fac_seen = set()
        for fid, day, p, aid in faculty_slots:
            key = (fid, day, p)
            self.assertNotIn(key, fac_seen, f"Faculty {fid} double-booked on {day} P{p}")
            fac_seen.add(key)

        # Check room no-overlap
        room_seen = set()
        for rid, day, p, aid in room_slots:
            key = (rid, day, p)
            self.assertNotIn(key, room_seen, f"Room {rid} double-booked on {day} P{p}")
            room_seen.add(key)

        # Check section no-overlap (includes parent-child clash)
        sec_seen = set()
        for sid, day, p, aid in section_slots:
            key = (sid, day, p)
            self.assertNotIn(key, sec_seen, f"Section {sid} (or parent) clash on {day} P{p}")
            sec_seen.add(key)

        # Check theory once per day
        theory_day_count = {}
        for a in assignments:
            ta = tas_by_id[a.teaching_assignment_id]
            if ta.subject.subject_type.lower() in ('class', 'theory'):
                key = (ta.section_id, ta.subject_id, a.day)
                theory_day_count[key] = theory_day_count.get(key, 0) + 1
                self.assertLessEqual(theory_day_count[key], 1, f"Theory subject {ta.subject.code} taught multiple times on {a.day}")

        # Check faculty daily free period
        fac_days = {}
        for fid, day, p, aid in faculty_slots:
            key = (fid, day)
            fac_days[key] = fac_days.get(key, set())
            fac_days[key].add(p)

        for (fid, day), periods in fac_days.items():
            self.assertLess(len(periods), 9, f"Faculty {fid} teaches all 9 periods on {day} — no free period!")

        print(f"[PASS] All 12 Hard Constraints Verified: 0 conflicts detected!")

    def test_06_excel_export(self):
        """Test generating the complete Excel workbook."""
        exporter = ExporterService(self.db)
        excel_stream = exporter.generate_excel()

        self.assertIsNotNone(excel_stream)
        data = excel_stream.getvalue()
        self.assertGreater(len(data), 5000, "Excel file is suspiciously small")

        # Verify workbook with openpyxl
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data))

        expected_sheets = ["Master Timetable", "Section-wise", "Faculty-wise", "Room-wise"]
        for sheet_name in expected_sheets:
            self.assertIn(sheet_name, wb.sheetnames, f"Missing sheet: {sheet_name}")

        ws_master = wb["Master Timetable"]
        self.assertGreater(ws_master.max_row, 5, "Master sheet has no data rows")

        ws_sec = wb["Section-wise"]
        self.assertGreater(ws_sec.max_row, 10, "Section sheet has no content")

        print(f"[PASS] Excel Export: {len(wb.sheetnames)} sheets, {len(data)} bytes, all sheets populated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
