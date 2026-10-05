"""
Test the solver against the expanded real dataset (Load_Faculty_Expanded.xlsx)
which contains 36 teaching rows with multiple faculty and lab groups.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.room import Room
from app.models.teaching_assignment import TeachingAssignment
from app.models.section import Section
from app.services.importer import ImporterService
from app.services.validator import ValidatorService
from app.services.solver import TimetableSolver


def test_expanded_dataset():
    print("=" * 70)
    print("STRESS TEST: Load_Faculty_Expanded.xlsx (36 rows)")
    print("=" * 70)

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # 1. Import Infra
    importer = ImporterService(db)
    r_infra = importer.process_infra_file("Infra.xlsx", "Infra.xlsx")
    print(f"Infra imported: {r_infra['records_imported']} rooms")

    # 2. Import Expanded Load
    r_load = importer.process_load_file("Load_Faculty_Expanded.xlsx", "Load_Faculty_Expanded.xlsx")
    print(f"Load imported: {r_load['records_imported']} assignments, warnings: {len(r_load.get('warnings', []))}")

    # 3. Validate
    validator = ValidatorService(db)
    v_res = validator.validate_all()
    print(f"Validation status: {v_res['status']}")
    print(f"Summary: {v_res['summary']}")

    if v_res['errors']:
        print("ERRORS:")
        for e in v_res['errors']:
            print(f"  - {e['message']}")
        return

    # 4. Solve
    assignments = db.query(TeachingAssignment).filter_by(is_active=True).all()
    rooms = db.query(Room).filter_by(is_active=True).all()
    sections = db.query(Section).filter_by(is_active=True).all()

    section_parent_map = {
        s.id: s.parent_section_id
        for s in sections
        if s.parent_section_id
    }

    print(f"\nLaunching CP-SAT solver for {len(assignments)} assignments...")
    solver = TimetableSolver(
        teaching_assignments=assignments,
        rooms=rooms,
        sections=sections,
        num_periods=9,
        section_parent_map=section_parent_map,
    )

    res = solver.solve(time_limit_seconds=120)

    print(f"\nRESULT:")
    print(f"  Status: {res['status']}")
    print(f"  Solve time: {res['solve_time']}s")
    print(f"  Build time: {res['build_time']}s")
    print(f"  Total sessions: {res['num_sessions']}")
    print(f"  Assignments: {len(res['assignments']) if res['assignments'] else 0}")
    print(f"  Conflicts: {res['num_conflicts']}")
    print(f"  Branches: {res['num_branches']}")

    if res['is_feasible']:
        print("\nSUCCESS! Feasible timetable found for the expanded dataset!")
    else:
        print("\nINFEASIBLE: Investigating...")


if __name__ == "__main__":
    test_expanded_dataset()
