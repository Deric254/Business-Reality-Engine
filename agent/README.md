# Local agent

Runs on a computer inside the business, reads local data, pushes it **out** over HTTPS. No inbound port, nothing to open on the firewall. Standard library only.

1. In the app, create a connector of type `local_agent` with a mapping (which field of each row is the amount, the time, the id...). The token is shown once.
2. Copy `reality_agent.py` and a `config.json` (see the docstring at the top of the script) to the business computer.
3. Test: `python reality_agent.py config.json --dry-run`, then `--once`, then schedule it (Windows Task Scheduler / cron) or run it without flags to loop.

Sources: `csv_folder`, `sqlite`, `odbc` (needs `pip install pyodbc`), `command` (your own export script that prints one JSON object per line; this is how legacy programs, authorized screen automation or manual exports are covered).

Safety: state only advances after the server confirmed delivery; resending is harmless (events with an id are de-duplicated); plain `http` is refused except localhost; the `command` source never uses a shell.
