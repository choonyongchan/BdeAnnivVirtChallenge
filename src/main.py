"""Run the full pipeline in one process: scrape activities, scrape members,
then generate the dashboard.

    python -m src.main

The one-off browser login is separate: python -m src.login
Each step raises on failure, so the first failure stops the pipeline.
"""
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from .activities.activities import ActivityScraper
from .dashboard import generate
from .members.members import MemberScraper
from .strava_session import AUTH_PATH, ScrapeError

REPO_ROOT = Path(__file__).parent.parent
INDEX_HTML = REPO_ROOT / "index.html"
USER_COUNT_JSON = REPO_ROOT / "user-count.json"

REAUTH_MSG = (
    "\n==================== STRAVA RE-AUTH REQUIRED ====================\n"
    "The saved Strava session is missing or expired.\n\n"
    "  1. python -m src.login          # opens a browser, log in\n"
    "  2. Re-encode the session:\n"
    "     PowerShell: [Convert]::ToBase64String([IO.File]::ReadAllBytes('src/auth_state.json'))\n"
    "     bash:       base64 -w0 src/auth_state.json\n"
    "  3. Paste the result into the GitHub Actions secret  AUTH_STATE\n"
    "===============================================================\n"
)


def check_auth() -> None:
    """Fail fast, before launching a browser, if the saved Strava session is
    absent or malformed. An expired but well-formed session still gets past this
    and is caught by the scrape itself; both paths exit non-zero with REAUTH_MSG.
    """
    try:
        state = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(REAUTH_MSG)
    if not state.get("cookies"):
        raise SystemExit(REAUTH_MSG)


def publish_dashboard() -> None:
    """Commit and push index.html (and its user-count.json badge data) so
    GitHub Actions redeploys the page.

    No-op when index.html is unchanged.
    """
    subprocess.run(["git", "add", str(INDEX_HTML), str(USER_COUNT_JSON)], cwd=REPO_ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT).returncode == 0:
        print("index.html unchanged, nothing to publish.")
        return
    message = f"🏃 Dashboard update {datetime.now():%Y-%m-%d %H:%M}"
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Pushed index.html.")


def report_counts_to_ci(new_activities: int, new_members: int) -> None:
    """Expose the new-row counts as step outputs, so the workflow can put them
    in the ledger commit message. No-op outside GitHub Actions."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as f:
        f.write(f"new_activities={new_activities}\n")
        f.write(f"new_members={new_members}\n")


def main() -> None:
    check_auth()
    try:
        print("=== scrape activities ===", flush=True)
        new_activities = ActivityScraper().scrape()

        print("\n=== scrape members ===", flush=True)
        new_members = MemberScraper().scrape()
    except ScrapeError as e:
        raise SystemExit(f"scrape failed, pipeline stopped: {e}\n{REAUTH_MSG}")

    report_counts_to_ci(new_activities, new_members)

    print("\n=== generate dashboard ===", flush=True)
    generate.run()

    print("\n=== publish dashboard ===", flush=True)
    publish_dashboard()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
