# Touchline: Global Player Intelligence

An interactive football analytics and visualization project built with Dash, Plotly, Pandas, DuckDB, and Three.js. It brings together player profiles, season performances, valuations, transfers, injuries, national-team records, teammate connections, and club seasons.

## Dataset

Source: [salimt/football-datasets](https://github.com/salimt/football-datasets), a Transfermarkt-derived football data lake. The imported snapshot has 5,560,709 rows across 10 source tables, including 1,878,719 player-season performance records and 92,671 player profiles.

The source repository does not clearly specify a data license. The project links to the source, and `.gitignore` excludes downloaded CSVs and the generated DuckDB database. Verify the source's usage rights before redistributing or deploying the data. The repository's MIT license applies to project code, not automatically to the dataset.

The source contains season-level aggregates, not match-by-match event data. Per-90 metrics are derived from recorded totals and minutes. The 3D pitch groups teammates by their listed player roles; it does not claim to show a specific match formation or actual on-pitch coordinates.

## Dashboard

- **Performance:** filter by season, competition, position, and searchable player selection. Select one or multiple players; selected players remain selected while searching. Rank by goals, assists, minutes, appearances, clean sheets, cards, or per-90 rates. Charts and the server-paginated performance table use the same filters.
- **Player 360:** browse profiles, career totals, market-value history, season output, injuries, transfers, and teammate links.
- **3D teammate pitch:** orbit and zoom a role-based Three.js pitch, inspect teammate details, switch between 3D and top-down views, and size nodes by shared goals or minutes.
- **Market & transfers:** compare recorded valuations, value trends, transfer activity, and common destinations.
- **Injuries & availability:** examine injury categories, cases, missed days/games, and yearly trends.
- **National & clubs:** compare international appearances/goals and club-season standings.
- **Loading and motion:** expensive views show local loading indicators; charts transition on updates and the page entrance animation respects reduced-motion preferences.

## Run locally

Requires Python 3.10 or later, an internet connection for the initial download, and several hundred megabytes of free disk space.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python setup_dataset.py
python app.py
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050). The setup script downloads the source tables, builds `Data/player_data.duckdb`, and removes temporary CSV downloads after a successful build. Use `python setup_dataset.py --force` to rebuild the database, or `python setup_dataset.py --refresh-views` to refresh analytics views without downloading the tables again.

## Deployment

The `Procfile` starts Gunicorn with `app:server`. Build or provide `Data/player_data.duckdb` before starting the web process, and use hosting with persistent storage for the database. Confirm the platform's storage and memory limits can support the full dataset.

## Authors

Linhuaqian, University of Debrecen, Data Visualization Lab.