from flask import Flask, render_template,request,redirect,url_for,flash,session,send_file
from werkzeug.security import generate_password_hash,check_password_hash
import sqlite3
import os
import hmac
from database import create_database
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from datetime import datetime

app = Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY", "local-development-only-key")

create_database()

@app.route("/temporary-admin-setup", methods=["GET", "POST"])
def temporary_admin_setup():
    setup_token = os.environ.get("ADMIN_SETUP_TOKEN")

    if not setup_token:
        return "Admin setup is disabled.", 404

    if request.method == "GET":
        return """
        <h2>Smart Campus Admin Setup</h2>
        <form method="POST">
            <input type="password" name="token" placeholder="Setup Token" required><br><br>
            <input type="text" name="name" placeholder="Admin Name" required><br><br>
            <input type="email" name="email" placeholder="Admin Email" required><br><br>
            <input type="password" name="password" placeholder="New Password" minlength="12" required><br><br>
            <button type="submit">Create or Reset Admin</button>
        </form>
        """

    if not hmac.compare_digest(
        setup_token, request.form.get("token", "")
    ):
        return "Invalid setup token.", 403

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not name or not email or len(password) < 12:
        return "Enter valid details. Password must have at least 12 characters.", 400

    conn = sqlite3.connect("attendance.db")

    try:
        user = conn.execute(
            "SELECT id, role FROM users WHERE email=?", (email,)
        ).fetchone()

        admin = conn.execute(
            "SELECT id FROM users WHERE role='admin' LIMIT 1"
        ).fetchone()

        password_hash = generate_password_hash(password)

        if user:
            if user[1] != "admin":
                return "This email belongs to a non-admin account.", 409

            conn.execute(
                "UPDATE users SET name=?, password=? WHERE id=?",
                (name, password_hash, user[0])
            )

        else:
            if admin:
                return "An Admin account already exists.", 409

            conn.execute(
                "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, password_hash, "admin")
            )

        conn.commit()
        return "Admin account created or password reset successfully."

    finally:
        conn.close()


@app.route("/")
def home():
    return render_template("index.html")
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])
        role = request.form["role"]
        teacher_code = request.form.get("teacher_code", "")

        if role == "teacher" and teacher_code != "TEACHER2026":
            flash("Invalid teacher registration code!")
            return render_template("register.html")

        conn = sqlite3.connect("attendance.db")

        try:
            conn.execute(
                "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                (name, email, password, role)
            )
            conn.commit()
            flash("Registration successful!")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Email already exists!")

        finally:
            conn.close()

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = sqlite3.connect("attendance.db")
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user[3], password):
            session["user_id"] = user[0]
            session["role"] = user[4]

            flash("Login successful!")

            if user[4] == "admin":
                return redirect(url_for("admin_dashboard"))

            elif user[4] == "teacher":
                return redirect(url_for("teacher_dashboard"))

            else:
                return redirect(url_for("student_dashboard"))

        flash("Invalid email or password!")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out successfully!")
    return redirect(url_for("login"))
@app.route("/student")
def student_dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("student.html")
@app.route("/students")
def view_students():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")
    students = conn.execute(
        "SELECT id, name, email FROM users WHERE role = 'student'"
    ).fetchall()
    conn.close()

    return render_template("students.html", students=students)




@app.route("/attendance")
def attendance():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        flash("Only students can view this page.")
        return redirect(url_for("teacher_dashboard"))

    conn = sqlite3.connect("attendance.db")

    student = conn.execute("""
        SELECT name FROM users
        WHERE id = ? AND role = 'student'
    """, (session["user_id"],)).fetchone()

    if not student:
        conn.close()
        flash("Student not found!")
        return redirect(url_for("login"))

    student_name = student[0]

    # SUBJECT-WISE ATTENDANCE
    subject_summary = conn.execute("""
        SELECT
            subjects.subject_name,
            COUNT(attendance.id),
            COALESCE(SUM(
                CASE WHEN attendance.status = 'Present'
                THEN 1 ELSE 0 END
            ), 0)
        FROM subjects
        LEFT JOIN attendance
            ON attendance.subject_id = subjects.id
            AND attendance.student_name = ?
        GROUP BY subjects.id, subjects.subject_name
        ORDER BY subjects.subject_name
    """, (student_name,)).fetchall()

    # CUMULATIVE ATTENDANCE
    totals = conn.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(
                CASE WHEN status = 'Present'
                THEN 1 ELSE 0 END
            ), 0)
        FROM attendance
        WHERE student_name = ?
        AND subject_id IS NOT NULL
    """, (student_name,)).fetchone()

    total_classes = totals[0]
    present_classes = totals[1]

    overall_percentage = 0

    if total_classes > 0:
        overall_percentage = round(
            (present_classes / total_classes) * 100, 2
        )

    # ATTENDANCE RECORDS
    records = conn.execute("""
        SELECT
            attendance.date,
            COALESCE(subjects.subject_name, 'Not Assigned'),
            attendance.status
        FROM attendance
        LEFT JOIN subjects
            ON attendance.subject_id = subjects.id
        WHERE attendance.student_name = ?
        ORDER BY attendance.date DESC
    """, (student_name,)).fetchall()

    conn.close()

    subject_data = []

    for item in subject_summary:
        total = item[1]
        present = item[2]

        percentage = 0

        if total > 0:
            percentage = round((present / total) * 100, 2)

        subject_data.append({
            "subject": item[0],
            "total": total,
            "present": present,
            "absent": total - present,
            "percentage": percentage
        })

    return render_template(
        "attendance.html",
        records=records,
        subject_summary=subject_data,
        total_classes=total_classes,
        present_classes=present_classes,
        overall_percentage=overall_percentage
    )



@app.route("/activities", methods=["GET", "POST"])
def activities():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect("attendance.db")

    if request.method == "POST":

        if session.get("role") != "teacher":
            flash("Only teachers can add activities!")
            conn.close()
            return redirect(url_for("activities"))

        category = request.form["category"]
        title = request.form["title"]
        description = request.form["description"]

        conn.execute(
            "INSERT INTO activities (title, description, status, category) VALUES (?, ?, ?, ?)",
            (title, description, "Pending", category)
        )

        conn.commit()
        conn.close()

        flash("Activity added successfully!")
        return redirect(url_for("activities"))

    if session.get("role") == "student":

        student_id = session["user_id"]

        activity_records = conn.execute("""
            SELECT
                activities.id,
                activities.title,
                activities.description,
                activities.category,
                COALESCE(student_activities.status, 'Pending')
            FROM activities
            LEFT JOIN student_activities
            ON activities.id = student_activities.activity_id
            AND student_activities.student_id = ?
            ORDER BY activities.id DESC
        """, (student_id,)).fetchall()

    else:

        activity_records = conn.execute(
            "SELECT id, title, description, category, status FROM activities ORDER BY id DESC"
        ).fetchall()

    conn.close()

    return render_template(
        "activities.html",
        activities=activity_records,
        role=session.get("role")
    )
@app.route("/complete-student-activity/<int:activity_id>", methods=["POST"])
def complete_student_activity(activity_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        flash("Only students can update their activity progress!")
        return redirect(url_for("activities"))

    conn = sqlite3.connect("attendance.db")

    activity = conn.execute(
        "SELECT id FROM activities WHERE id = ?",
        (activity_id,)
    ).fetchone()

    if not activity:
        conn.close()
        flash("Activity not found!")
        return redirect(url_for("activities"))

    conn.execute("""
        INSERT INTO student_activities
        (student_id, activity_id, status)
        VALUES (?, ?, 'Completed')
        ON CONFLICT(student_id, activity_id)
        DO UPDATE SET status = 'Completed'
    """, (session["user_id"], activity_id))

    conn.commit()
    conn.close()

    flash("Your activity has been marked as completed!")
    return redirect(url_for("activities"))

@app.route("/update-activity/<int:activity_id>", methods=["POST"])
def update_activity(activity_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Only teachers can complete activities!")
        return redirect(url_for("activities"))

    conn = sqlite3.connect("attendance.db")

    conn.execute(
        "UPDATE activities SET status = 'Completed' WHERE id = ?",
        (activity_id,)
    )

    conn.commit()
    conn.close()

    flash("Activity marked as completed!")
    return redirect(url_for("activities"))
@app.route("/delete-activity/<int:activity_id>", methods=["POST"])
def delete_activity(activity_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Only teachers can delete activities!")
        return redirect(url_for("activities"))

    conn = sqlite3.connect("attendance.db")

    conn.execute(
        "DELETE FROM activities WHERE id = ?",
        (activity_id,)
    )

    conn.commit()
    conn.close()

    flash("Activity deleted successfully!")
    return redirect(url_for("activities"))

@app.route("/download-student-report/<int:student_id>")
def download_student_report(student_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Only teachers can download student reports!")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")

    student = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ? AND role = 'student'",
        (student_id,)
    ).fetchone()

    if not student:
        conn.close()
        flash("Student not found!")
        return redirect(url_for("student_progress_report"))

    attendance_records = conn.execute("""
        SELECT date, status
        FROM attendance
        WHERE student_name = ?
        ORDER BY date DESC
    """, (student[1],)).fetchall()

    activity_records = conn.execute("""
        SELECT activities.title,
               activities.category,
               COALESCE(student_activities.status, 'Pending')
        FROM activities
        LEFT JOIN student_activities
        ON activities.id = student_activities.activity_id
        AND student_activities.student_id = ?
    """, (student_id,)).fetchall()

    conn.close()

    total_days = len(attendance_records)
    present_days = sum(
        1 for record in attendance_records
        if record[1] == "Present"
    )

    attendance_percentage = 0

    if total_days > 0:
        attendance_percentage = round(
            (present_days / total_days) * 100, 2
        )

    total_activities = len(activity_records)

    completed_activities = sum(
        1 for activity in activity_records
        if activity[2] == "Completed"
    )

    activity_percentage = 0

    if total_activities > 0:
        activity_percentage = round(
            (completed_activities / total_activities) * 100, 2
        )

    buffer = BytesIO()

    pdf = canvas.Canvas(buffer, pagesize=A4)

    from reportlab.lib.utils import simpleSplit

    width, height = A4
    y = height - 50

    def write_line(text, size=10, gap=20):
        nonlocal y

        pdf.setFont("Helvetica", size)

        lines = simpleSplit(str(text), "Helvetica", size, width - 100)

        for line in lines:
            if y < 50:
                pdf.showPage()
                y = height - 50
                pdf.setFont("Helvetica", size)

            pdf.drawString(50, y, line)
            y -= gap

    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, y, "SMART CAMPUS")
    y -= 35

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawCentredString(width / 2, y, "Student Progress Report")
    y -= 40

    write_line("STUDENT INFORMATION", 13, 25)
    write_line("Student ID: " + str(student[0]))
    write_line("Name: " + student[1])
    write_line("Email: " + student[2])

    y -= 10

    write_line("ATTENDANCE SUMMARY", 13, 25)
    write_line("Total Attendance Days: " + str(total_days))
    write_line("Present Days: " + str(present_days))
    write_line("Attendance Percentage: " + str(attendance_percentage) + "%")

    y -= 10

    write_line("ATTENDANCE HISTORY", 13, 25)

    for record in attendance_records:
        write_line(str(record[0]) + " - " + str(record[1]))

    y -= 10

    write_line("ACTIVITY SUMMARY", 13, 25)
    write_line("Total Activities: " + str(total_activities))
    write_line("Completed Activities: " + str(completed_activities))
    write_line("Pending Activities: " + str(total_activities - completed_activities))
    write_line("Activity Progress: " + str(activity_percentage) + "%")

    y -= 10

    write_line("ACTIVITY HISTORY", 13, 25)

    for activity in activity_records:
        write_line("Activity: " + str(activity[0]))
        write_line("Category: " + str(activity[1]))
        write_line("Status: " + str(activity[2]))
        y -= 5

    pdf.save()

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"{student[1]}_Student_Report.pdf",
        mimetype="application/pdf"
    )



@app.route("/teacher-analytics")
def teacher_analytics():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")

    students = conn.execute("""
        SELECT
            users.id,
            users.name,

            (SELECT COUNT(*)
             FROM attendance
             WHERE attendance.student_name = users.name
             AND attendance.subject_id IS NOT NULL),

            (SELECT COUNT(*)
             FROM attendance
             WHERE attendance.student_name = users.name
             AND attendance.status = 'Present'
             AND attendance.subject_id IS NOT NULL),

            (SELECT COUNT(*)
             FROM student_activities
             WHERE student_activities.student_id = users.id
             AND student_activities.status = 'Completed')

        FROM users
        WHERE users.role = 'student'
        ORDER BY users.id
    """).fetchall()

    total_activities = conn.execute(
        "SELECT COUNT(*) FROM activities"
    ).fetchone()[0]

    monthly_records = conn.execute("""
        SELECT
            strftime('%Y-%m', date),
            COUNT(*),
            SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END)
        FROM attendance
        WHERE subject_id IS NOT NULL
        GROUP BY strftime('%Y-%m', date)
        ORDER BY strftime('%Y-%m', date)
    """).fetchall()

    conn.close()

    student_names = []
    attendance_percentages = []
    completed_activities = []
    pending_activities = []

    monthly_labels = []
    monthly_percentages = []

    for record in monthly_records:

        month = record[0]
        total_classes = record[1]
        present_classes = record[2] or 0

        percentage = 0

        if total_classes > 0:
            percentage = round(
                (present_classes / total_classes) * 100, 2
            )

        monthly_labels.append(month)
        monthly_percentages.append(percentage)

    if not monthly_labels:
        monthly_labels = ["No Attendance Data"]
        monthly_percentages = [0]

    for student in students:

        total_classes = student[2]
        present_classes = student[3]
        completed = student[4]

        attendance_percentage = 0

        if total_classes > 0:
            attendance_percentage = round(
                (present_classes / total_classes) * 100, 2
            )

        student_names.append(student[1])
        attendance_percentages.append(attendance_percentage)
        completed_activities.append(completed)
        pending_activities.append(
            max(0, total_activities - completed)
        )

    return render_template(
        "teacher_analytics.html",
        student_names=student_names,
        attendance_percentages=attendance_percentages,
        completed_activities=completed_activities,
        pending_activities=pending_activities,
        monthly_labels=monthly_labels,
        monthly_percentages=monthly_percentages
    )


@app.route("/student-details/<int:student_id>")
def student_details(student_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")

    student = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ? AND role = 'student'",
        (student_id,)
    ).fetchone()

    if not student:
        conn.close()
        flash("Student not found!")
        return redirect(url_for("student_progress_report"))

    attendance_records = conn.execute("""
        SELECT date, status
        FROM attendance
        WHERE student_name = ?
        ORDER BY date DESC
    """, (student[1],)).fetchall()

    activity_records = conn.execute("""
        SELECT activities.title,
               activities.category,
               COALESCE(student_activities.status, 'Pending')
        FROM activities
        LEFT JOIN student_activities
        ON activities.id = student_activities.activity_id
        AND student_activities.student_id = ?
        ORDER BY activities.id DESC
    """, (student_id,)).fetchall()

    conn.close()

    return render_template(
        "student_details.html",
        student=student,
        attendance_records=attendance_records,
        activity_records=activity_records
    )




@app.route("/teacher-marks", methods=["GET", "POST"])
def teacher_marks():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")

    students = conn.execute("""
        SELECT id, name
        FROM users
        WHERE role = 'student'
        ORDER BY name
    """).fetchall()

    if request.method == "POST":

        # EDIT STUDENT MARKS
        if request.form.get("action") == "edit":
            try:
                mark_id = int(request.form["mark_id"])
                student_id = int(request.form["student_id"])
                subject = request.form["subject"].strip()
                exam_name = request.form["exam_name"].strip()
                marks = float(request.form["marks"])
                max_marks = float(request.form["max_marks"])

                if (not subject or not exam_name or max_marks <= 0
                        or marks < 0 or marks > max_marks):
                    flash("Please enter valid marks.")

                elif not any(s[0] == student_id for s in students):
                    flash("Invalid student selected.")

                else:
                    conn.execute("""
                        UPDATE student_marks
                        SET student_id = ?, subject = ?,
                            exam_name = ?, marks_obtained = ?,
                            max_marks = ?, teacher_id = ?
                        WHERE id = ?
                    """, (
                        student_id, subject, exam_name,
                        marks, max_marks, session["user_id"],
                        mark_id
                    ))

                    conn.commit()
                    flash("Student marks updated successfully!")

            except (ValueError, TypeError, KeyError):
                flash("Please enter valid marks.")

            conn.close()
            return redirect(url_for("teacher_marks"))

        # DELETE STUDENT MARKS
        if request.form.get("action") == "delete":

            try:
                mark_id = int(request.form.get("mark_id"))

                conn.execute(
                    "DELETE FROM student_marks WHERE id = ?",
                    (mark_id,)
                )

                conn.commit()
                flash("Student marks deleted successfully!")

            except (ValueError, TypeError):
                flash("Invalid marks selection.")

            conn.close()
            return redirect(url_for("teacher_marks"))

        # ADD OR UPDATE STUDENT MARKS
        try:
            student_id = int(request.form["student_id"])
            subject = request.form["subject"].strip()
            exam_name = request.form["exam_name"].strip()
            marks = float(request.form["marks"])
            max_marks = float(request.form["max_marks"])

            if (not subject or not exam_name or max_marks <= 0
                    or marks < 0 or marks > max_marks):
                flash("Please enter valid marks.")

            elif not any(s[0] == student_id for s in students):
                flash("Invalid student selected.")

            else:
                conn.execute("""
                    INSERT INTO student_marks
                    (student_id, subject, exam_name, marks_obtained,
                     max_marks, teacher_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(student_id, subject, exam_name)
                    DO UPDATE SET
                        marks_obtained = excluded.marks_obtained,
                        max_marks = excluded.max_marks,
                        teacher_id = excluded.teacher_id
                """, (
                    student_id, subject, exam_name,
                    marks, max_marks, session["user_id"]
                ))

                conn.commit()
                flash("Student marks saved successfully!")

        except (ValueError, TypeError):
            flash("Please enter valid numeric marks.")

    # DISPLAY SAVED MARKS
    saved_marks = conn.execute("""
        SELECT users.name, student_marks.subject,
               student_marks.exam_name,
               student_marks.marks_obtained,
               student_marks.max_marks,
               student_marks.id
        FROM student_marks
        JOIN users ON users.id = student_marks.student_id
        ORDER BY users.name, student_marks.subject
    """).fetchall()

    conn.close()

    return render_template(
        "teacher_marks.html",
        students=students,
        saved_marks=saved_marks
    )





@app.route("/my-marks")
def my_marks():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        flash("Access denied! Students only.")
        return redirect("/teacher")

    conn = sqlite3.connect("attendance.db")

    marks = conn.execute("""
        SELECT subject, exam_name, marks_obtained, max_marks
        FROM student_marks
        WHERE student_id = ?
        ORDER BY subject, exam_name
    """, (session["user_id"],)).fetchall()

    total_obtained = sum(mark[2] for mark in marks)
    total_maximum = sum(mark[3] for mark in marks)

    overall_percentage = (
        round((total_obtained / total_maximum) * 100, 2)
        if total_maximum > 0 else 0
    )

    highest_subject = max(
        marks,
        key=lambda mark: (mark[2] / mark[3]) * 100
    )[0] if marks else "No marks yet"

    # Chart Data
    chart_labels = [
        f"{mark[0]} - {mark[1]}" for mark in marks
    ]

    chart_percentages = [
        round((mark[2] / mark[3]) * 100, 2)
        for mark in marks
    ]

    conn.close()

    return render_template(
        "my_marks.html",
        marks=marks,
        total_obtained=total_obtained,
        total_maximum=total_maximum,
        overall_percentage=overall_percentage,
        highest_subject=highest_subject,
        chart_labels=chart_labels,
        chart_percentages=chart_percentages
    )




@app.route("/profile")
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect("attendance.db")

    student = conn.execute(
        "SELECT id, name, email, role FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    conn.close()

    return render_template("profile.html", student=student)


@app.route("/progress")
def progress():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        flash("This page is for students only.")
        return redirect(url_for("teacher_dashboard"))

    conn = sqlite3.connect("attendance.db")

    student = conn.execute(
        "SELECT name FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    total_days = 0
    present_days = 0
    percentage = 0

    if student:
        records = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) "
            "FROM attendance WHERE student_name = ?",
            (student[0],)
        ).fetchone()

        total_days = records[0]
        present_days = records[1] or 0

        if total_days > 0:
            percentage = round(
                (present_days / total_days) * 100, 2
            )

    activity_records = conn.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(
                CASE WHEN student_activities.status = 'Completed'
                THEN 1 ELSE 0 END
            ), 0)
        FROM activities
        LEFT JOIN student_activities
        ON activities.id = student_activities.activity_id
        AND student_activities.student_id = ?
    """, (session["user_id"],)).fetchone()

    total_activities = activity_records[0]
    completed_activities = activity_records[1]
    pending_activities = total_activities - completed_activities

    activity_percentage = 0

    if total_activities > 0:
        activity_percentage = round(
            (completed_activities / total_activities) * 100, 2
        )

    conn.close()

    return render_template(
        "progress.html",
        total_days=total_days,
        present_days=present_days,
        percentage=percentage,
        total_activities=total_activities,
        completed_activities=completed_activities,
        pending_activities=pending_activities,
        activity_percentage=activity_percentage
    )
@app.route("/teacher")
def teacher_dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    return render_template("teacher.html")

@app.route("/manage-subjects", methods=["GET", "POST"])
def manage_subjects():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")

    if request.method == "POST":
        subject_name = request.form.get("subject_name", "").strip()

        if not subject_name:
            flash("Please enter a subject name.")
        else:
            try:
                conn.execute("""
                    INSERT INTO subjects (subject_name, teacher_id)
                    VALUES (?, ?)
                """, (subject_name, session["user_id"]))

                conn.commit()
                flash("Subject added successfully!")

            except sqlite3.IntegrityError:
                flash("You have already added this subject.")

    subjects = conn.execute("""
        SELECT id, subject_name
        FROM subjects
        WHERE teacher_id = ?
        ORDER BY subject_name
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "manage_subjects.html",
        subjects=subjects
    )





@app.route("/teacher-attendance", methods=["GET", "POST"])
def teacher_attendance():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    teacher_id = session["user_id"]
    conn = sqlite3.connect("attendance.db")

    subjects = conn.execute("""
        SELECT id, subject_name
        FROM subjects
        WHERE teacher_id = ?
        ORDER BY subject_name
    """, (teacher_id,)).fetchall()

    # EDIT ATTENDANCE
    if request.method == "POST" and request.form.get("action") == "edit":

        try:
            attendance_id = int(request.form["attendance_id"])
            student_id = int(request.form["student_id"])
            subject_id = int(request.form["subject_id"])
            date = request.form["date"]
            status = request.form["status"]

            subject = conn.execute("""
                SELECT id FROM subjects
                WHERE id = ? AND teacher_id = ?
            """, (subject_id, teacher_id)).fetchone()

            student = conn.execute("""
                SELECT name FROM users
                WHERE id = ? AND role = 'student'
            """, (student_id,)).fetchone()

            record = conn.execute("""
                SELECT id FROM attendance
                WHERE id = ?
                AND subject_id IN (
                    SELECT id FROM subjects
                    WHERE teacher_id = ?
                )
            """, (attendance_id, teacher_id)).fetchone()

            if not subject or not student or not record or status not in ["Present", "Absent"] or not date:
                flash("Invalid attendance details!")

            else:
                existing = conn.execute("""
                    SELECT id FROM attendance
                    WHERE student_name = ?
                    AND date = ?
                    AND subject_id = ?
                    AND id != ?
                """, (student[0], date, subject_id, attendance_id)).fetchone()

                if existing:
                    flash("Attendance already exists for this student, subject and date!")

                else:
                    conn.execute("""
                        UPDATE attendance
                        SET student_name = ?,
                            date = ?,
                            status = ?,
                            subject_id = ?
                        WHERE id = ?
                    """, (student[0], date, status, subject_id, attendance_id))

                    conn.commit()
                    flash("Attendance updated successfully!")

        except (ValueError, KeyError):
            flash("Invalid attendance details!")

        conn.close()
        return redirect(url_for("teacher_attendance"))

    # DELETE ATTENDANCE
    if request.method == "POST" and request.form.get("action") == "delete":

        try:
            attendance_id = int(request.form["attendance_id"])

            result = conn.execute("""
                DELETE FROM attendance
                WHERE id = ?
                AND subject_id IN (
                    SELECT id FROM subjects
                    WHERE teacher_id = ?
                )
            """, (attendance_id, teacher_id))

            conn.commit()

            if result.rowcount:
                flash("Attendance deleted successfully!")
            else:
                flash("Attendance record not found or access denied!")

        except (ValueError, KeyError):
            flash("Invalid attendance selection!")

        conn.close()
        return redirect(url_for("teacher_attendance"))

    # ADD ATTENDANCE
    if request.method == "POST":

        try:
            student_id = int(request.form["student_id"])
            subject_id = int(request.form["subject_id"])
            date = request.form["date"]
            status = request.form["status"]

            student = conn.execute("""
                SELECT name FROM users
                WHERE id = ? AND role = 'student'
            """, (student_id,)).fetchone()

            subject = conn.execute("""
                SELECT id FROM subjects
                WHERE id = ? AND teacher_id = ?
            """, (subject_id, teacher_id)).fetchone()

            if not student or not subject or status not in ["Present", "Absent"] or not date:
                flash("Invalid attendance details!")

            else:
                existing = conn.execute("""
                    SELECT id FROM attendance
                    WHERE student_name = ?
                    AND date = ?
                    AND subject_id = ?
                """, (student[0], date, subject_id)).fetchone()

                if existing:
                    flash("Attendance already marked for this student in this subject on this date!")

                else:
                    conn.execute("""
                        INSERT INTO attendance
                        (student_name, date, status, subject_id)
                        VALUES (?, ?, ?, ?)
                    """, (student[0], date, status, subject_id))

                    conn.commit()
                    flash("Subject-wise attendance saved successfully!")

        except (ValueError, KeyError):
            flash("Please enter valid attendance details!")

        conn.close()
        return redirect(url_for("teacher_attendance"))

    # STUDENT CUMULATIVE ATTENDANCE
    students = conn.execute("""
        SELECT
            users.id,
            users.name,
            COUNT(attendance.id),
            COALESCE(SUM(
                CASE WHEN attendance.status = 'Present'
                THEN 1 ELSE 0 END
            ), 0)
        FROM users
        LEFT JOIN attendance
            ON users.name = attendance.student_name
            AND attendance.subject_id IS NOT NULL
        WHERE users.role = 'student'
        GROUP BY users.id, users.name
        ORDER BY users.id
    """).fetchall()

    # SUBJECT-WISE ATTENDANCE SUMMARY
    subject_summary = conn.execute("""
        SELECT
            users.id,
            users.name,
            subjects.subject_name,
            COUNT(attendance.id),
            COALESCE(SUM(
                CASE WHEN attendance.status = 'Present'
                THEN 1 ELSE 0 END
            ), 0)
        FROM users
        CROSS JOIN subjects
        LEFT JOIN attendance
            ON attendance.student_name = users.name
            AND attendance.subject_id = subjects.id
        WHERE users.role = 'student'
        AND subjects.teacher_id = ?
        GROUP BY users.id, users.name, subjects.id, subjects.subject_name
        ORDER BY users.name, subjects.subject_name
    """, (teacher_id,)).fetchall()

    # ONLY THIS TEACHER'S ATTENDANCE RECORDS
    attendance_records = conn.execute("""
        SELECT
            attendance.id,
            attendance.student_name,
            attendance.date,
            attendance.status,
            attendance.subject_id,
            subjects.subject_name
        FROM attendance
        INNER JOIN subjects
            ON attendance.subject_id = subjects.id
        WHERE subjects.teacher_id = ?
        ORDER BY attendance.date DESC, attendance.id DESC
    """, (teacher_id,)).fetchall()

    conn.close()

    student_data = []

    for student in students:

        total_days = student[2]
        present_days = student[3] or 0

        percentage = 0

        if total_days > 0:
            percentage = round(
                (present_days / total_days) * 100, 2
            )

        student_data.append({
            "id": student[0],
            "name": student[1],
            "total": total_days,
            "present": present_days,
            "percentage": percentage
        })

    subject_data = []

    for record in subject_summary:

        total = record[3]
        present = record[4]

        percentage = 0

        if total > 0:
            percentage = round(
                (present / total) * 100, 2
            )

        subject_data.append({
            "student_id": record[0],
            "student_name": record[1],
            "subject": record[2],
            "total": total,
            "present": present,
            "percentage": percentage
        })

    return render_template(
        "teacher_attendance.html",
        students=student_data,
        subjects=subjects,
        subject_summary=subject_data,
        attendance_records=attendance_records
    )





@app.route("/date-wise-attendance")
def date_wise_attendance():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    selected_date = request.args.get("date", "")

    records = []
    total_students = 0
    total_present = 0
    total_absent = 0
    attendance_percentage = 0

    if selected_date:
        conn = sqlite3.connect("attendance.db")

        records = conn.execute("""
            SELECT
                attendance.student_name,
                attendance.date,
                attendance.status,
                COALESCE(subjects.subject_name, 'Not Assigned')
            FROM attendance
            LEFT JOIN subjects
                ON attendance.subject_id = subjects.id
            WHERE attendance.date = ?
            ORDER BY attendance.student_name, subjects.subject_name
        """, (selected_date,)).fetchall()

        total_students = len(records)

        total_present = sum(
            1 for record in records
            if record[2] == "Present"
        )

        total_absent = total_students - total_present

        if total_students > 0:
            attendance_percentage = round(
                (total_present / total_students) * 100, 2
            )

        conn.close()

    return render_template(
        "date_wise_attendance.html",
        records=records,
        selected_date=selected_date,
        total_students=total_students,
        total_present=total_present,
        total_absent=total_absent,
        attendance_percentage=attendance_percentage
    )


@app.route("/download-date-attendance")
def download_date_attendance():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    selected_date = request.args.get("date")

    if not selected_date:
        return redirect(url_for("date_wise_attendance"))

    conn = sqlite3.connect("attendance.db")

    records = conn.execute("""
        SELECT
            attendance.student_name,
            COALESCE(subjects.subject_name, 'Not Assigned'),
            attendance.date,
            attendance.status
        FROM attendance
        LEFT JOIN subjects
            ON attendance.subject_id = subjects.id
        WHERE attendance.date = ?
        ORDER BY attendance.student_name, subjects.subject_name
    """, (selected_date,)).fetchall()

    conn.close()

    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    from io import BytesIO

    pdf_buffer = BytesIO()
    pdf = canvas.Canvas(pdf_buffer, pagesize=A4)

    width, height = A4

    # College heading
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawCentredString(width / 2, 800, "MATRUSRI ENGINEERING COLLEGE")

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawCentredString(width / 2, 770, "SMART CAMPUS")

    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawCentredString(width / 2, 735, "DATE-WISE ATTENDANCE REPORT")

    pdf.setStrokeColor(colors.black)
    pdf.line(40, 715, width - 40, 715)

    total_records = len(records)
    total_present = sum(1 for r in records if r[3] == "Present")
    total_absent = total_records - total_present

    percentage = round(
        (total_present / total_records) * 100, 2
    ) if total_records else 0

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(50, 680, f"Attendance Date: {selected_date}")

    pdf.drawString(50, 650, f"Total Records: {total_records}")
    pdf.drawString(230, 650, f"Present: {total_present}")
    pdf.drawString(380, 650, f"Absent: {total_absent}")

    pdf.drawString(50, 625, f"Attendance Percentage: {percentage}%")

    # Table heading
    y = 580

    pdf.setFillColor(colors.lightgrey)
    pdf.rect(45, y - 8, width - 90, 28, fill=1, stroke=1)

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 10)

    pdf.drawString(55, y, "S.No")
    pdf.drawString(95, y, "Student Name")
    pdf.drawString(225, y, "Subject")
    pdf.drawString(345, y, "Date")
    pdf.drawString(465, y, "Status")

    y -= 28

    pdf.setFont("Helvetica", 9)

    for i, record in enumerate(records, start=1):

        if y < 60:
            pdf.showPage()
            y = 780
            pdf.setFont("Helvetica", 9)

        pdf.rect(45, y - 10, width - 90, 25, fill=0, stroke=1)

        pdf.drawString(55, y, str(i))
        pdf.drawString(95, y, str(record[0])[:20])
        pdf.drawString(225, y, str(record[1])[:18])
        pdf.drawString(345, y, str(record[2]))
        pdf.drawString(465, y, str(record[3]))

        y -= 25

    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawCentredString(
        width / 2, 30,
        "Generated by Smart Campus Attendance System"
    )

    pdf.save()
    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=f"Attendance_Report_{selected_date}.pdf",
        mimetype="application/pdf"
    )

@app.route("/student-progress-report")
def student_progress_report():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")
    conn.row_factory = sqlite3.Row

    students_data = conn.execute("""
        SELECT id, name
        FROM users
        WHERE role = 'student'
    """).fetchall()

    total_activities = conn.execute("""
        SELECT COUNT(*) FROM activities
    """).fetchone()[0]

    students = []

    for student in students_data:

        attendance = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present
            FROM attendance
            WHERE student_name = ?
        """, (student["name"],)).fetchone()

        total = attendance["total"] or 0
        present = attendance["present"] or 0

        percentage = round((present / total) * 100, 2) if total else 0

        completed = conn.execute("""
            SELECT COUNT(*)
            FROM student_activities
            WHERE student_id = ? AND status = 'Completed'
        """, (student["id"],)).fetchone()[0]

        pending = max(0, total_activities - completed)

        activity_percentage = (
            round((completed / total_activities) * 100, 2)
            if total_activities else 0
        )

        students.append({
            "id": student["id"],
            "name": student["name"],
            "total": total,
            "present": present,
            "percentage": percentage,
            "total_activities": total_activities,
            "completed_activities": completed,
            "pending_activities": pending,
            "activity_percentage": activity_percentage
        })

    conn.close()

    return render_template(
        "student_progress_report.html",
        students=students,
        total_activities=total_activities,
        completed_activities=sum(s["completed_activities"] for s in students),
        pending_activities=sum(s["pending_activities"] for s in students)
    )


@app.route("/download-my-marks")
def download_my_marks():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        flash("Access denied! Students only.")
        return redirect("/teacher")

    conn = sqlite3.connect("attendance.db")

    student = conn.execute("""
        SELECT name FROM users WHERE id = ?
    """, (session["user_id"],)).fetchone()

    marks = conn.execute("""
        SELECT subject, exam_name, marks_obtained, max_marks
        FROM student_marks
        WHERE student_id = ?
        ORDER BY subject, exam_name
    """, (session["user_id"],)).fetchall()

    conn.close()

    total_obtained = sum(mark[2] for mark in marks)
    total_maximum = sum(mark[3] for mark in marks)

    percentage = (
        round((total_obtained / total_maximum) * 100, 2)
        if total_maximum > 0 else 0
    )

    pdf_buffer = BytesIO()
    pdf = canvas.Canvas(pdf_buffer, pagesize=A4)

    pdf.setTitle("Student Marks Report")

    # College heading
    pdf.setFillColorRGB(0.1, 0.2, 0.5)
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawCentredString(297, 800, "MATRUSRI ENGINEERING COLLEGE")

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawCentredString(297, 770, "SMART CAMPUS")

    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(297, 735, "STUDENT MARKS REPORT")

    # Student details
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, 690, f"Student Name: {student[0] if student else 'Student'}")
    pdf.drawString(50, 670, f"Report Date: {datetime.now().strftime('%d-%m-%Y')}")

    pdf.drawString(50, 640, f"Total Marks: {total_obtained} / {total_maximum}")
    pdf.drawString(50, 620, f"Overall Percentage: {percentage}%")

    # Table heading
    y = 580

    pdf.setFillColorRGB(0.1, 0.2, 0.5)
    pdf.rect(45, y - 5, 505, 25, fill=1, stroke=0)

    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 9)

    pdf.drawString(55, y + 3, "Subject")
    pdf.drawString(170, y + 3, "Exam")
    pdf.drawString(275, y + 3, "Obtained")
    pdf.drawString(365, y + 3, "Maximum")
    pdf.drawString(460, y + 3, "Percentage")

    y -= 30

    
    for mark in marks:

        if y < 70:
            pdf.showPage()
            y = 780

        subject, exam, obtained, maximum = mark
        mark_percentage = round((obtained / maximum) * 100, 2)

        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Helvetica", 9)

        pdf.drawString(55, y, str(subject)[:18])
        pdf.drawString(170, y, str(exam)[:15])
        pdf.drawString(275, y, str(obtained))
        pdf.drawString(365, y, str(maximum))
        pdf.drawString(460, y, f"{mark_percentage}%")

        pdf.setStrokeColorRGB(0.8, 0.8, 0.8)
        pdf.line(45, y - 8, 550, y - 8)

        y -= 28

    pdf.setFillColorRGB(0.1, 0.2, 0.5)
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawCentredString(297, 35, "Smart Campus - Student Academic Report")

    pdf.save()
    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name="My_Marks_Report.pdf",
        mimetype="application/pdf"
    )

@app.route("/teacher-notifications", methods=["GET", "POST"])
def teacher_notifications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "teacher":
        flash("Access denied! Teachers only.")
        return redirect(url_for("student_dashboard"))

    conn = sqlite3.connect("attendance.db")

    students = conn.execute("""
        SELECT id, name
        FROM users
        WHERE role = 'student'
        ORDER BY name
    """).fetchall()

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        message = request.form.get("message", "").strip()
        selected_student = request.form.get("student_id", "")

        if not title or not message:
            flash("Please enter the title and message.")

        elif selected_student == "all":
            conn.execute("""
                INSERT INTO notifications
                (student_id, title, message, teacher_id)
                VALUES (NULL, ?, ?, ?)
            """, (title, message, session["user_id"]))

            conn.commit()
            flash("Notification sent to all students!")

        else:
            try:
                student_id = int(selected_student)

                valid_student = conn.execute("""
                    SELECT id FROM users
                    WHERE id = ? AND role = 'student'
                """, (student_id,)).fetchone()

                if not valid_student:
                    flash("Invalid student selected.")
                else:
                    conn.execute("""
                        INSERT INTO notifications
                        (student_id, title, message, teacher_id)
                        VALUES (?, ?, ?, ?)
                    """, (
                        student_id,
                        title,
                        message,
                        session["user_id"]
                    ))

                    conn.commit()
                    flash("Notification sent successfully!")

            except (ValueError, TypeError):
                flash("Please select a valid student.")

        conn.close()
        return redirect(url_for("teacher_notifications"))

    notifications = conn.execute("""
        SELECT notifications.id,
               notifications.title,
               notifications.message,
               notifications.created_at,
               CASE
                   WHEN notifications.student_id IS NULL
                   THEN 'All Students'
                   ELSE users.name
               END
        FROM notifications
        LEFT JOIN users
        ON notifications.student_id = users.id
        WHERE notifications.teacher_id = ?
        ORDER BY notifications.id DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "teacher_notifications.html",
        students=students,
        notifications=notifications
    )

@app.route("/my-notifications")
def my_notifications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "student":
        flash("Access denied! Students only.")
        return redirect("/teacher")

    conn = sqlite3.connect("attendance.db")

    notifications = conn.execute("""
        SELECT title, message, created_at
        FROM notifications
        WHERE student_id = ? OR student_id IS NULL
        ORDER BY id DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "my_notifications.html",
        notifications=notifications
    )

@app.route("/admin")
def admin_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        flash("Access denied! Admins only.")
        return redirect(url_for("login"))

    conn = sqlite3.connect("attendance.db")

    total_students = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'student'"
    ).fetchone()[0]

    total_teachers = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'teacher'"
    ).fetchone()[0]

    total_attendance = conn.execute(
        "SELECT COUNT(*) FROM attendance"
    ).fetchone()[0]

    total_activities = conn.execute(
        "SELECT COUNT(*) FROM activities"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "admin.html",
        total_students=total_students,
        total_teachers=total_teachers,
        total_attendance=total_attendance,
        total_activities=total_activities
    )

@app.route("/admin-users")
def admin_users():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        flash("Access denied! Admins only.")
        return redirect(url_for("login"))

    conn = sqlite3.connect("attendance.db")

    students = conn.execute("""
        SELECT id, name, email
        FROM users
        WHERE role = 'student'
        ORDER BY name
    """).fetchall()

    teachers = conn.execute("""
        SELECT id, name, email
        FROM users
        WHERE role = 'teacher'
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "admin_users.html",
        students=students,
        teachers=teachers
    )

@app.route("/admin-delete-user/<int:user_id>", methods=["POST"])
def admin_delete_user(user_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        flash("Access denied! Admins only.")
        return redirect(url_for("login"))

    user_id = user_id

    conn = sqlite3.connect("attendance.db")

    user = conn.execute(
        "SELECT id, role FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user or user[1] not in ("student", "teacher"):
        flash("Invalid user.")
        conn.close()
        return redirect(url_for("admin_users"))

    if user[1] == "student":
        conn.execute(
            "DELETE FROM student_activities WHERE student_id = ?",
            (user_id,)
        )
        conn.execute(
            "DELETE FROM student_marks WHERE student_id = ?",
            (user_id,)
        )
        conn.execute(
            "DELETE FROM notifications WHERE student_id = ?",
            (user_id,)
        )

    elif user[1] == "teacher":
        conn.execute(
            "DELETE FROM student_marks WHERE teacher_id = ?",
            (user_id,)
        )
        conn.execute(
            "DELETE FROM notifications WHERE teacher_id = ?",
            (user_id,)
        )

    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    conn.commit()
    conn.close()

    flash("User account deleted successfully!")
    return redirect(url_for("admin_users"))



@app.route("/admin-attendance")
def admin_attendance():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        flash("Access denied! Admins only.")
        return redirect(url_for("login"))

    conn = sqlite3.connect("attendance.db")

    attendance_records = conn.execute("""
        SELECT
            attendance.id,
            attendance.student_name,
            attendance.date,
            attendance.status,
            COALESCE(subjects.subject_name, 'Not Assigned')
        FROM attendance
        LEFT JOIN subjects
            ON attendance.subject_id = subjects.id
        ORDER BY attendance.date DESC, attendance.id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin_attendance.html",
        attendance_records=attendance_records
    )


@app.route("/admin-notifications")
def admin_notifications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role") != "admin":
        flash("Access denied! Admins only.")
        return redirect(url_for("login"))

    conn = sqlite3.connect("attendance.db")

    notifications = conn.execute("""
        SELECT
            notifications.title,
            notifications.message,
            notifications.created_at,
            teachers.name,
            CASE
                WHEN notifications.student_id IS NULL
                THEN 'All Students'
                ELSE students.name
            END
        FROM notifications
        LEFT JOIN users AS teachers
            ON notifications.teacher_id = teachers.id
        LEFT JOIN users AS students
            ON notifications.student_id = students.id
        ORDER BY notifications.id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "admin_notifications.html",
        notifications=notifications
    )


if __name__ == "__main__":
    app.run(debug=True)

        