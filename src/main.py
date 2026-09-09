"""Run the full pipeline in one process: scrape activities, scrape members,
then generate the dashboard.

    python -m src.main

The one-off browser login is separate: python -m src.login
Each step raises on failure, so the first failure stops the pipeline.
"""
import subprocess
from datetime import datetime
from pathlib import Path

from .activities.activities import ActivityScraper
from .dashboard import generate
from .members.members import MemberScraper
from .strava_session import ScrapeError

REPO_ROOT = Path(__file__).parent.parent
INDEX_HTML = REPO_ROOT / "index.html"


def publish_dashboard() -> None:
    """Commit and push index.html so GitHub Actions redeploys the page.

    No-op when index.html is unchanged.
    """
    subprocess.run(["git", "add", str(INDEX_HTML)], cwd=REPO_ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT).returncode == 0:
        print("index.html unchanged, nothing to publish.")
        return
    message = f"🏃 Dashboard update {datetime.now():%Y-%m-%d %H:%M}"
    subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
    print("Pushed index.html.")


def main() -> None:
    try:
        print("=== scrape activities ===", flush=True)
        ActivityScraper().scrape()

        print("\n=== scrape members ===", flush=True)
        MemberScraper().scrape()
    except ScrapeError as e:
        raise SystemExit(f"scrape failed, pipeline stopped: {e}")

    print("\n=== generate dashboard ===", flush=True)
    generate.run()

    print("\n=== publish dashboard ===", flush=True)
    publish_dashboard()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
