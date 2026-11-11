
import sqlite3

def create_database():
    conn = sqlite3.connect("attendance.db")

    # Users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # Add role column to users table
    columns = [
        column[1]
        for column in conn.execute("PRAGMA table_info(users)").fetchall()
    ]

    if "role" not in columns:
        conn.execute("""
            ALTER TABLE users
            ADD COLUMN role TEXT NOT NULL DEFAULT 'student'
        """)

    # Attendance table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    # Activities table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    # Add category column to activities table
    columns = [
        column[1]
        for column in conn.execute("PRAGMA table_info(activities)").fetchall()
    ]

    if "category" not in columns:
        conn.execute("""
            ALTER TABLE activities
            ADD COLUMN category TEXT NOT NULL DEFAULT 'Assignment'
        """)

    # Student Activities table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            activity_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            FOREIGN KEY (student_id) REFERENCES users(id),
            FOREIGN KEY (activity_id) REFERENCES activities(id),
            UNIQUE(student_id, activity_id)
        )
    """)

    # Student Marks table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            exam_name TEXT NOT NULL,
            marks_obtained REAL NOT NULL,
            max_marks REAL NOT NULL,
            teacher_id INTEGER NOT NULL,
            FOREIGN KEY (student_id) REFERENCES users(id),
            FOREIGN KEY (teacher_id) REFERENCES users(id),
            UNIQUE(student_id, subject, exam_name)
        )
    """)

    # Student Notifications table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            teacher_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES users(id),
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    create_database()
    print("Database created successfully!")
