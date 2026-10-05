"""
Synthetic Test Dataset Generator for College Timetable Generator.

Generates comprehensive, realistic synthetic test datasets (Synthetic_Load.xlsx and Synthetic_Infra.xlsx)
that thoroughly stress-test every constraint and edge case:
- Multiple faculty with varying workloads (1 to 5 courses)
- Multiple sections (undergrad, postgrad, mixed sizes 30 to 120 students)
- Lab groups with parent-child relationships (e.g. 2101 -> 21011, 21012)
- High room contention (limited large halls, limited specialized labs)
- Faculty contention (shared specialized faculty across sections)
- BYOD requirements mixed across theory and labs
- Varied durations: 1-period theory, 2-period labs, 3-period workshops
- Varied frequencies: 1, 2, 3, 4, and 5 classes per week
- Theory once-per-day enforcement
- Labs multiple times per day permitted
- Faculty daily free period guarantee
- Block distribution for proximity testing (Blocks 10, 20, 30)
"""

import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

FONT_NAME = "Segoe UI"
HEADER_FILL = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
HEADER_FONT = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")
REGULAR_FONT = Font(name=FONT_NAME, size=10)
THIN_BORDER = Border(
    left=Side(style='thin', color='D0D5DD'),
    right=Side(style='thin', color='D0D5DD'),
    top=Side(style='thin', color='D0D5DD'),
    bottom=Side(style='thin', color='D0D5DD'),
)


def generate_synthetic_load(filepath="Synthetic_Load.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Teaching Load"

    headers = [
        "Name of faculty", "Faculty UID", "Subject Name", "Subject Code",
        "Section", "Strength", "Type", "BYOD", "Duration", "Classes Per Week"
    ]
    for col_idx, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border = THIN_BORDER

    # Synthetic rows designed to test every feature
    data = [
        # --- Section 2101 (Large Section: 75 students) ---
        ["Dr. Alok Verma", "101", "Advanced Data Structures", "CS201", "2101", 75, "Class", "Yes", 1, 4],
        ["Prof. Sunita Roy", "102", "Operating Systems", "CS202", "2101", 75, "Class", "No", 1, 4],
        ["Dr. Vikram Seth", "103", "Computer Networks", "CS203", "2101", 75, "Class", "Yes", 1, 3],
        ["Prof. Meera Sen", "104", "Database Management", "CS204", "2101", 75, "Class", "No", 1, 3],
        # Subgroups of 2101 for OS Lab and DBMS Lab (Half strength: 38 students)
        ["Prof. Sunita Roy", "102", "Operating Systems Lab", "CS202L", "21011", 38, "Lab", "Yes", 2, 2],
        ["Prof. Sunita Roy", "102", "Operating Systems Lab", "CS202L", "21012", 37, "Lab", "Yes", 2, 2],
        ["Prof. Meera Sen", "104", "Database Lab", "CS204L", "21011", 38, "Lab", "Yes", 3, 1],
        ["Prof. Meera Sen", "104", "Database Lab", "CS204L", "21012", 37, "Lab", "Yes", 3, 1],

        # --- Section 2102 (Medium Section: 60 students) ---
        ["Dr. Alok Verma", "101", "Advanced Data Structures", "CS201", "2102", 60, "Class", "Yes", 1, 4],
        ["Dr. Rajesh Gupta", "105", "Discrete Mathematics", "MA201", "2102", 60, "Class", "No", 1, 5],
        ["Prof. Sunita Roy", "102", "Operating Systems", "CS202", "2102", 60, "Class", "No", 1, 3],
        ["Dr. Vikram Seth", "103", "Computer Networks", "CS203", "2102", 60, "Class", "Yes", 1, 4],
        # Subgroups of 2102
        ["Dr. Alok Verma", "101", "Data Structures Lab", "CS201L", "21021", 30, "Lab", "Yes", 2, 2],
        ["Dr. Alok Verma", "101", "Data Structures Lab", "CS201L", "21022", 30, "Lab", "Yes", 2, 2],

        # --- Section 2103 (Mega Section: 110 students, tests large capacity rooms) ---
        ["Dr. Rajesh Gupta", "105", "Linear Algebra & Probability", "MA202", "2103", 110, "Class", "No", 2, 2],
        ["Prof. Kabir Das", "106", "Digital Electronics", "EC201", "2103", 110, "Class", "Yes", 1, 4],
        ["Dr. Ananya Roy", "107", "Software Engineering", "CS205", "2103", 110, "Class", "Yes", 1, 3],
        # Lab subgroup for Electronics
        ["Prof. Kabir Das", "106", "Digital Electronics Lab", "EC201L", "21031", 55, "Lab", "No", 2, 2],
        ["Prof. Kabir Das", "106", "Digital Electronics Lab", "EC201L", "21032", 55, "Lab", "No", 2, 2],
    ]

    for row_idx, row_data in enumerate(data, 2):
        for col_idx, val in enumerate(row_data, 1):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.font = REGULAR_FONT
            c.border = THIN_BORDER
            if col_idx in (2, 5, 6, 7, 8, 9, 10):
                c.alignment = Alignment(horizontal='center', vertical='center')

    for col in ws.columns:
        ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = 18

    wb.save(filepath)
    print(f"Synthetic Load written to: {filepath} ({len(data)} teaching assignments)")


def generate_synthetic_infra(filepath="Synthetic_Infra.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Infrastructure"

    headers = ["Block", "Room", "Strength", "Type", "BYOD"]
    for col_idx, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col_idx, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border = THIN_BORDER

    # Synthetic room inventory across 3 blocks
    rooms = [
        # Block 10 (Main Academic Block - Classrooms)
        ["10", "101", 80, "Classroom", "Yes"],
        ["10", "102", 80, "Classroom", "No"],
        ["10", "103", 65, "Classroom", "Yes"],
        ["10", "104", 65, "Classroom", "No"],
        ["10", "201", 120, "Classroom", "Yes"],  # Large Lecture Hall
        ["10", "202", 120, "Classroom", "No"],   # Large Lecture Hall
        ["10", "301", 90, "Classroom", "Yes"],
        ["10", "302", 90, "Classroom", "No"],

        # Block 20 (Computing Complex - Computer Labs)
        ["20", "101", 45, "Lab", "Yes"],
        ["20", "102", 45, "Lab", "Yes"],
        ["20", "103", 40, "Lab", "Yes"],
        ["20", "104", 40, "Lab", "Yes"],
        ["20", "201", 60, "Lab", "Yes"],        # Higher capacity lab
        ["20", "202", 60, "Lab", "Yes"],

        # Block 30 (Hardware & Electronics Labs)
        ["30", "101", 60, "Lab", "No"],         # Hardware Lab without BYOD
        ["30", "102", 60, "Lab", "No"],
    ]

    for row_idx, row_data in enumerate(rooms, 2):
        for col_idx, val in enumerate(row_data, 1):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.font = REGULAR_FONT
            c.border = THIN_BORDER
            c.alignment = Alignment(horizontal='center', vertical='center')

    for col in ws.columns:
        ws.column_dimensions[openpyxl.utils.get_column_letter(col[0].column)].width = 16

    wb.save(filepath)
    print(f"Synthetic Infra written to: {filepath} ({len(rooms)} rooms)")


if __name__ == "__main__":
    generate_synthetic_load()
    generate_synthetic_infra()
