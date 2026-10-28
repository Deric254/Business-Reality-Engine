# Local agent

Runs on a computer inside the business, reads local data, pushes it **out** over HTTPS. No inbound port, nothing to open on the firewall. Standard library only (Python 3.9+).

## Your systems: smePro, Imara, Pharmacy ERP (any combination)

**For the owner (no Python, no commands):** in the app open **Data -> Your systems**, press **Connect** on the system, download the agent when asked (`RealityAgent.exe`), double-click it, paste the code on the page that opens, and press Connect. It finds the system's data by itself and the app then shows **Connected and synced**, or says plainly why not. It keeps working in the background and starts with the computer. Repeat for the other systems: paste each code into the same page, on whichever computer that system lives on; one agent serves all three.

**For you (publishing it):** the Windows build runs in GitHub (Actions -> "Windows agent" -> Run workflow, or push a tag like `agent-v2.0.0` to publish it as a release). Put the download link in the server setting `AGENT_DOWNLOAD_URL` and the app shows it to owners. The build has not been run on a real Windows machine from here: after the first build, open it once on a PC and check that the page opens and a system connects.

The agent reads each system's database **read-only** (it never writes to it and never stops the system) and keeps the engine's copy exactly equal to it.

*Command-line version (for technicians):* `python reality_agent.py add <code>`, `check`, `run`, `path <system> <file>`.

| System | Looked for in | Notes |
|---|---|---|
| smePro | `%APPDATA%\com.smepro.app\erp.db` | Encrypted. Needs its key file `erp.db.key` beside it and, once, `pip install sqlcipher3-wheels`. Several businesses in one database: the app asks which one. |
| Imara | `~/.imara/imara.db` | Money received (invoice payments) and money paid out. |
| Pharmacy ERP | `%LOCALAPPDATA%\PharmacyERP\pharmacy.db`, `~/.pharmacy-erp/pharmacy.db` | Sales net of refunds, refunds, received purchases. |

Other Windows users on the same computer are searched too. If several copies exist, the newest valid one is used and all are listed.

**What is sent, and how it stays right.** Every pass reads inside one read transaction, so a sale and its refund are always seen together. Only new or edited records are sent; deletions are sent as deletions. Each pass ends with control totals (count and sum per kind of record) that the engine compares with what it stored; any difference triggers a full resend that also removes strays. A change in the system's files is noticed within seconds; the engine is told every 30 seconds the agent is alive, so a computer that is switched off or an agent that stopped raises an alert within minutes, and the alert closes itself when the link returns.

**When a system changes.** Everything specific to a system lives in `profiles/<system>.json` (where its database is, how to recognise it, what each figure means). Every statement is test-compiled against the real database each pass: if the system renames something, the app says *the system has changed*, nothing is read as "empty", and what the engine already holds is left alone. A newer system version than the one verified gives a notice, not a stop. To adapt without touching code, put a corrected profile of the same name in `profiles.d/` next to `config.json`; it replaces the built-in one.

**Start with the computer.** `RealityAgent.exe` registers itself for the signed-in Windows user when a system is connected (no administrator rights). `RealityAgent.exe --remove-startup` removes it. Its log is `~/.reality-agent/agent.log`.

Settings (optional, in `~/.reality-agent/config.json`): `poll_seconds` (20), `heartbeat_seconds` (30), `force_pass_seconds` (600, a full verified pass at least this often).

## Other data (CSV folder, plain SQLite query, ODBC, your own script)

Runs on a computer inside the business, reads local data, pushes it **out** over HTTPS. No inbound port, nothing to open on the firewall. Standard library only.

1. In the app, create a connector of type `local_agent` with a mapping (which field of each row is the amount, the time, the id...). The token is shown once.
2. Copy `reality_agent.py` and a `config.json` (see the docstring at the top of the script) to the business computer.
3. Test: `python reality_agent.py config.json --dry-run`, then `--once`, then schedule it (Windows Task Scheduler / cron) or run it without flags to loop.

Sources: `csv_folder`, `sqlite`, `odbc` (needs `pip install pyodbc`), `command` (your own export script that prints one JSON object per line; this is how legacy programs, authorized screen automation or manual exports are covered).

Safety: state only advances after the server confirmed delivery; resending is harmless (events with an id are de-duplicated); plain `http` is refused except localhost; the `command` source never uses a shell.
