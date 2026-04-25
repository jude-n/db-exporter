# DB Exporter

A lightweight cross-platform (macOS/Windows/Linux) desktop app that:

1. Connects to a database (MySQL or Oracle)
2. Lists tables
3. Exports schema + data to per-table CSV or SQL files
4. Organises connections into **groups** (e.g. Production, Staging, Test) with colour coding
5. Saves the whole setup as a reusable **profile** for one-click re-export
6. Tracks export history and detects stale profiles automatically

## Stack

- **Python 3.13** recommended (3.11 and 3.12 also work).
- **FastAPI + uvicorn** — local API server powering the backend.
- **PyWebView** — wraps the UI in a native desktop window (no browser tab needed).
- **mysql-connector-python** — pure-Python MySQL driver.
- **oracledb (thin mode)** — Oracle driver that does *not* require the Oracle Instant Client.
- **keyring** — stores profile passwords in the OS keychain (macOS Keychain, Windows Credential Manager, GNOME Keyring).

## Install Python (if you don't have it)

The recommended version is **Python 3.13** on every OS. 3.12 and 3.11 also work.

If `python3 --version` prints `3.11.x`, `3.12.x`, or `3.13.x`, skip ahead to **Run**. Otherwise:

### macOS

```bash
brew install python@3.13
# Apple Silicon:  /opt/homebrew/bin/python3.13
# Intel Macs:     /usr/local/bin/python3.13
```

### Windows

Install **Python 3.13 (64-bit)** from https://python.org. During setup:

- Check **"Add python.exe to PATH"**

> Note: PyWebView on Windows requires **pythonnet** which needs the .NET SDK to build. Install Python 3.13 (not 3.14+) to get prebuilt wheels and avoid compilation issues.

### Linux

```bash
# Debian / Ubuntu
sudo apt-get install python3.13 python3.13-venv

# Fedora / RHEL
sudo dnf install python3.13

# Arch
sudo pacman -S python
```

## Run

```bash
cd db-exporter

# macOS (Apple Silicon):
/opt/homebrew/bin/python3.13 -m venv .venv
# macOS (Intel):
/usr/local/bin/python3.13 -m venv .venv
# Windows:
py -3.13 -m venv .venv
# Linux:
python3.13 -m venv .venv

source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

The app opens as a native desktop window — no browser tab, no terminal interaction required after launch.

## Folder layout

```
db-exporter/
├── main.py                 # Entry point — starts FastAPI server + opens PyWebView window
├── server.py               # FastAPI routes (connect, export, profiles, groups, progress, history)
├── run_history.json        # Created automatically after first export
├── exports/                # Created automatically — default export output location
├── ui/
│   └── index.html          # Web UI (Connection / Tables / Export / History tabs)
├── db/
│   ├── base.py             # BaseConnector + ColumnInfo
│   ├── mysql_conn.py       # MySQL implementation
│   ├── oracle_conn.py      # Oracle implementation
│   └── factory.py          # Dialect registry — add new DBs here
├── exporters/
│   ├── csv_exporter.py     # Streaming CSV export
│   └── sql_exporter.py     # CREATE TABLE + INSERT INTO SQL export
├── profiles/
│   ├── data/               # Profile JSON files (one per saved connection)
│   ├── groups/
│   │   └── groups.json     # Group registry
│   ├── manager.py          # Profile store
│   ├── groups.py           # Group registry manager
│   └── keyring_store.py    # OS keychain password store
├── requirements.txt
└── README.md
```

## How profiles and groups work

### Profiles

A profile is a JSON file in `profiles/data/` containing:

- `connection` — dialect/host/port/user/database (**password is never stored here**)
- `selected_tables` — tables ticked when the profile was saved
- `output_folder` — where export files are written
- `format` — export format (`csv` or `sql`)
- `group_id` — optional UUID linking to a group

Passwords are stored separately and securely in the OS keychain under the service name `db_exporter`, keyed by profile name.

### Groups

Groups let you organise profiles by environment (Production, Staging, Test, etc.) with colour coding so you never accidentally export the wrong database.

- Groups are stored in `profiles/groups/groups.json`
- Each group has a name, colour (hex), and optional base output folder
- Renaming a group updates all profiles in it automatically (linked by UUID not name)
- Clicking a group header selects all profiles inside it for batch export

### Sidebar interactions

| Action | Result |
|--------|--------|
| Single click profile | Toggle selection for export |
| Hover profile | Shows ⬇ ⎘ ✎ ✕ action buttons |
| ⬇ | Load connection details into the form |
| ⎘ | Copy profile (keeps same group) |
| ✎ | Edit profile name, group, or output folder |
| ✕ | Delete profile |
| Click group header checkbox | Select / deselect all profiles in group |
| Drag profile to group | Move profile to that group |
| Alt + drag | Copy profile to that group |

### One-click export

Select one or more profiles → click **One-Click Export** → the app connects to each DB in sequence, validates the table list, and writes all export files. Failed profiles are skipped and shown in the summary.

### Stale detection

After loading, the app silently checks each profile's saved tables against what the connected user can currently see. Profiles with missing or inaccessible tables show an amber dot — a sign the DB schema has changed or you're connecting with a different user.

## Export formats

### CSV (default)

For each selected table `FOO`:

- `FOO.csv` — data, first row is the header
- `FOO.schema.csv` — `column_name, data_type, nullable, default`

### SQL

For each selected table `FOO`:

- `FOO.sql` — dialect-aware `DROP TABLE`, `CREATE TABLE`, and `INSERT INTO` statements

SQL output handles MySQL and Oracle differences automatically:

| | MySQL | Oracle |
|---|---|---|
| Identifiers | `` `backticks` `` | `"double quotes"` |
| DROP | `DROP TABLE IF EXISTS` | PL/SQL `BEGIN / EXCEPTION` block |
| Transaction | `BEGIN; … COMMIT;` | `COMMIT;` only |

## Output folder structure

If a profile belongs to a group, exports go to:

```
<base_folder>/<group_name>/<profile_name>/
```

For example, a profile `prod-mysql` in group `Production` with base `~/exports` writes to:

```
~/exports/Production/prod-mysql/
```

Ungrouped (standalone) profiles use their own saved output folder.

## Export history

Every export is logged to `run_history.json` in the project root. The **History** tab in the app shows a timestamped log of every run with profile name, group, table count, output path, and status. You can clear the history from within the app.

## Security

- Passwords are stored in the OS keychain — never in JSON files or plain text
- The local API server only accepts requests from `127.0.0.1:5177` (CORS locked down)
- Output folder paths are validated to block path traversal attacks
- Profile names are sanitised to prevent filesystem injection
- Only one export can run at a time — simultaneous export attempts are rejected

## Extending

- **Add a new DB dialect**: implement `BaseConnector` (see `db/mysql_conn.py` as a template) and register it in `db/factory.py`.
- **Add a new exporter format**: create `exporters/<format>_exporter.py` and wire it into `server.py` and `ui/index.html`.
- **Cancel mid-export**: add a cancel token in `csv_exporter.py` and `sql_exporter.py` — roughly a 10-line change each.
- **Cross-schema Oracle access**: switch `user_tables` to `all_tables` in `db/oracle_conn.py` and add a schema filter.

## Packaging to a standalone app

First, install PyInstaller into your venv:

```bash
pip install pyinstaller
```

---

### macOS → `.app`

```bash
pyinstaller \
  --windowed \
  --onefile \
  --name "DBExporter" \
  --add-data "ui:ui" \
  main.py
```

The result is `dist/DBExporter.app`. Drag it to `/Applications` to install it. If macOS blocks it, right-click → Open → Open to bypass Gatekeeper the first time.

---

### Windows → `.exe`

```powershell
pyinstaller `
  --windowed `
  --onefile `
  --name "DBExporter" `
  --add-data "ui;ui" `
  main.py
```

Note: Windows uses a semicolon in `--add-data` instead of a colon. The result is `dist\DBExporter.exe` — double-click to launch, or pin to your taskbar.

---

### Linux → binary

```bash
pyinstaller \
  --windowed \
  --onefile \
  --name "DBExporter" \
  --add-data "ui:ui" \
  main.py
```

```bash
chmod +x dist/DBExporter
mv dist/DBExporter ~/.local/bin/DBExporter
```

To add to your application launcher:

```bash
cat > ~/.local/share/applications/dbexporter.desktop << EOF
[Desktop Entry]
Name=DB Exporter
Exec=$HOME/.local/bin/DBExporter
Type=Application
Categories=Utility;Development;
EOF
```

---

### Notes for all platforms

- Always build on the OS you are targeting — a Mac build will not run on Windows and vice versa.
- If PyWebView fails to render after bundling, install `pyinstaller-hooks-contrib` and rebuild:

```bash
pip install pyinstaller-hooks-contrib
pyinstaller --clean --windowed --onefile --name "DBExporter" --add-data "ui:ui" main.py
```
