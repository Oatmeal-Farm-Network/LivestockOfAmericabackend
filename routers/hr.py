"""
routers/hr.py
Full HR & Workforce Management — employees, attendance, tasks,
leave requests, pay periods, pay slips, certifications.

Mount: app.include_router(hr.router)
All routes require ?business_id=<int> query param (or body field).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db
from business_access import require_business, require_business_body
from typing import Optional
from datetime import date, datetime
from fastapi.responses import StreamingResponse
import csv
import io

router = APIRouter(prefix="/api/hr", tags=["hr"])
_tables_ready = False


# ── Schema bootstrap ──────────────────────────────────────────────────────────

def _ensure_tables(db: Session):
    global _tables_ready
    if _tables_ready:
        return

    stmts = [
        # Employees
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HREmployee')
        CREATE TABLE HREmployee (
            EmployeeID           INT IDENTITY PRIMARY KEY,
            BusinessID           INT NOT NULL,
            FirstName            NVARCHAR(100) NOT NULL,
            LastName             NVARCHAR(100) NOT NULL,
            Email                NVARCHAR(200) NULL,
            Phone                NVARCHAR(50)  NULL,
            DateOfBirth          DATE          NULL,
            HireDate             DATE          NOT NULL,
            TerminationDate      DATE          NULL,
            IsActive             BIT           NOT NULL DEFAULT 1,
            EmploymentType       NVARCHAR(30)  NOT NULL DEFAULT 'full_time',
            JobTitle             NVARCHAR(200) NULL,
            Department           NVARCHAR(100) NULL,
            PayType              NVARCHAR(20)  NOT NULL DEFAULT 'hourly',
            HourlyRate           DECIMAL(10,2) NULL,
            SalaryRate           DECIMAL(10,2) NULL,
            PieceRateUnit        NVARCHAR(50)  NULL,
            PieceRateAmount      DECIMAL(10,2) NULL,
            PaySchedule          NVARCHAR(20)  NOT NULL DEFAULT 'biweekly',
            EmergencyContactName NVARCHAR(200) NULL,
            EmergencyContactPhone NVARCHAR(50) NULL,
            AddressLine          NVARCHAR(300) NULL,
            AddressCity          NVARCHAR(100) NULL,
            AddressState         NVARCHAR(100) NULL,
            Notes                NVARCHAR(MAX) NULL,
            CreatedAt            DATETIME2     DEFAULT GETDATE(),
            UpdatedAt            DATETIME2     DEFAULT GETDATE()
        )""",

        # Certifications
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HRCertification')
        CREATE TABLE HRCertification (
            CertID      INT IDENTITY PRIMARY KEY,
            EmployeeID  INT NOT NULL,
            BusinessID  INT NOT NULL,
            CertName    NVARCHAR(200) NOT NULL,
            IssuedBy    NVARCHAR(200) NULL,
            IssuedDate  DATE NULL,
            ExpiryDate  DATE NULL,
            DocumentURL NVARCHAR(500) NULL,
            Notes       NVARCHAR(500) NULL,
            CreatedAt   DATETIME2 DEFAULT GETDATE()
        )""",

        # Attendance
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HRAttendance')
        CREATE TABLE HRAttendance (
            AttendanceID  INT IDENTITY PRIMARY KEY,
            EmployeeID    INT NOT NULL,
            BusinessID    INT NOT NULL,
            WorkDate      DATE NOT NULL,
            CheckInTime   NVARCHAR(10) NULL,
            CheckOutTime  NVARCHAR(10) NULL,
            BreakMinutes  INT NOT NULL DEFAULT 0,
            HoursWorked   DECIMAL(5,2) NULL,
            FieldID       INT NULL,
            Activity      NVARCHAR(300) NULL,
            Notes         NVARCHAR(500) NULL,
            CreatedAt     DATETIME2 DEFAULT GETDATE()
        )""",

        # Task assignments
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HRTask')
        CREATE TABLE HRTask (
            TaskID             INT IDENTITY PRIMARY KEY,
            BusinessID         INT NOT NULL,
            Title              NVARCHAR(300) NOT NULL,
            Description        NVARCHAR(MAX) NULL,
            AssignedToEmployeeID INT NULL,
            AssignedByPeopleID INT NULL,
            FieldID            INT NULL,
            DueDate            DATE NULL,
            Status             NVARCHAR(20) NOT NULL DEFAULT 'pending',
            Priority           NVARCHAR(10) NOT NULL DEFAULT 'normal',
            Location           NVARCHAR(300) NULL,
            SafetyNotes        NVARCHAR(500) NULL,
            CompletedAt        DATETIME2 NULL,
            Notes              NVARCHAR(MAX) NULL,
            CreatedAt          DATETIME2 DEFAULT GETDATE(),
            UpdatedAt          DATETIME2 DEFAULT GETDATE()
        )""",

        # Leave requests
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HRLeave')
        CREATE TABLE HRLeave (
            LeaveID            INT IDENTITY PRIMARY KEY,
            EmployeeID         INT NOT NULL,
            BusinessID         INT NOT NULL,
            LeaveType          NVARCHAR(30) NOT NULL DEFAULT 'vacation',
            StartDate          DATE NOT NULL,
            EndDate            DATE NOT NULL,
            DaysRequested      DECIMAL(5,1) NOT NULL DEFAULT 1,
            Status             NVARCHAR(20) NOT NULL DEFAULT 'pending',
            Notes              NVARCHAR(500) NULL,
            ReviewedByPeopleID INT NULL,
            ReviewedAt         DATETIME2 NULL,
            CreatedAt          DATETIME2 DEFAULT GETDATE()
        )""",

        # Pay periods
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HRPayPeriod')
        CREATE TABLE HRPayPeriod (
            PayPeriodID  INT IDENTITY PRIMARY KEY,
            BusinessID   INT NOT NULL,
            StartDate    DATE NOT NULL,
            EndDate      DATE NOT NULL,
            Status       NVARCHAR(20) NOT NULL DEFAULT 'open',
            TotalGross   DECIMAL(12,2) NULL,
            TotalNet     DECIMAL(12,2) NULL,
            Notes        NVARCHAR(500) NULL,
            CreatedAt    DATETIME2 DEFAULT GETDATE()
        )""",

        # Pay slips
        """IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='HRPaySlip')
        CREATE TABLE HRPaySlip (
            PaySlipID        INT IDENTITY PRIMARY KEY,
            EmployeeID       INT NOT NULL,
            PayPeriodID      INT NOT NULL,
            BusinessID       INT NOT NULL,
            RegularHours     DECIMAL(7,2) NOT NULL DEFAULT 0,
            OvertimeHours    DECIMAL(7,2) NOT NULL DEFAULT 0,
            PieceRateUnits   DECIMAL(10,2) NOT NULL DEFAULT 0,
            GrossPay         DECIMAL(10,2) NOT NULL DEFAULT 0,
            FederalTax       DECIMAL(10,2) NOT NULL DEFAULT 0,
            StateTax         DECIMAL(10,2) NOT NULL DEFAULT 0,
            SocialSecurity   DECIMAL(10,2) NOT NULL DEFAULT 0,
            Medicare         DECIMAL(10,2) NOT NULL DEFAULT 0,
            OtherDeductions  DECIMAL(10,2) NOT NULL DEFAULT 0,
            NetPay           DECIMAL(10,2) NOT NULL DEFAULT 0,
            PaymentDate      DATE NULL,
            PaymentMethod    NVARCHAR(50) NULL,
            Notes            NVARCHAR(500) NULL,
            CreatedAt        DATETIME2 DEFAULT GETDATE()
        )""",
    ]

    for stmt in stmts:
        try:
            db.execute(text(stmt))
        except Exception:
            pass
    db.commit()
    _tables_ready = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(r):
    return dict(r._mapping)

def _rows(rs):
    return [dict(r._mapping) for r in rs]

def _not_found(label="Record"):
    raise HTTPException(status_code=404, detail=f"{label} not found")


# ═══════════════════════════════════════════════════════════════════════════════
#  EMPLOYEES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/employees", dependencies=[Depends(require_business)])
def list_employees(
    business_id: int = Query(...),
    active_only: bool = Query(True),
    department: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    where = "WHERE BusinessID = :bid"
    params: dict = {"bid": business_id}
    if active_only:
        where += " AND IsActive = 1"
    if department:
        where += " AND Department = :dept"
        params["dept"] = department
    rows = db.execute(text(f"""
        SELECT EmployeeID, FirstName, LastName, Email, Phone, JobTitle, Department,
               EmploymentType, PayType, HourlyRate, SalaryRate, PaySchedule,
               HireDate, TerminationDate, IsActive, Notes, CreatedAt
        FROM HREmployee {where}
        ORDER BY LastName, FirstName
    """), params).fetchall()
    return _rows(rows)


@router.get("/employees/{employee_id}", dependencies=[Depends(require_business)])
def get_employee(employee_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    r = db.execute(text(
        "SELECT * FROM HREmployee WHERE EmployeeID=:eid AND BusinessID=:bid"
    ), {"eid": employee_id, "bid": business_id}).fetchone()
    if not r:
        _not_found("Employee")
    return _row(r)


@router.post("/employees", dependencies=[Depends(require_business_body)])
def create_employee(body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    r = db.execute(text("""
        INSERT INTO HREmployee
          (BusinessID, FirstName, LastName, Email, Phone, DateOfBirth,
           HireDate, EmploymentType, JobTitle, Department, PayType,
           HourlyRate, SalaryRate, PieceRateUnit, PieceRateAmount, PaySchedule,
           EmergencyContactName, EmergencyContactPhone,
           AddressLine, AddressCity, AddressState, Notes)
        OUTPUT INSERTED.EmployeeID
        VALUES
          (:bid, :fn, :ln, :email, :phone, :dob,
           :hire, :etype, :title, :dept, :ptype,
           :hr, :sr, :pru, :pra, :psched,
           :ecn, :ecp, :addr, :city, :state, :notes)
    """), {
        "bid":   body["business_id"],
        "fn":    body["first_name"],
        "ln":    body["last_name"],
        "email": body.get("email"),
        "phone": body.get("phone"),
        "dob":   body.get("date_of_birth"),
        "hire":  body["hire_date"],
        "etype": body.get("employment_type", "full_time"),
        "title": body.get("job_title"),
        "dept":  body.get("department"),
        "ptype": body.get("pay_type", "hourly"),
        "hr":    body.get("hourly_rate"),
        "sr":    body.get("salary_rate"),
        "pru":   body.get("piece_rate_unit"),
        "pra":   body.get("piece_rate_amount"),
        "psched":body.get("pay_schedule", "biweekly"),
        "ecn":   body.get("emergency_contact_name"),
        "ecp":   body.get("emergency_contact_phone"),
        "addr":  body.get("address_line"),
        "city":  body.get("address_city"),
        "state": body.get("address_state"),
        "notes": body.get("notes"),
    })
    db.commit()
    return {"employee_id": r.scalar()}


@router.put("/employees/{employee_id}", dependencies=[Depends(require_business_body)])
def update_employee(employee_id: int, body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    db.execute(text("""
        UPDATE HREmployee SET
          FirstName=:fn, LastName=:ln, Email=:email, Phone=:phone,
          DateOfBirth=:dob, TerminationDate=:term, IsActive=:active,
          EmploymentType=:etype, JobTitle=:title, Department=:dept,
          PayType=:ptype, HourlyRate=:hr, SalaryRate=:sr,
          PieceRateUnit=:pru, PieceRateAmount=:pra, PaySchedule=:psched,
          EmergencyContactName=:ecn, EmergencyContactPhone=:ecp,
          AddressLine=:addr, AddressCity=:city, AddressState=:state,
          Notes=:notes, UpdatedAt=GETDATE()
        WHERE EmployeeID=:eid AND BusinessID=:bid
    """), {
        "eid":   employee_id,
        "bid":   body["business_id"],
        "fn":    body.get("first_name"),
        "ln":    body.get("last_name"),
        "email": body.get("email"),
        "phone": body.get("phone"),
        "dob":   body.get("date_of_birth"),
        "term":  body.get("termination_date"),
        "active":body.get("is_active", 1),
        "etype": body.get("employment_type", "full_time"),
        "title": body.get("job_title"),
        "dept":  body.get("department"),
        "ptype": body.get("pay_type", "hourly"),
        "hr":    body.get("hourly_rate"),
        "sr":    body.get("salary_rate"),
        "pru":   body.get("piece_rate_unit"),
        "pra":   body.get("piece_rate_amount"),
        "psched":body.get("pay_schedule", "biweekly"),
        "ecn":   body.get("emergency_contact_name"),
        "ecp":   body.get("emergency_contact_phone"),
        "addr":  body.get("address_line"),
        "city":  body.get("address_city"),
        "state": body.get("address_state"),
        "notes": body.get("notes"),
    })
    db.commit()
    return {"ok": True}


@router.delete("/employees/{employee_id}", dependencies=[Depends(require_business)])
def deactivate_employee(employee_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    db.execute(text(
        "UPDATE HREmployee SET IsActive=0, TerminationDate=CAST(GETDATE() AS DATE), UpdatedAt=GETDATE() "
        "WHERE EmployeeID=:eid AND BusinessID=:bid"
    ), {"eid": employee_id, "bid": business_id})
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  CERTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/employees/{employee_id}/certifications", dependencies=[Depends(require_business)])
def list_certifications(employee_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    rows = db.execute(text(
        "SELECT * FROM HRCertification WHERE EmployeeID=:eid AND BusinessID=:bid ORDER BY ExpiryDate"
    ), {"eid": employee_id, "bid": business_id}).fetchall()
    return _rows(rows)


@router.post("/employees/{employee_id}/certifications", dependencies=[Depends(require_business_body)])
def add_certification(employee_id: int, body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    r = db.execute(text("""
        INSERT INTO HRCertification (EmployeeID, BusinessID, CertName, IssuedBy, IssuedDate, ExpiryDate, DocumentURL, Notes)
        OUTPUT INSERTED.CertID
        VALUES (:eid, :bid, :name, :by, :issued, :expiry, :doc, :notes)
    """), {
        "eid":    employee_id,
        "bid":    body["business_id"],
        "name":   body["cert_name"],
        "by":     body.get("issued_by"),
        "issued": body.get("issued_date"),
        "expiry": body.get("expiry_date"),
        "doc":    body.get("document_url"),
        "notes":  body.get("notes"),
    })
    db.commit()
    return {"cert_id": r.scalar()}


@router.delete("/certifications/{cert_id}", dependencies=[Depends(require_business)])
def delete_certification(cert_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    db.execute(text("DELETE FROM HRCertification WHERE CertID=:cid AND BusinessID=:bid"),
               {"cid": cert_id, "bid": business_id})
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/attendance", dependencies=[Depends(require_business)])
def list_attendance(
    business_id: int = Query(...),
    employee_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    where = "WHERE a.BusinessID=:bid"
    params: dict = {"bid": business_id}
    if employee_id:
        where += " AND a.EmployeeID=:eid"
        params["eid"] = employee_id
    if start_date:
        where += " AND a.WorkDate >= :sd"
        params["sd"] = start_date
    if end_date:
        where += " AND a.WorkDate <= :ed"
        params["ed"] = end_date
    rows = db.execute(text(f"""
        SELECT a.*, e.FirstName + ' ' + e.LastName AS EmployeeName
        FROM HRAttendance a
        JOIN HREmployee e ON e.EmployeeID = a.EmployeeID
        {where}
        ORDER BY a.WorkDate DESC, e.LastName
    """), params).fetchall()
    return _rows(rows)


@router.post("/attendance", dependencies=[Depends(require_business_body)])
def log_attendance(body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    # Auto-calculate hours if check-in/out provided
    hours = body.get("hours_worked")
    if not hours and body.get("check_in_time") and body.get("check_out_time"):
        try:
            ci = datetime.strptime(body["check_in_time"], "%H:%M")
            co = datetime.strptime(body["check_out_time"], "%H:%M")
            diff = (co - ci).seconds / 3600 - (body.get("break_minutes", 0) / 60)
            hours = round(max(diff, 0), 2)
        except Exception:
            pass
    r = db.execute(text("""
        INSERT INTO HRAttendance
          (EmployeeID, BusinessID, WorkDate, CheckInTime, CheckOutTime,
           BreakMinutes, HoursWorked, FieldID, Activity, Notes)
        OUTPUT INSERTED.AttendanceID
        VALUES (:eid, :bid, :dt, :ci, :co, :brk, :hrs, :fid, :act, :notes)
    """), {
        "eid":   body["employee_id"],
        "bid":   body["business_id"],
        "dt":    body["work_date"],
        "ci":    body.get("check_in_time"),
        "co":    body.get("check_out_time"),
        "brk":   body.get("break_minutes", 0),
        "hrs":   hours,
        "fid":   body.get("field_id"),
        "act":   body.get("activity"),
        "notes": body.get("notes"),
    })
    db.commit()
    return {"attendance_id": r.scalar()}


@router.put("/attendance/{attendance_id}", dependencies=[Depends(require_business_body)])
def update_attendance(attendance_id: int, body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    hours = body.get("hours_worked")
    if not hours and body.get("check_in_time") and body.get("check_out_time"):
        try:
            ci = datetime.strptime(body["check_in_time"], "%H:%M")
            co = datetime.strptime(body["check_out_time"], "%H:%M")
            diff = (co - ci).seconds / 3600 - (body.get("break_minutes", 0) / 60)
            hours = round(max(diff, 0), 2)
        except Exception:
            pass
    db.execute(text("""
        UPDATE HRAttendance SET
          WorkDate=:dt, CheckInTime=:ci, CheckOutTime=:co,
          BreakMinutes=:brk, HoursWorked=:hrs, FieldID=:fid,
          Activity=:act, Notes=:notes
        WHERE AttendanceID=:aid AND BusinessID=:bid
    """), {
        "aid":   attendance_id,
        "bid":   body["business_id"],
        "dt":    body.get("work_date"),
        "ci":    body.get("check_in_time"),
        "co":    body.get("check_out_time"),
        "brk":   body.get("break_minutes", 0),
        "hrs":   hours,
        "fid":   body.get("field_id"),
        "act":   body.get("activity"),
        "notes": body.get("notes"),
    })
    db.commit()
    return {"ok": True}


@router.delete("/attendance/{attendance_id}", dependencies=[Depends(require_business)])
def delete_attendance(attendance_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    db.execute(text("DELETE FROM HRAttendance WHERE AttendanceID=:aid AND BusinessID=:bid"),
               {"aid": attendance_id, "bid": business_id})
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  TASKS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/tasks", dependencies=[Depends(require_business)])
def list_tasks(
    business_id: int = Query(...),
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    where = "WHERE t.BusinessID=:bid"
    params: dict = {"bid": business_id}
    if employee_id:
        where += " AND t.AssignedToEmployeeID=:eid"
        params["eid"] = employee_id
    if status:
        where += " AND t.Status=:st"
        params["st"] = status
    rows = db.execute(text(f"""
        SELECT t.*,
               e.FirstName + ' ' + e.LastName AS AssigneeName
        FROM HRTask t
        LEFT JOIN HREmployee e ON e.EmployeeID = t.AssignedToEmployeeID
        {where}
        ORDER BY
          CASE t.Priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
          t.DueDate, t.CreatedAt DESC
    """), params).fetchall()
    return _rows(rows)


@router.post("/tasks", dependencies=[Depends(require_business_body)])
def create_task(body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    r = db.execute(text("""
        INSERT INTO HRTask
          (BusinessID, Title, Description, AssignedToEmployeeID, AssignedByPeopleID,
           FieldID, DueDate, Status, Priority, Location, SafetyNotes, Notes)
        OUTPUT INSERTED.TaskID
        VALUES
          (:bid, :title, :desc, :emp, :by,
           :fid, :due, :st, :pri, :loc, :safety, :notes)
    """), {
        "bid":    body["business_id"],
        "title":  body["title"],
        "desc":   body.get("description"),
        "emp":    body.get("assigned_to_employee_id"),
        "by":     body.get("assigned_by_people_id"),
        "fid":    body.get("field_id"),
        "due":    body.get("due_date"),
        "st":     body.get("status", "pending"),
        "pri":    body.get("priority", "normal"),
        "loc":    body.get("location"),
        "safety": body.get("safety_notes"),
        "notes":  body.get("notes"),
    })
    db.commit()
    return {"task_id": r.scalar()}


@router.put("/tasks/{task_id}", dependencies=[Depends(require_business_body)])
def update_task(task_id: int, body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    completed_at = None
    if body.get("status") == "completed":
        completed_at = "GETDATE()"
    db.execute(text(f"""
        UPDATE HRTask SET
          Title=:title, Description=:desc,
          AssignedToEmployeeID=:emp, FieldID=:fid, DueDate=:due,
          Status=:st, Priority=:pri, Location=:loc, SafetyNotes=:safety,
          Notes=:notes, UpdatedAt=GETDATE()
          {', CompletedAt=GETDATE()' if body.get('status') == 'completed' else ''}
        WHERE TaskID=:tid AND BusinessID=:bid
    """), {
        "tid":    task_id,
        "bid":    body["business_id"],
        "title":  body.get("title"),
        "desc":   body.get("description"),
        "emp":    body.get("assigned_to_employee_id"),
        "fid":    body.get("field_id"),
        "due":    body.get("due_date"),
        "st":     body.get("status", "pending"),
        "pri":    body.get("priority", "normal"),
        "loc":    body.get("location"),
        "safety": body.get("safety_notes"),
        "notes":  body.get("notes"),
    })
    db.commit()
    return {"ok": True}


@router.delete("/tasks/{task_id}", dependencies=[Depends(require_business)])
def delete_task(task_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    db.execute(text("DELETE FROM HRTask WHERE TaskID=:tid AND BusinessID=:bid"),
               {"tid": task_id, "bid": business_id})
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  LEAVE REQUESTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/leave", dependencies=[Depends(require_business)])
def list_leave(
    business_id: int = Query(...),
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _ensure_tables(db)
    where = "WHERE l.BusinessID=:bid"
    params: dict = {"bid": business_id}
    if employee_id:
        where += " AND l.EmployeeID=:eid"
        params["eid"] = employee_id
    if status:
        where += " AND l.Status=:st"
        params["st"] = status
    rows = db.execute(text(f"""
        SELECT l.*, e.FirstName + ' ' + e.LastName AS EmployeeName
        FROM HRLeave l
        JOIN HREmployee e ON e.EmployeeID = l.EmployeeID
        {where}
        ORDER BY l.StartDate DESC
    """), params).fetchall()
    return _rows(rows)


@router.post("/leave", dependencies=[Depends(require_business_body)])
def request_leave(body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    r = db.execute(text("""
        INSERT INTO HRLeave
          (EmployeeID, BusinessID, LeaveType, StartDate, EndDate, DaysRequested, Status, Notes)
        OUTPUT INSERTED.LeaveID
        VALUES (:eid, :bid, :ltype, :sd, :ed, :days, :st, :notes)
    """), {
        "eid":   body["employee_id"],
        "bid":   body["business_id"],
        "ltype": body.get("leave_type", "vacation"),
        "sd":    body["start_date"],
        "ed":    body["end_date"],
        "days":  body.get("days_requested", 1),
        "st":    body.get("status", "pending"),
        "notes": body.get("notes"),
    })
    db.commit()
    return {"leave_id": r.scalar()}


@router.put("/leave/{leave_id}/review", dependencies=[Depends(require_business_body)])
def review_leave(leave_id: int, body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    db.execute(text("""
        UPDATE HRLeave SET
          Status=:st, ReviewedByPeopleID=:by, ReviewedAt=GETDATE(), Notes=COALESCE(:notes, Notes)
        WHERE LeaveID=:lid AND BusinessID=:bid
    """), {
        "lid":   leave_id,
        "bid":   body["business_id"],
        "st":    body["status"],
        "by":    body.get("reviewed_by_people_id"),
        "notes": body.get("notes"),
    })
    db.commit()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  PAY PERIODS & PAY SLIPS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/pay-periods", dependencies=[Depends(require_business)])
def list_pay_periods(business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    rows = db.execute(text(
        "SELECT * FROM HRPayPeriod WHERE BusinessID=:bid ORDER BY StartDate DESC"
    ), {"bid": business_id}).fetchall()
    return _rows(rows)


@router.post("/pay-periods", dependencies=[Depends(require_business_body)])
def create_pay_period(body: dict, db: Session = Depends(get_db)):
    _ensure_tables(db)
    r = db.execute(text("""
        INSERT INTO HRPayPeriod (BusinessID, StartDate, EndDate, Status, Notes)
        OUTPUT INSERTED.PayPeriodID
        VALUES (:bid, :sd, :ed, 'open', :notes)
    """), {"bid": body["business_id"], "sd": body["start_date"],
           "ed": body["end_date"], "notes": body.get("notes")})
    db.commit()
    return {"pay_period_id": r.scalar()}


@router.get("/pay-periods/{period_id}/slips", dependencies=[Depends(require_business)])
def list_pay_slips(period_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    rows = db.execute(text("""
        SELECT ps.*, e.FirstName + ' ' + e.LastName AS EmployeeName,
               e.PayType, e.JobTitle
        FROM HRPaySlip ps
        JOIN HREmployee e ON e.EmployeeID = ps.EmployeeID
        WHERE ps.PayPeriodID=:pid AND ps.BusinessID=:bid
        ORDER BY e.LastName, e.FirstName
    """), {"pid": period_id, "bid": business_id}).fetchall()
    return _rows(rows)


@router.post("/pay-periods/{period_id}/calculate", dependencies=[Depends(require_business)])
def calculate_pay_slips(period_id: int, business_id: int = Query(...), db: Session = Depends(get_db)):
    """
    Auto-generate pay slips from attendance records for the period.
    Pulls hours from HRAttendance; applies hourly/salary/piece-rate logic.
    Returns the generated slips (does not persist until /confirm is called).
    """
    _ensure_tables(db)
    period = db.execute(text(
        "SELECT * FROM HRPayPeriod WHERE PayPeriodID=:pid AND BusinessID=:bid"
    ), {"pid": period_id, "bid": business_id}).fetchone()
    if not period:
        _not_found("Pay period")

    employees = db.execute(text(
        "SELECT * FROM HREmployee WHERE BusinessID=:bid AND IsActive=1"
    ), {"bid": business_id}).fetchall()

    slips = []
    for emp in employees:
        att = db.execute(text("""
            SELECT SUM(HoursWorked) AS TotalHours
            FROM HRAttendance
            WHERE EmployeeID=:eid AND BusinessID=:bid
              AND WorkDate BETWEEN :sd AND :ed
        """), {
            "eid": emp.EmployeeID, "bid": business_id,
            "sd": period.StartDate, "ed": period.EndDate
        }).fetchone()
        total_hours = float(att.TotalHours or 0)

        pay_type = (emp.PayType or "hourly").lower()
        regular_hours, overtime_hours, gross = 0.0, 0.0, 0.0

        if pay_type == "hourly":
            regular_hours = min(total_hours, 40.0)
            overtime_hours = max(total_hours - 40.0, 0.0)
            rate = float(emp.HourlyRate or 0)
            gross = regular_hours * rate + overtime_hours * rate * 1.5
        elif pay_type == "salary":
            regular_hours = total_hours
            # Pro-rate weekly salary over 52 weeks / pay schedules
            annual = float(emp.SalaryRate or 0)
            periods_per_year = {"weekly": 52, "biweekly": 26, "monthly": 12}.get(
                (emp.PaySchedule or "biweekly").lower(), 26
            )
            gross = annual / periods_per_year
        elif pay_type == "piece_rate":
            # Units from attendance activity notes (simplified — admin can adjust)
            regular_hours = total_hours
            piece_units = total_hours  # fallback: 1 unit per hour
            gross = piece_units * float(emp.PieceRateAmount or 0)

        # Standard deduction estimates
        federal = gross * 0.12
        state = gross * 0.05
        ss = gross * 0.062
        medicare = gross * 0.0145
        net = gross - federal - state - ss - medicare

        slips.append({
            "employee_id":    emp.EmployeeID,
            "employee_name":  f"{emp.FirstName} {emp.LastName}",
            "pay_type":       pay_type,
            "regular_hours":  round(regular_hours, 2),
            "overtime_hours": round(overtime_hours, 2),
            "gross_pay":      round(gross, 2),
            "federal_tax":    round(federal, 2),
            "state_tax":      round(state, 2),
            "social_security":round(ss, 2),
            "medicare":       round(medicare, 2),
            "net_pay":        round(net, 2),
        })

    return {"period": _row(period), "slips": slips}


@router.post("/pay-periods/{period_id}/confirm", dependencies=[Depends(require_business_body)])
def confirm_pay_slips(period_id: int, body: dict, db: Session = Depends(get_db)):
    """Persist pay slips and close the pay period."""
    _ensure_tables(db)
    slips = body.get("slips", [])
    business_id = body["business_id"]

    # Delete any existing drafts for this period
    db.execute(text(
        "DELETE FROM HRPaySlip WHERE PayPeriodID=:pid AND BusinessID=:bid"
    ), {"pid": period_id, "bid": business_id})

    total_gross = 0.0
    total_net = 0.0
    for s in slips:
        db.execute(text("""
            INSERT INTO HRPaySlip
              (EmployeeID, PayPeriodID, BusinessID,
               RegularHours, OvertimeHours, PieceRateUnits,
               GrossPay, FederalTax, StateTax, SocialSecurity, Medicare, OtherDeductions, NetPay,
               PaymentDate, PaymentMethod, Notes)
            VALUES
              (:eid, :pid, :bid,
               :rh, :oh, :pru,
               :gross, :fed, :state, :ss, :med, :other, :net,
               :pdate, :pmeth, :notes)
        """), {
            "eid":   s["employee_id"],
            "pid":   period_id,
            "bid":   business_id,
            "rh":    s.get("regular_hours", 0),
            "oh":    s.get("overtime_hours", 0),
            "pru":   s.get("piece_rate_units", 0),
            "gross": s.get("gross_pay", 0),
            "fed":   s.get("federal_tax", 0),
            "state": s.get("state_tax", 0),
            "ss":    s.get("social_security", 0),
            "med":   s.get("medicare", 0),
            "other": s.get("other_deductions", 0),
            "net":   s.get("net_pay", 0),
            "pdate": body.get("payment_date"),
            "pmeth": body.get("payment_method"),
            "notes": s.get("notes"),
        })
        total_gross += float(s.get("gross_pay", 0))
        total_net += float(s.get("net_pay", 0))

    db.execute(text("""
        UPDATE HRPayPeriod
        SET Status='paid', TotalGross=:gross, TotalNet=:net
        WHERE PayPeriodID=:pid AND BusinessID=:bid
    """), {"pid": period_id, "bid": business_id,
           "gross": round(total_gross, 2), "net": round(total_net, 2)})
    db.commit()

    # Post payroll journal entry to Accounting if it's set up
    _post_payroll_journal_entry(db, business_id, period_id, round(total_gross, 2), round(total_net, 2))

    # Notify
    try:
        from routers.notifications import notify_business
        notify_business(
            db, business_id, type="payroll_confirmed",
            title=f"Payroll confirmed: ${round(total_gross, 2):,.2f} gross",
            body=f"Pay period #{period_id} closed. {len(slips)} employees, ${round(total_net, 2):,.2f} net. Journal entry posted to accounting.",
            link_path=f"/hr?BusinessID={business_id}&tab=payroll",
            entity_type="HRPayPeriod", entity_id=period_id,
        )
    except Exception:
        pass

    return {"ok": True, "total_gross": round(total_gross, 2), "total_net": round(total_net, 2)}


def _post_payroll_journal_entry(db: Session, business_id: int, period_id: int, total_gross: float, total_net: float):
    """Debit Wages Expense; Credit Wages Payable + Tax Liabilities."""
    try:
        # Only proceed if accounting is set up
        acct_count = db.execute(text("SELECT COUNT(*) FROM Accounts WHERE BusinessID=:bid"), {"bid": business_id}).scalar()
        if not acct_count:
            return

        # Find required accounts (best-effort by AccountNumber prefix)
        wages_acct = db.execute(text("""
            SELECT TOP 1 AccountID FROM Accounts
            WHERE BusinessID=:bid AND (AccountNumber LIKE '5%' OR AccountNumber LIKE '6%') AND IsActive=1
            ORDER BY AccountNumber
        """), {"bid": business_id}).fetchone()
        payable_acct = db.execute(text("""
            SELECT TOP 1 AccountID FROM Accounts
            WHERE BusinessID=:bid AND AccountNumber LIKE '2%' AND IsActive=1
            ORDER BY AccountNumber
        """), {"bid": business_id}).fetchone()

        if not wages_acct or not payable_acct:
            return

        admin = db.execute(text("""
            SELECT TOP 1 PeopleID FROM BusinessAccess
            WHERE BusinessID=:bid AND Active=1 ORDER BY AccessLevelID DESC
        """), {"bid": business_id}).fetchone()
        created_by = admin.PeopleID if admin else None

        # Generate JE number
        count = db.execute(text(
            "SELECT COUNT(*)+1 FROM JournalEntries WHERE BusinessID=:bid"
        ), {"bid": business_id}).scalar()
        je_num = f"JE-PAY-{business_id}-{count:04d}"
        tax_liability = round(total_gross - total_net, 2)

        je_row = db.execute(text("""
            INSERT INTO JournalEntries
                (BusinessID, EntryNumber, EntryDate, Description, Reference, SourceType, SourceID, IsPosted, CreatedBy)
            OUTPUT INSERTED.JournalEntryID
            VALUES (:bid, :num, CAST(GETDATE() AS DATE), :desc, :ref, 'Payroll', :srcId, 1, :by)
        """), {
            "bid": business_id, "num": je_num,
            "desc": f"Payroll run — pay period #{period_id}",
            "ref": f"PAY-PERIOD-{period_id}", "srcId": period_id, "by": created_by,
        }).fetchone()
        je_id = je_row.JournalEntryID

        # Debit: Wages Expense (full gross)
        db.execute(text("""
            INSERT INTO JournalEntryLines
                (JournalEntryID, BusinessID, AccountID, DebitAmount, CreditAmount, Description, LineOrder)
            VALUES (:je, :bid, :acct, :amt, 0, :desc, 0)
        """), {"je": je_id, "bid": business_id, "acct": wages_acct.AccountID,
               "amt": total_gross, "desc": f"Wages expense — period #{period_id}"})

        # Credit: Wages Payable (net pay owed to employees)
        db.execute(text("""
            INSERT INTO JournalEntryLines
                (JournalEntryID, BusinessID, AccountID, DebitAmount, CreditAmount, Description, LineOrder)
            VALUES (:je, :bid, :acct, 0, :amt, :desc, 1)
        """), {"je": je_id, "bid": business_id, "acct": payable_acct.AccountID,
               "amt": total_net, "desc": f"Net wages payable — period #{period_id}"})

        # Credit: Tax Liabilities (gross - net)
        if tax_liability > 0:
            db.execute(text("""
                INSERT INTO JournalEntryLines
                    (JournalEntryID, BusinessID, AccountID, DebitAmount, CreditAmount, Description, LineOrder)
                VALUES (:je, :bid, :acct, 0, :amt, :desc, 2)
            """), {"je": je_id, "bid": business_id, "acct": payable_acct.AccountID,
                   "amt": tax_liability, "desc": f"Payroll tax liabilities — period #{period_id}"})
        db.commit()
    except Exception as _e:
        print(f"[payroll-je] {_e}")


@router.get("/payroll-summary", dependencies=[Depends(require_business)])
def payroll_summary(
    business_id: int = Query(...),
    period_start: str = Query(...),
    period_end: str = Query(...),
    db: Session = Depends(get_db),
):
    """Attendance-based payroll estimate for a date range — no pay slips created."""
    _ensure_tables(db)

    employees = db.execute(text("""
        SELECT e.EmployeeID, e.FirstName, e.LastName, e.PayType, e.HourlyRate,
               e.SalaryRate, e.PaySchedule, e.PieceRateAmount, e.Department, e.JobTitle,
               ISNULL(SUM(a.HoursWorked), 0) AS TotalHours,
               COUNT(a.AttendanceID) AS AttendanceDays
        FROM HREmployee e
        LEFT JOIN HRAttendance a ON a.EmployeeID = e.EmployeeID AND a.BusinessID = e.BusinessID
            AND a.WorkDate >= :start AND a.WorkDate <= :end
        WHERE e.BusinessID = :bid AND e.IsActive = 1
        GROUP BY e.EmployeeID, e.FirstName, e.LastName, e.PayType, e.HourlyRate,
                 e.SalaryRate, e.PaySchedule, e.PieceRateAmount, e.Department, e.JobTitle
        ORDER BY e.LastName, e.FirstName
    """), {"bid": business_id, "start": period_start, "end": period_end}).fetchall()

    rows = []
    total_gross = 0.0
    total_net = 0.0

    for emp in employees:
        hours = float(emp.TotalHours or 0)
        gross = 0.0
        pay_type = emp.PayType or "hourly"
        if pay_type == "hourly":
            gross = hours * float(emp.HourlyRate or 0)
        elif pay_type == "salary":
            # Pro-rate monthly salary by business days
            import calendar
            try:
                y1, m1 = int(period_start[:4]), int(period_start[5:7])
                y2, m2 = int(period_end[:4]), int(period_end[5:7])
                # Approximate: monthly / 22 * days in period
                from datetime import date as _date
                days = (_date(y2, m2, int(period_end[8:10])) - _date(y1, m1, int(period_start[8:10]))).days + 1
                monthly = float(emp.SalaryRate or 0)
                gross = monthly / 22 * min(days, 22)
            except Exception:
                gross = float(emp.SalaryRate or 0)
        elif pay_type == "piece_rate":
            gross = hours * float(emp.PieceRateAmount or 0)

        taxes = gross * (0.12 + 0.05 + 0.062 + 0.0145)
        net = gross - taxes
        total_gross += gross
        total_net += net

        rows.append({
            "employee_id":    emp.EmployeeID,
            "employee_name":  f"{emp.FirstName} {emp.LastName}",
            "department":     emp.Department,
            "job_title":      emp.JobTitle,
            "pay_type":       pay_type,
            "total_hours":    round(hours, 2),
            "attendance_days":int(emp.AttendanceDays or 0),
            "gross_pay":      round(gross, 2),
            "estimated_taxes":round(taxes, 2),
            "net_pay":        round(net, 2),
        })

    return {
        "period_start":  period_start,
        "period_end":    period_end,
        "employee_count":len(rows),
        "total_gross":   round(total_gross, 2),
        "total_net":     round(total_net, 2),
        "rows":          rows,
    }


# ── Summary / Dashboard ───────────────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(require_business)])
def hr_summary(business_id: int = Query(...), db: Session = Depends(get_db)):
    _ensure_tables(db)
    try:
        r = db.execute(text("""
            SELECT
              (SELECT COUNT(*) FROM HREmployee WHERE BusinessID=:bid AND IsActive=1) AS active_employees,
              (SELECT COUNT(*) FROM HREmployee WHERE BusinessID=:bid AND EmploymentType='seasonal' AND IsActive=1) AS seasonal,
              (SELECT COUNT(*) FROM HRTask WHERE BusinessID=:bid AND Status='pending') AS pending_tasks,
              (SELECT COUNT(*) FROM HRTask WHERE BusinessID=:bid AND Status='in_progress') AS active_tasks,
              (SELECT COUNT(*) FROM HRLeave WHERE BusinessID=:bid AND Status='pending') AS pending_leave,
              (SELECT COUNT(*) FROM HRPayPeriod WHERE BusinessID=:bid AND Status='open') AS open_pay_periods,
              (SELECT COUNT(*) FROM HRCertification
               WHERE BusinessID=:bid AND ExpiryDate IS NOT NULL
                 AND ExpiryDate BETWEEN CAST(GETDATE() AS DATE) AND DATEADD(day,60,CAST(GETDATE() AS DATE))) AS expiring_certs
        """), {"bid": business_id}).fetchone()
        return dict(r._mapping)
    except Exception:
        return {"active_employees": 0, "seasonal": 0, "pending_tasks": 0,
                "active_tasks": 0, "pending_leave": 0, "open_pay_periods": 0, "expiring_certs": 0}


# ── Payroll CSV Export ────────────────────────────────────────────────────────

@router.get("/payroll/export", dependencies=[Depends(require_business)])
def export_payroll_csv(business_id: int = Query(...), pay_period_id: Optional[int] = None,
                       db: Session = Depends(get_db)):
    """Download payroll summary for a pay period (or all paid periods) as CSV."""
    _ensure_tables(db)
    q = """
        SELECT pp.PeriodLabel, pp.StartDate, pp.EndDate, pp.PaySchedule,
               e.FirstName + ' ' + e.LastName AS EmployeeName,
               e.JobTitle, e.PayType, e.Department,
               ps.RegularHours, ps.OvertimeHours, ps.PieceRateUnits,
               ps.GrossPay, ps.FederalTax, ps.StateTax,
               ps.SocialSecurity, ps.Medicare, ps.OtherDeductions, ps.NetPay,
               ps.PaymentDate, ps.PaymentMethod, ps.Notes
        FROM HRPaySlip ps
        JOIN HRPayPeriod pp ON pp.PayPeriodID = ps.PayPeriodID
        JOIN HREmployee e ON e.EmployeeID = ps.EmployeeID
        WHERE ps.BusinessID = :bid
    """
    params: dict = {"bid": business_id}
    if pay_period_id:
        q += " AND ps.PayPeriodID = :pid"; params["pid"] = pay_period_id
    q += " ORDER BY pp.StartDate DESC, e.LastName, e.FirstName"
    rows = db.execute(text(q), params).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["PeriodLabel", "StartDate", "EndDate", "PaySchedule",
                     "EmployeeName", "JobTitle", "PayType", "Department",
                     "RegularHours", "OvertimeHours", "PieceRateUnits",
                     "GrossPay", "FederalTax", "StateTax", "SocialSecurity",
                     "Medicare", "OtherDeductions", "NetPay",
                     "PaymentDate", "PaymentMethod", "Notes"])
    for r in rows:
        writer.writerow([r.PeriodLabel, r.StartDate, r.EndDate, r.PaySchedule,
                         r.EmployeeName, r.JobTitle, r.PayType, r.Department,
                         r.RegularHours, r.OvertimeHours, r.PieceRateUnits,
                         r.GrossPay, r.FederalTax, r.StateTax, r.SocialSecurity,
                         r.Medicare, r.OtherDeductions, r.NetPay,
                         r.PaymentDate, r.PaymentMethod, r.Notes])
    buf.seek(0)
    suffix = f"_period{pay_period_id}" if pay_period_id else ""
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=payroll_{business_id}{suffix}.csv"},
    )
