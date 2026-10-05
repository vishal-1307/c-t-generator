# College Timetable Generator — Complete User & Administration Manual

---

## 1. System Overview & Philosophy

The **College Timetable Generator (CTG)** is a specialized, production-ready web application designed for academic administrators, department heads, and timetable coordinators. 

### Why Deterministic Solving?
Unlike general AI models which can hallucinate room numbers or silently drop required classes, CTG uses **Google OR-Tools CP-SAT** (Constraint Programming - Satisfiability). This ensures:
1. **Mathematical Guarantees**: A generated schedule is strictly conflict-free across faculty, rooms, and sections.
2. **Deterministic Reproducibility**: Given the same constraints and seeds, valid schedules are produced reliably.
3. **AI as an Assistant, Not a Scheduler**: Optional AI capabilities (via Google Gemini) are strictly confined to read-only analytical queries and explanations.

---

## 2. Navigation & User Interface Structure

The application features a clean, professional, collapsible sidebar with two primary areas:

```
[Primary Navigation]
├── Dashboard        — Unified 3-step file upload & generation workflow
├── Generate         — Advanced generation options and solver monitoring
├── Timetable        — Interactive visual schedules
│   ├── By Section   — Class schedules for students
│   ├── By Faculty   — Teaching loads and schedules for teachers
│   └── By Room      — Room utilization and occupancy
└── Export           — Multi-sheet Excel workbook export

[Advanced Navigation (Collapsible)]
├── Validation       — In-depth data sanity checks and readiness status
├── Data             — Direct inspection & management of foundational entities
│   ├── Faculty      — Teacher profiles, active status, UID
│   ├── Subjects     — Courses, durations (1-3 periods), types (Class/Lab)
│   ├── Sections     — Student groups and lab subgroups
│   ├── Rooms        — Inventory, capacities, BYOD status, blocks, floors
│   ├── Time Slots   — Institution period grid (default: 9 periods of 50m)
│   ├── Mappings     — Faculty-Subject-Section load assignments
│   └── Availability — Blackout windows for teachers, rooms, and sections
├── Master Timetable — Searchable, filterable flat table of all scheduled slots
├── Versions         — Draft, Published, and historical schedule versions
└── Users            — Administrator credentials and access controls
```

---

## 3. Data Model & Input Standards

The application requires two standard Excel workbooks. You can download official pre-formatted templates directly from the app.

### 3.1 `Load.xlsx` (Teaching Demand)
Specifies who is assigned to teach what, to whom, and at what frequency.

| Column Header | Type | Required | Description | Example |
|---|---|---|---|---|
| `Name of faculty` | Text | Yes | Full name of the instructor | `Praveen Kumar` |
| `Faculty UID` | Text/Int | Yes | Unique employee or faculty ID | `1234` |
| `Subject Name` | Text | Yes | Course title | `Embedded systems` |
| `Subject Code` | Text | Yes | Unique alphanumeric course identifier | `ECE181` |
| `Section` | Text/Int | Yes | Section identifier. Use 4 digits for parents, 5 for lab groups | `2401` or `24011` |
| `Strength` | Integer | Yes | Enrolled student count in this section | `68` |
| `Type` | Enum | Yes | `Class` for Theory / Lectures; `Lab` for Practicals | `Class` |
| `BYOD` | Enum | Yes | `Yes` if student laptops/chargers are required; `No` otherwise | `Yes` |
| `Duration` | Integer | Yes | Session length in periods: `1` (50m), `2` (100m), or `3` (150m) | `1` |
| `Classes Per Week`| Integer | Yes | Number of times this session occurs weekly | `4` |

#### Key Business Rules for Load:
- **Total Weekly Periods** = `Duration × Classes Per Week`. (e.g., Duration 2 × 2 classes/week = 4 periods/week).
- **Classes Per Week is Required**: The system never assumes or defaults to 4 classes/week.
- **Subject Code Consistency**: The same code must always map to the same name, duration, and type.
- **Parent & Lab Group Linkage**: If section `2401` exists, groups `24011` and `24012` are automatically recognized as subgroups consisting of students from `2401`. The scheduler strictly prohibits scheduling a parent section and its subgroup simultaneously.

---

### 3.2 `Infra.xlsx` (Room Inventory)
Specifies physical classrooms and laboratories available on campus.

| Column Header | Type | Required | Description | Example |
|---|---|---|---|---|
| `Block` | Text/Int | Yes | Building/Block identifier | `36` |
| `Room` | Text/Int | Yes | Room number | `301` |
| `Strength` | Integer | Yes | Maximum student seating capacity | `72` |
| `Type` | Enum | Yes | `Class room` (or `Classroom`) or `Lab` | `Class room` |
| `BYOD` | Enum | Yes | `Yes` if student desk charging outlets exist; `No` otherwise | `Yes` |

#### Key Business Rules for Infrastructure:
- **Floor Derivation**: Floor numbers are automatically derived from the hundreds digit (e.g., Room `301` is Floor 3; Room `102` is Floor 1).
- **Faculty Rooms**: Faculty offices must never be assigned as teaching rooms.
- **Infrastructure Persistence**: Infrastructure can be saved once and reused across different academic terms without re-uploading.

---

## 4. Timetable Solver Logic & Rules

### 4.1 The 12 Load-Bearing Hard Constraints
Every generated timetable mathematically satisfies:
1. **Faculty No-Overlap**: No teacher is assigned to two places at once.
2. **Room No-Overlap**: No room hosts multiple classes in the same period.
3. **Section No-Overlap**: No section has simultaneous conflicting classes.
4. **Lab Group / Parent Clash Prevention**: A parent section (e.g., `2401`) and its child subgroup (e.g., `24011`) share students and can never be scheduled concurrently.
5. **Room Capacity**: Assigned room capacity $\ge$ section strength.
6. **Room Type Match**: Lectures occur only in classrooms; labs occur only in lab rooms.
7. **BYOD Compliance**: BYOD-required courses are assigned only to BYOD-equipped rooms.
8. **Multi-Period Session Contiguity**: 2-period and 3-period sessions are scheduled in consecutive periods on the same day in the same room with the same faculty.
9. **Grid Boundaries**: No multi-period session extends beyond the 9th period.
10. **Theory Once-per-Day**: A section has at most one lecture of a given theory course per day (encouraging optimal student retention).
11. **Faculty Daily Free Period**: Any faculty member teaching on a given day is guaranteed at least one unoccupied period that day.
12. **Mon–Fri Operating Window**: All sessions are strictly placed Monday through Friday within the 9 daily periods.

### 4.2 Soft Objectives (Quality Optimization)
When multiple valid schedules exist, CP-SAT optimizes a weighted penalty function:
- **Section Compactness (Weight = 10)**: Minimizes isolated free periods (gaps) in student daily schedules.
- **Faculty Compactness (Weight = 5)**: Groups teaching periods to prevent scattered teacher schedules.
- **Even Day Distribution (Weight = 3)**: Distributes courses evenly across Monday through Friday.
- **Room Proximity (Weight = 2)**: Prefers rooms in the same building block for a section's consecutive classes.

---

## 5. End-to-End Operational Workflow

### Step 1: Upload Data
1. Navigate to **Dashboard**.
2. Upload `Load.xlsx` via drag-and-drop.
3. Either check **Use Saved Infrastructure** or upload a new `Infra.xlsx`.

### Step 2: Analyze & Sanity Check
1. Click **Analyze Files**.
2. The system executes comprehensive pre-solve validation:
   - Verifies room capacities against section sizes.
   - Verifies classroom and lab counts.
   - Ensures faculty workload does not exceed 40 periods/week.
3. If issues exist, clear explanations are provided (e.g., *"No BYOD classroom with capacity $\ge$ 70 exists"*).

### Step 3: Generation
1. Click **Generate Timetable**.
2. CP-SAT solves the problem in 2–10 seconds.
3. Live solver metrics are displayed: status (`OPTIMAL`/`FEASIBLE`), runtime, and period counts.

### Step 4: Viewing Schedules
- Switch between **By Section**, **By Faculty**, and **By Room** views.
- Unscheduled slots are labeled **No Class**.

### Step 5: Exporting to Excel
1. Click **Export** $\rightarrow$ **Export to Excel**.
2. The downloaded workbook includes:
   - `Master Timetable`: All scheduled slots with column auto-filters.
   - `Section-wise`: Formatted weekly grids for every section.
   - `Faculty-wise`: Weekly grids for each teacher.
   - `Room-wise`: Occupancy grids for campus facilities.

---

## 6. Manual Adjustments & Version Control

- **Manual Edits**: Administrators can preview moves of specific classes to different times or rooms. The backend validates hard constraints in real-time before committing.
- **Versions**: Create multiple draft versions to test scenarios. Once approved, mark a version as **Published** to lock it from inadvertent modifications.

---

## 7. AI Assistant Reference

The application includes an embedded AI assistant powered by Google Gemini.

### Example Permitted Inquiries:
- *"Give me a summary of the generated timetable."*
- *"Why does section 2405 have a gap on Tuesday?"*
- *"Which rooms are available on Wednesday at Period 4?"*
- *"Which teachers are free on Friday during Period 1?"*

### Safety Principles:
- The assistant is strictly **read-only**.
- All responses cite backend facts (e.g., room capacities, slot occupancy).
- The assistant never executes SQL, invents schedule entries, or overrides constraints.
