# Teacher & Administrator Quick Start Guide

## College Timetable Generator (CTG)

Welcome! This guide explains how to generate a college timetable from scratch in under 5 minutes without technical knowledge.

---

## 3-Step Quick Overview

```
Step 1: Upload Files ──▶ Step 2: Analyze ──▶ Step 3: Generate & Export
```

---

## Step 1: Prepare Your Excel Files

You need two files:

### File A: `Load.xlsx` (Teaching Load)
This file tells the system **who teaches what to whom, and how often**.

Download the official template directly from the app (click **Download Load Template** on the dashboard).

Key columns:
- **Faculty UID**: Unique ID of the teacher (e.g. `1234`)
- **Name of faculty**: Teacher's full name
- **Subject Code**: Short code (e.g. `ECE181`)
- **Subject Name**: Full title (e.g. `Embedded systems`)
- **Section**: Section number (e.g. `2401`). If it is a lab group, add a 5th digit (e.g. `24011`)
- **Strength**: Number of students in this class
- **Type**: Enter `Class` for lectures, `Lab` for practicals
- **BYOD**: Enter `Yes` if students need laptops with charging points, otherwise `No`
- **Duration**: `1` for single period, `2` for double period, `3` for 3-period lab block
- **Classes Per Week**: How many times this session meets every week

> **Tip**: Weekly teaching load = `Duration × Classes Per Week`. If a 2-period lab meets twice a week, that is 4 periods total.

### File B: `Infra.xlsx` (Room Inventory)
This file tells the system **which rooms exist, their capacity, and capabilities**.

Key columns:
- **Block**: Building block (e.g. `36`, `33`)
- **Room**: Room number (e.g. `301`, `102`)
- **Strength**: Maximum seating capacity
- **Type**: `Class room` (or `Classroom`) or `Lab`
- **BYOD**: `Yes` if the room has student power outlets, otherwise `No`

> **Good News**: You only need to upload `Infra.xlsx` once! The system remembers your rooms. Next semester, just click **Use Saved Infrastructure**.

---

## Step 2: Generate the Timetable

1. Open the application at `http://localhost:3000` (or your deployed URL)
2. Log in with your administrator credentials (default: `admin` / `admin123`)
3. On the **Dashboard**:
   - Drag and drop your `Load.xlsx` into the Step 1 box
   - Under Step 2, click **Use Saved Infrastructure** (or upload `Infra.xlsx` if first time)
4. Click the large blue **Analyze Files** button
5. Review the validation screen:
   - ✅ **Green checkmarks**: Data is ready to generate
   - ⚠️ **Amber warnings**: Non-critical notices (e.g., heavy faculty load)
   - ❌ **Red errors**: Must be fixed in Excel before generating
6. If green, click **Generate Timetable**
7. Wait 5–15 seconds while Google OR-Tools computes the optimal conflict-free schedule!
8. When complete, you will see:
   ```
   ✓ Timetable Generated Successfully
   - Total Classes: 30
   - Total Periods: 40
   - Hard Conflicts: 0
   - Solver Status: OPTIMAL
   - Generation Time: 2.9s
   ```

---

## Step 3: View & Share

### Viewing Schedules
Click **View Timetable** in the top navigation:
- **By Section**: Select any section (e.g. `2401`) to see its weekly grid. Lab groups are clearly shown.
- **By Faculty**: Select any teacher to view their personal weekly timetable. Guaranteed to have free periods.
- **By Room**: View room occupancy grids to check room utilization.

> Empty slots are displayed clearly as **No Class** (never blank or confusing).

### Exporting to Excel
1. Click **Export** in the sidebar
2. Click **Export to Excel**
3. Open the downloaded file in Microsoft Excel:
   - **Sheet 1 (Master)**: All assignments in one filterable table
   - **Sheet 2 (Section-wise)**: Ready-to-print schedules for each section
   - **Sheet 3 (Faculty-wise)**: Individual timetables for each faculty member
   - **Sheet 4 (Room-wise)**: Weekly occupancy for each room

---

## Need Help? Ask the AI Assistant

Click the **AI Assistant** button in the bottom right corner of any page. You can ask natural questions like:
- *"Give me a summary of the generated timetable"*
- *"Which rooms are available on Tuesday at Period 3?"*
- *"Does section 2401 have any gaps on Wednesday?"*
- *"How many classes does Praveen Kumar teach?"*

The assistant only uses verified timetable data and will never guess or invent information.
