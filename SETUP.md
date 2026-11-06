# 3DS-RPC Setup Instructions

## 1. Configure environment variables and secrets

First, copy the template:

```
cp api/template.private.py api/private.py
```

Open `api/private.py` and fill in all required secrets and configuration values as described in the file’s comments (e.g., DB_URL, CLIENT_ID, CLIENT_SECRET, HOST, etc).

Adjust the values as needed for your environment.

## 2. (Recommended) Create and activate a Python virtual environment

Open a terminal in the project root and run:

### On Windows:
```
python -m venv .venv
.venv\Scripts\activate
```

### On Linux/macOS:
```
python3 -m venv .venv
source .venv/bin/activate
```

This will create and activate a virtual environment for your dependencies.

## 3. Install Python dependencies

With the virtual environment activated, run:

```
pip install -r requirements.txt
```

### Development tooling (optional)

To also install the linting tooling, use the development requirements file:

```
pip install -r requirements-dev.txt
```

Then check the codebase from the project root:

```
ruff check .
```

## 4. Initialize the database

Run the database reset script from the project root:

### On Windows:
```
python sqlite/reset.py
```

### On Linux/macOS:
```
python3 sqlite/reset.py
```

This will create or reset the database using the schema in CREATE.sql.

For PostgreSQL, point `DATABASE_URL` at the database and start the web app. On startup it creates a fresh schema at the latest revision, or applies any pending migrations to an existing one; `CREATE.sql` is only needed for the SQLite quickstart.

---

## 5. Run the backend

From the project root, start the backend for your desired network:

```
python backend.py --network nintendo
```

or

```
python backend.py --network pretendo
```

Replace `nintendo` or `pretendo` as needed.

---

You are now ready to run the backend!
