"""
Test script for the 3-tier anti-gap solver defense and compactor pass.
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


def test_gap_compactor_logic():
    print("Testing gap compactor logic...")
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    importer = ImporterService(db)
    importer.process_infra_file("Infra.xlsx", "Infra.xlsx")
    importer.process_load_file("Load.xlsx", "Load.xlsx")

    assignments = db.query(TeachingAssignment).filter_by(is_active=True).all()
    rooms = db.query(Room).filter_by(is_active=True).all()
    sections = db.query(Section).filter_by(is_active=True).all()

    section_parent_map = {
        s.id: s.parent_section_id
        for s in sections
        if s.parent_section_id
    }

    # Simulate an assignment list with an artificial long gap
    # Section 2401 on Wednesday: Class at P1 and Class at P8 (gap P2-P7)
    simulated_assignments = [
        {
            'teaching_assignment_id': assignments[0].id,
            'session_index': 0,
            'day': 'Wednesday',
            'day_index': 2,
            'period_start': 1,
            'period_end': 1,
            'room_id': rooms[0].id,
            'faculty_id': assignments[0].faculty_id,
            'section_id': assignments[0].section_id,
            'subject_id': assignments[0].subject_id,
        },
        {
            'teaching_assignment_id': assignments[1].id,
            'session_index': 0,
            'day': 'Wednesday',
            'day_index': 2,
            'period_start': 8,
            'period_end': 8,
            'room_id': rooms[1].id,
            'faculty_id': assignments[1].faculty_id,
            'section_id': assignments[0].section_id,  # same section 2401
            'subject_id': assignments[1].subject_id,
        }
    ]

    print(f"Before compaction: Section 2401 Wednesday has classes at P{simulated_assignments[0]['period_start']} and P{simulated_assignments[1]['period_start']}")
    
    # We will verify that compactor slides the second class from P8 to P2!
    print("Simulated test ready.")


if __name__ == "__main__":
    test_gap_compactor_logic()
