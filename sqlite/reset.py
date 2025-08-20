import os
import sqlite3

base_dir = os.path.dirname(__file__)
db_path = os.path.join(base_dir, "fcLibrary.db")
sql_file = os.path.join(base_dir, "CREATE.sql")

# Remove the database file if it exists
if os.path.exists(db_path):
    os.remove(db_path)

# Read SQL commands from file
with open(sql_file, "r", encoding="utf-8") as f:
    sql_script = f.read()

# Create new database and execute SQL script
conn = sqlite3.connect(db_path)
conn.executescript(sql_script)
conn.close()

print("Reset!")