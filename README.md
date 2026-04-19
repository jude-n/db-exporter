# DB Exporter

A lightweight cross-platform (macOS/Windows/Linux) desktop app that:

1. Connects to a database (MySQL or Oracle)
2. Lists tables
3. Exports schema + data to per-table CSV or SQL files
4. Saves the whole setup as a reusable **profile** for one-click re-export

## Stack

- **Python 3.11 – 3.13** recommended (3.10 works but is reaching end-of-life).
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

### Linux

```bash
# Debian / Ubuntu
sudo apt-get install python3.13 python3.13-venv

# Fedora / RHEL
sudo dnf install python3.13

# Arch
sudo pacman -S python
```

If your distro doesn't ship 3.13 yet, 3.11 or 3.12 is fine.

## Run

```bash
cd db_exporter

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
db_exporter/
├── main.py                 # Entry point — starts FastAPI server + opens PyWebView window
├── server.py               # FastAPI routes (connect, export, profiles, progress)
├── ui/
│   └── index.html          # Web UI (Connection / Tables / Export tabs)
├── db/
│   ├── base.py             # BaseConnector + ColumnInfo
│   ├── mysql_conn.py       # MySQL implementation
│   ├── oracle_conn.py      # Oracle implementation
│   └── factory.py          # dialect registry — add new DBs here
├── exporters/
│   ├── csv_exporter.py     # streaming CSV export
│   └── sql_exporter.py     # CREATE TABLE + INSERT INTO SQL export
├── profiles/
│   ├── manager.py          # JSON profile store (~/.db_exporter/profiles/)
│   └── keyring_store.py    # OS keychain password store
├── requirements.txt
└── README.md
```

## How a profile works

A profile is a JSON file at `~/.db_exporter/profiles/<name>.json` containing:

- `connection` — dialect/host/port/user/database (**password is not stored here**)
- `selected_tables` — tables ticked when the profile was saved
- `output_folder` — where export files are written
- `format` — export format (`csv` or `sql`)

Passwords are stored separately and securely in the OS keychain under the service name `db_exporter`, keyed by profile name.

**One-click repeat export**: pick a profile → click **Run (One-Click Export)** → the app connects, validates the table list, and writes all export files.

## Export formats

### CSV (default)

For each selected table `FOO`:

- `FOO.csv` — data, first row is the header
- `FOO.schema.csv` — `column_name, data_type, nullable, default`

### SQL

For each selected table `FOO`:

- `FOO.sql` — `DROP TABLE IF EXISTS`, `CREATE TABLE`, and `INSERT INTO` statements wrapped in a transaction

## Extending

- **Add a new DB dialect**: implement `BaseConnector` (see `db/mysql_conn.py` as a template) and register it in `db/factory.py`.
- **Add a new exporter format**: create `exporters/<format>_exporter.py` and add the format option to `server.py` and `ui/index.html`.
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

- `--windowed` hides the terminal window
- `--add-data "ui:ui"` bundles the `ui/` folder inside the app

The result is `dist/DBExporter.app`. Drag it to `/Applications` to install it like any other Mac app. You can also double-click it directly from `dist/`.

If macOS says the app is from an unidentified developer, right-click → Open → Open to bypass Gatekeeper the first time.

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

Note: Windows uses a semicolon in `--add-data` instead of a colon.

The result is `dist\DBExporter.exe`. You can move it anywhere — double-click to launch. To make it feel more native, create a shortcut and pin it to your taskbar.

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

The result is `dist/DBExporter`. Make it executable and move it somewhere on your PATH:

```bash
chmod +x dist/DBExporter
mv dist/DBExporter ~/.local/bin/DBExporter
```

To add it to your application launcher, create a `.desktop` file:

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
- The `dist/` folder also contains a `DBExporter/` directory (non-onefile build) — you only need the single file or app.
- If PyWebView fails to render after bundling, install `pyinstaller-hooks-contrib` and rebuild:

```bash
pip install pyinstaller-hooks-contrib
pyinstaller --clean --windowed --onefile --name "DBExporter" --add-data "ui:ui" main.py
```
