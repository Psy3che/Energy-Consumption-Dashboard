import argparse
import concurrent.futures
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

import duckdb


ROOT = Path(__file__).parent
DATA_DIR = ROOT / "Data"
DATABASE_PATH = DATA_DIR / "player_data.duckdb"
RAW_BASE = "https://raw.githubusercontent.com/salimt/football-datasets/main/"
LFS_BASE = "https://media.githubusercontent.com/media/salimt/football-datasets/main/"

TABLES = {
    "player_profiles": ("player_profiles/player_profiles.csv", False),
    "player_performances": ("player_performances/player_performances.csv", True),
    "player_market_values": ("player_market_value/player_market_value.csv", False),
    "player_injuries": ("player_injuries/player_injuries_updated_20260929.csv", False),
    "player_national_performances": ("player_national_performances/player_national_performances.csv", False),
    "player_teammates": ("player_teammates_played_with/player_teammates_played_with.csv", False),
    "player_transfers": ("transfer_history/transfer_history.csv", True),
    "team_details": ("team_details/team_details.csv", False),
    "team_competitions_seasons": ("team_competitions_seasons/team_competitions_seasons.csv", False),
    "team_children": ("team_children/team_children.csv", False),
}


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


def normalize_column(value):
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") or "column"


def download_table(item, directory):
    table_name, (relative_path, is_lfs) = item
    base = LFS_BASE if is_lfs else RAW_BASE
    target = directory / f"{table_name}.csv"
    if target.exists() and target.stat().st_size > 1000:
        with target.open("rb") as source:
            if not source.read(80).startswith(b"version https://git-lfs.github.com/spec/v1"):
                return table_name, target
        target.unlink()
    request = urllib.request.Request(
        base + "datalake/transfermarkt/" + relative_path,
        headers={"User-Agent": "Touchline-Student-Dashboard/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)

    with target.open("rb") as source:
        if source.read(80).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise RuntimeError(f"Git LFS returned a pointer instead of data for {table_name}.")
    return table_name, target


def add_clean_views(connection):
    def column(table_name, *candidates):
        available = {row[0] for row in connection.execute(f"DESCRIBE {quote_identifier(table_name)}").fetchall()}
        match = next((candidate for candidate in candidates if candidate in available), None)
        return f"source.{quote_identifier(match)}" if match else "NULL"

    def typed_view(view_name, table_name, fields):
        expressions = []
        for output_name, candidates, data_type in fields:
            value = column(table_name, *candidates)
            value_as_text = f"CAST({value} AS VARCHAR)"
            if data_type == "text":
                expression = f"NULLIF(TRIM({value_as_text}), '')"
            elif data_type == "date":
                expression = f"TRY_CAST({value_as_text} AS DATE)"
            elif data_type == "flex_date":
                expression = f"COALESCE(TRY_CAST({value_as_text} AS DATE), TRY_CAST(TRY_STRPTIME({value_as_text}, '%d.%m.%Y') AS DATE))"
            elif data_type == "day_first_date":
                expression = f"TRY_CAST(TRY_STRPTIME({value_as_text}, '%d.%m.%Y') AS DATE)"
            elif data_type == "double":
                cleaned = f"REPLACE(REGEXP_REPLACE(COALESCE({value_as_text}, ''), '[^0-9,.-]', '', 'g'), ',', '')"
                expression = f"TRY_CAST(NULLIF({cleaned}, '') AS DOUBLE)"
            else:
                cleaned = f"REPLACE(REGEXP_REPLACE(COALESCE({value_as_text}, ''), '[^0-9,.-]', '', 'g'), ',', '')"
                expression = f"TRY_CAST(TRY_CAST(NULLIF({cleaned}, '') AS DOUBLE) AS BIGINT)"
            expressions.append(f"{expression} AS {quote_identifier(output_name)}")
        query = (
            f"CREATE OR REPLACE VIEW {quote_identifier(view_name)} AS SELECT {', '.join(expressions)} "
            f"FROM {quote_identifier(table_name)} AS source"
        )
        connection.execute(query)

    connection.execute("DROP VIEW IF EXISTS club_seasons")
    typed_view("performance", "player_performances", [
        ("player_id", ("player_id",), "integer"),
        ("season", ("season", "season_name"), "text"),
        ("competition_name", ("competition_name",), "text"),
        ("team_name", ("team_name",), "text"),
        ("squad_matches", ("nb_in_group",), "integer"),
        ("appearances", ("nb_on_pitch",), "integer"),
        ("goals", ("goals",), "integer"),
        ("assists", ("assists",), "integer"),
        ("own_goals", ("own_goals",), "integer"),
        ("substitute_appearances", ("subed_in",), "integer"),
        ("yellow_cards", ("yellow_cards",), "integer"),
        ("second_yellow_cards", ("second_yellow_cards",), "integer"),
        ("red_cards", ("direct_red_cards", "red_cards"), "integer"),
        ("penalty_goals", ("penalty_goals",), "integer"),
        ("minutes_played", ("minutes_played",), "integer"),
        ("goals_conceded", ("goals_conceded",), "integer"),
        ("clean_sheets", ("clean_sheets",), "integer"),
    ])
    typed_view("profile", "player_profiles", [
        ("player_id", ("player_id",), "integer"),
        ("player_name", ("player_name",), "text"),
        ("full_name", ("full_name",), "text"),
        ("date_of_birth", ("date_of_birth",), "text"),
        ("citizenship", ("citizenship",), "text"),
        ("position", ("position",), "text"),
        ("main_position", ("player_main_position", "main_position"), "text"),
        ("sub_position", ("player_sub_position", "sub_position"), "text"),
        ("foot", ("foot",), "text"),
        ("height", ("height",), "text"),
        ("current_club", ("current_club",), "text"),
        ("player_slug", ("player_slug",), "text"),
        ("player_image_url", ("player_image_url",), "text"),
    ])
    typed_view("market_values", "player_market_values", [
        ("player_id", ("player_id",), "integer"),
        ("value_date", ("date_unix", "date"), "date"),
        ("value_eur", ("value",), "double"),
    ])
    typed_view("injuries", "player_injuries", [
        ("player_id", ("player_id",), "integer"),
        ("season", ("season", "season_name"), "text"),
        ("injury_reason", ("injury_reason",), "text"),
        ("from_date", ("from_date",), "flex_date"),
        ("end_date", ("end_date",), "flex_date"),
        ("days_missed", ("days_missed",), "integer"),
        ("games_missed", ("games_missed",), "integer"),
    ])
    typed_view("national_performances", "player_national_performances", [
        ("player_id", ("player_id",), "integer"),
        ("team_id", ("team_id",), "integer"),
        ("matches", ("matches",), "integer"),
        ("goals", ("goals",), "integer"),
        ("debut", ("debut",), "date"),
        ("career_state", ("career_state",), "text"),
    ])
    typed_view("transfers", "player_transfers", [
        ("player_id", ("player_id",), "integer"),
        ("season", ("season", "season_name"), "text"),
        ("transfer_date", ("date_unformatted", "transfer_date", "date"), "date"),
        ("from_team", ("from_team_name", "from_team"), "text"),
        ("to_team", ("to_team_name", "to_team"), "text"),
        ("value_at_transfer", ("value_at_transfer",), "text"),
        ("transfer_fee", ("transfer_fee",), "text"),
        ("transfer_id", ("transfer_id",), "integer"),
    ])
    typed_view("teammate_links", "player_teammates", [
        ("player_id", ("player_id",), "integer"),
        ("teammate_id", ("teammate_player_id",), "integer"),
        ("teammate_name", ("teammate_player_name", "player_with_name", "teammate_name"), "text"),
        ("points_per_game", ("ppg_played_with",), "double"),
        ("joint_goal_participation", ("joint_goal_participation",), "integer"),
        ("minutes_played_together", ("minutes_played_with",), "integer"),
    ])
    typed_view("club_seasons_base", "team_competitions_seasons", [
        ("club_id", ("club_id",), "integer"),
        ("team_name", ("team_name", "club_name"), "text"),
        ("competition_name", ("competition_name",), "text"),
        ("season", ("season_season", "season", "season_name"), "text"),
        ("rank", ("season_rank", "rank"), "integer"),
        ("played", ("season_total_matches", "total_matches"), "integer"),
        ("wins", ("season_wins", "wins"), "integer"),
        ("draws", ("season_draws", "draws"), "integer"),
        ("losses", ("season_losses", "losses"), "integer"),
        ("goals_for", ("season_goals_for", "goals_for"), "integer"),
        ("goals_against", ("season_goals_against", "goals_against"), "integer"),
        ("points", ("season_points", "points"), "integer"),
        ("two_point_system", ("season_is_two_point_system",), "text"),
    ])
    connection.execute("""
        CREATE OR REPLACE VIEW club_seasons AS
        SELECT
            club_id,
            team_name,
            competition_name,
            season,
            rank,
            played,
            wins,
            draws,
            losses,
            goals_for,
            goals_against,
            COALESCE(
                points,
                CASE WHEN LOWER(two_point_system) IN ('true', '1', 'yes')
                    THEN wins * 2 + draws
                    ELSE wins * 3 + draws
                END
            ) AS points
        FROM club_seasons_base
    """)


def build_database(force=False, keep_source=False):
    if DATABASE_PATH.exists() and not force:
        raise FileExistsError(f"{DATABASE_PATH.name} already exists. Pass --force to rebuild it.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = DATA_DIR / ".transfermarkt-cache"
    cache_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".football-build-", dir=DATA_DIR) as temp_name:
        temp_dir = Path(temp_name)
        temp_database = temp_dir / DATABASE_PATH.name
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            downloads = dict(executor.map(lambda item: download_table(item, cache_dir), TABLES.items()))

        connection = duckdb.connect(str(temp_database))
        connection.execute("SET preserve_insertion_order = false")
        for table_name, csv_path in downloads.items():
            schema = connection.execute(
                "DESCRIBE SELECT * FROM read_csv_auto(?, header=true, all_varchar=true, quote='\"', escape='\"')",
                [str(csv_path)],
            ).fetchall()
            used_names = set()
            selections = []
            for column in schema:
                original_name = column[0]
                clean_name = normalize_column(original_name)
                if clean_name in used_names:
                    clean_name = f"{clean_name}_{len(used_names)}"
                used_names.add(clean_name)
                selections.append(f"{quote_identifier(original_name)} AS {quote_identifier(clean_name)}")
            projection = ", ".join(selections)
            connection.execute(
                f"CREATE TABLE {quote_identifier(table_name)} AS SELECT {projection} FROM read_csv_auto(?, header=true, all_varchar=true, quote='\"', escape='\"')",
                [str(csv_path)],
            )
            count = connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(table_name)}").fetchone()[0]
            print(f"Imported {table_name}: {count:,} rows")

        add_clean_views(connection)
        connection.execute("CHECKPOINT")
        connection.close()
        temp_database.replace(DATABASE_PATH)

    if not keep_source:
        shutil.rmtree(cache_dir, ignore_errors=True)
    print(f"\nDataset ready: {DATABASE_PATH}")


def refresh_database_views():
    if not DATABASE_PATH.exists():
        raise FileNotFoundError(f"{DATABASE_PATH.name} is missing; run setup_dataset.py first.")
    connection = duckdb.connect(str(DATABASE_PATH))
    add_clean_views(connection)
    connection.execute("CHECKPOINT")
    connection.close()
    print("Clean analytics views refreshed.")


def main():
    parser = argparse.ArgumentParser(description="Download and index the Transfermarkt football dataset.")
    parser.add_argument("--force", action="store_true", help="replace the existing local DuckDB database")
    parser.add_argument("--keep-source", action="store_true", help="keep downloaded CSVs for faster rebuilds")
    parser.add_argument("--refresh-views", action="store_true", help="rebuild clean analytics views from the existing database")
    arguments = parser.parse_args()
    if arguments.refresh_views:
        refresh_database_views()
    else:
        build_database(force=arguments.force, keep_source=arguments.keep_source)


if __name__ == "__main__":
    main()