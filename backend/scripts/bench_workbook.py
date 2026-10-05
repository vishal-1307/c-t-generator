"""How the workbook importer behaves at institution scale.

The demo workbook is 386 rows. A university's is not. This builds a synthetic
one at a realistic size and measures analyse and apply, plus the query counts -
because a linear-looking wall time can still hide a per-row query that only
bites on a bigger machine.
"""
import io
import sys
import time

sys.path.insert(0, ".")

from openpyxl import Workbook
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.bulk_import import analyse_workbook, apply_workbook
from app.database import Base

LAB_TYPES = ["programming", "cybersecurity", "iot", "networking", "electronics"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def build(faculty_n, subject_n, room_n, section_n, periods=9):
    """A workbook shaped like a real institution's, at a chosen size."""
    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("01_Academic_Context")
    ws.append(["academic_year", "semester", "program", "department"])
    ws.append(["2026-27", 5, "BCA", "Computer Applications"])

    ws = wb.create_sheet("09_Time_Slots")
    ws.append(["period_code", "day", "start_time", "end_time", "is_lunch"])
    for day in DAYS:
        for p in range(1, periods + 1):
            start = 9 * 60 + 30 + (p - 1) * 50
            end = start + 50
            ws.append([
                f"P{p}", day,
                f"{start // 60:02d}:{start % 60:02d}",
                f"{end // 60:02d}:{end % 60:02d}",
                "Yes" if p == 5 else "No",
            ])

    ws = wb.create_sheet("03_Rooms")
    ws.append(["block", "floor", "room_number", "room_code", "capacity",
               "room_type", "lab_type", "BYOD", "charging", "is_active"])
    for i in range(room_n):
        block = 36 + (i // 200)
        floor = (i % 200) // 50 + 1
        number = 100 * floor + (i % 50) + 1
        is_lab = i % 4 == 0
        ws.append([
            str(block), str(floor), str(number), f"{block}-{number}",
            70 if not is_lab else 40,
            "lab" if is_lab else "classroom",
            LAB_TYPES[i % len(LAB_TYPES)] if is_lab else "",
            "Yes", "Yes", "Yes",
        ])

    ws = wb.create_sheet("04_Faculty")
    ws.append(["faculty_id", "faculty_name", "department", "is_active"])
    for i in range(faculty_n):
        ws.append([f"T-{i:05d}", f"Dr Faculty {i}", "CSE", "Yes"])

    ws = wb.create_sheet("05_Subjects")
    ws.append(["subject_code", "subject_name", "delivery_type", "lecture_per_week",
               "practical_per_week", "required_lab_type", "BYOD_required",
               "charging_required", "is_active"])
    for i in range(subject_n):
        mixed = i % 3 == 0
        ws.append([
            f"SUB{i:05d}", f"Subject {i}",
            "mixed" if mixed else "theory",
            2 if mixed else 3,
            2 if mixed else 0,
            LAB_TYPES[i % len(LAB_TYPES)] if mixed else "",
            "Yes" if mixed else "No",
            "Yes" if mixed else "No",
            "Yes",
        ])

    ws = wb.create_sheet("06_Sections")
    ws.append(["section_number", "batch", "program", "department", "strength",
               "academic_year", "semester", "is_active"])
    for i in range(section_n):
        ws.append([f"S{i:05d}", "BCA-2024", "BCA", "Computer Applications",
                   60 + (i % 11), "2026-27", 5, "Yes"])

    ws = wb.create_sheet("07_Faculty_Subject")
    ws.append(["faculty_id", "subject_code"])
    fs_rows = 0
    for i in range(faculty_n):
        for k in range(3):  # each teacher qualified for three subjects
            ws.append([f"T-{i:05d}", f"SUB{(i * 3 + k) % subject_n:05d}"])
            fs_rows += 1

    ws = wb.create_sheet("08_Section_Subject")
    ws.append(["section_number", "subject_code"])
    ss_rows = 0
    for i in range(section_n):
        for k in range(7):  # a seven-subject semester
            ws.append([f"S{i:05d}", f"SUB{(i * 7 + k) % subject_n:05d}"])
            ss_rows += 1

    ws = wb.create_sheet("11_Batches")
    ws.append(["section_number", "group_code", "group_strength"])
    for i in range(section_n):
        ws.append([f"S{i:05d}", "G1", 30])
        ws.append([f"S{i:05d}", "G2", 30])

    ws = wb.create_sheet("10_Availability")
    ws.append(["resource_type", "resource_id", "day", "period_code", "reason"])
    for i in range(0, faculty_n, 5):
        ws.append(["faculty", f"T-{i:05d}", DAYS[i % 5], f"P{(i % 9) + 1}", "research"])

    total = (
        1 + len(DAYS) * periods + room_n + faculty_n + subject_n + section_n
        + fs_rows + ss_rows + section_n * 2 + (faculty_n + 4) // 5
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), total


def measure(label, faculty_n, subject_n, room_n, section_n):
    data, rows = build(faculty_n, subject_n, room_n, section_n)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    queries = []

    def record(conn, cursor, statement, params, context, executemany):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", record)

    t0 = time.perf_counter()
    preview = analyse_workbook(db, data, "bench.xlsx")
    t_analyse = time.perf_counter() - t0
    q_analyse = len(queries)

    queries.clear()
    t0 = time.perf_counter()
    result = apply_workbook(db, data, "bench.xlsx")
    t_apply = time.perf_counter() - t0
    q_apply = len(queries)

    event.remove(engine, "before_cursor_execute", record)

    print(
        f"{label:12s} {rows:7,d} rows | "
        f"analyse {t_analyse:6.2f}s / {q_analyse:6,d} q | "
        f"apply {t_apply:6.2f}s / {q_apply:7,d} q | "
        f"ok={not preview.has_errors} committed={result.committed}"
    )
    db.close()
    engine.dispose()
    return rows, t_analyse, t_apply


if __name__ == "__main__":
    print("workbook importer at increasing scale\n")
    print(f"{'size':12s} {'rows':>7s}   analyse            apply")
    small = measure("demo-ish", 25, 16, 85, 19)
    medium = measure("department", 200, 150, 300, 120)
    large = measure("faculty-wide", 800, 600, 900, 500)

    print()
    for name, (rows, ta, tap) in [("medium/small", (medium[0] / small[0], medium[1] / small[1], medium[2] / small[2])),
                                  ("large/medium", (large[0] / medium[0], large[1] / medium[1], large[2] / medium[2]))]:
        print(f"{name}: rows x{rows:.1f}  analyse x{ta:.1f}  apply x{tap:.1f}")
    print("\n(linear or better means no per-row query crept in)")
