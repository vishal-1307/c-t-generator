# College Timetable Generator

A production-quality automated timetable scheduling system designed for colleges and universities. Powered by **Google OR-Tools CP-SAT** constraint satisfaction engine, built with **FastAPI** backend and **Next.js** frontend.

---

## Key Features

- **Automated Conflict-Free Scheduling**: Mathematically guaranteed zero hard conflicts using CP-SAT solver
- **Dual-File Import**: Accepts standard `Load.xlsx` (teaching demand) and `Infra.xlsx` (room inventory)
- **Typo Tolerance**: Tolerates legacy column variations (e.g. `Strenght`, double spaces in `Faculty  UID`)
- **Infrastructure Reuse**: Upload infrastructure once, reuse across multiple semesters/runs
- **Lab Group Support**: Automatic parent-child relationship handling prevents students being double-booked
- **BYOD Room Allocation**: Enforces power-charging requirements for laptop-mandatory courses
- **Theory Once-per-Day**: Automatically distributes theory classes across different days
- **Faculty Daily Free Period**: Guarantees faculty members at least one free period on teaching days
- **Multi-Period Sessions**: Seamlessly handles 2-period and 3-period lab/lecture blocks (consecutive on same day)
- **Section Compactness**: Soft optimization penalizes internal gaps in student schedules
- **4 Main Timetable Views**: By Section, By Faculty, By Room, and Master Timetable
- **Professional Excel Export**: Multi-sheet workbook (Master, Section-wise, Faculty-wise, Room-wise)
- **Controlled Manual Editing**: Validated move operations prevent creating conflicts
- **Version Management**: Draft and published states with full run history
- **Read-Only AI Assistant**: Gemini-powered conversational assistant strictly grounded in timetable data

---

## Architecture

```
USER
  ↓
Next.js Frontend (TypeScript + Tailwind CSS + Lucide Icons)
  ↓
FastAPI Backend (Python 3.11+)
  ↓
Google OR-Tools CP-SAT Solver
  ↓
Validated Timetable (SQLite / PostgreSQL)
  ↓
Excel Export / Web Views / Gemini Assistant (Read-Only)
```

> **IMPORTANT**: AI is NOT the scheduler. Google OR-Tools CP-SAT is the deterministic scheduling engine. Gemini AI operates strictly in a read-only capacity through allowlisted backend query tools.

---

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- npm

### 1. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start development server
python run.py
```

Backend will be available at: `http://localhost:8000`  
Interactive API docs at: `http://localhost:8000/docs`

### 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

Frontend will be available at: `http://localhost:3000`

---

## Primary Workflow

```
UPLOAD / REUSE INFRASTRUCTURE
        ↓
UPLOAD TEACHING LOAD (Load.xlsx)
        ↓
ANALYZE & VALIDATE
        ↓
GENERATE TIMETABLE (OR-Tools CP-SAT)
        ↓
VIEW TIMETABLE (Section / Faculty / Room / Master)
        ↓
EXPORT EXCEL
```

---

## File Formats

### Load.xlsx (Canonical Template)
| Column | Description | Example |
|---|---|---|
| `Name of faculty` | Full faculty name | Praveen Kumar |
| `Faculty UID` | Unique faculty identifier | 1234 |
| `Subject Name` | Course title | Embedded systems |
| `Subject Code` | Unique course code | ECE181 |
| `Section` | Section code (4-digit parent, 5-digit lab group) | 2401 |
| `Strength` | Number of enrolled students | 68 |
| `Type` | `Class` (theory) or `Lab` (practical) | Class |
| `BYOD` | `Yes` (requires power points) or `No` | Yes |
| `Duration` | Period length: 1, 2, or 3 periods | 1 |
| `Classes Per Week` | Weekly occurrences | 4 |

### Infra.xlsx (Canonical Template)
| Column | Description | Example |
|---|---|---|
| `Block` | Building/block number | 36 |
| `Room` | Room number (floor auto-derived) | 301 |
| `Strength` | Seating capacity | 72 |
| `Type` | `Classroom` or `Lab` | Classroom |
| `BYOD` | `Yes` (has student charging) or `No` | Yes |

---

## Automated Tests

Run the comprehensive acceptance test suite:

```bash
python test_acceptance.py
```

Tests verify all 12 hard constraints:
1. Faculty no-overlap
2. Room no-overlap
3. Section no-overlap
4. Parent-child lab group clash prohibition
5. Room capacity >= section strength
6. Room type match (Theory → Classroom, Lab → Lab)
7. BYOD requirement satisfaction
8. Multi-period session contiguity
9. Monday-Friday working grid (09:30–17:00, 9 periods)
10. Theory once per day per section
11. Faculty daily free period
12. Exact classes per week satisfaction

---

## Configuration (`.env`)

```env
PROJECT_NAME="College Timetable Generator"
SECRET_KEY="your-secret-key-here"
ACCESS_TOKEN_EXPIRE_MINUTES=11520
DATABASE_URL="sqlite:///./sql_app.db"
# Optional Gemini AI Assistant:
GEMINI_API_KEY="your-gemini-api-key"
```

---

## License

MIT License. Designed for academic and institutional scheduling.
