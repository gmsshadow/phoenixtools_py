# Phoenixtools (Python Desktop Rewrite)

Local-first desktop rewrite of the original Ruby/Rails Phoenixtools.

## Status

Work in progress. See `FEATURE_MAP.md` for parity targets.

## Development setup

```powershell
cd phoenixtools_py
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
phoenixtools
```

## Using the app

- **Home**: run “Daily refresh (market)” to import current market buys/sells.
- **Configuration**: set `user_id` + `xml_code`, then run “setup import” to pull `info_data` + `pos_list`.
- **Trade routes**: shows simple profit-ranked routes based on current market data.

## Build a Windows executable

```powershell
cd phoenixtools_py
.\packaging\build_windows.ps1
```

