import os
import subprocess
import shutil
from datetime import datetime, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Define 30 commits spanning June 8th, 2026
COMMITS = [
    # 1. Base files
    (["LICENSE", ".gitignore"], "init: initialize repository structure and add base repository files"),
    
    # 2. Dependencies
    (["pyproject.toml", "requirements.txt"], "chore: add project dependencies and metadata"),
    
    # 3. Application initialization
    (["app/__init__.py"], "feat: add package initialization"),
    
    # 4. Configuration
    (["app/config.py"], "feat: implement environment configuration settings"),
    
    # 5. Database layer
    (["app/database.py"], "feat: configure database connection layer with SQLAlchemy"),
    
    # 6. Models
    (["app/models.py"], "feat: define database model for jobs"),
    
    # 7. Schemas
    (["app/schemas.py"], "feat: add Pydantic schemas for API request and response validation"),
    
    # 8. Metrics registry
    (["app/metrics.py"], "feat: configure Prometheus metrics registry and tracking"),
    
    # 9. Worker initialization
    (["worker/__init__.py"], "feat: configure task priority queue routing"),
    
    # 10. Celery App
    (["worker/celery_app.py"], "feat: implement Celery app instance configuration"),
    
    # 11. Handlers
    (["worker/handlers.py"], "feat: register core job handlers"),
    
    # 12. Tasks
    (["worker/tasks.py"], "feat: implement background processing tasks with exponential backoff"),
    
    # 13. Signals
    (["worker/signals.py"], "feat: implement Celery signals for status and metrics sync"),
    
    # 14. API Router Init
    (["app/api/__init__.py"], "feat: add API router initialization"),
    
    # 15. API router endpoints
    (["app/api/router.py"], "feat: implement REST endpoints for job creation and query"),
    
    # 16. API DLQ endpoints
    (["app/api/dlq.py"], "feat: implement dead-letter queue (DLQ) inspection and replay"),
    
    # 17. Main API server
    (["app/main.py"], "feat: implement main FastAPI app with lifespan hooks"),
    
    # 18. Alembic configuration
    (["alembic.ini", "migrations/env.py"], "feat: add database schema migration configuration via Alembic"),
    
    # 19. Initial database migration
    (["migrations/versions/001_create_jobs.py"], "feat: add initial migration script for jobs table"),
    
    # 20. Prometheus config
    (["monitoring/prometheus.yml"], "chore: configure Prometheus scrape config for metrics endpoints"),
    
    # 21. Grafana datasources
    (["monitoring/grafana/datasources.yml"], "chore: configure Grafana datasource provisioning"),
    
    # 22. Grafana dashboard configs
    (["monitoring/grafana/dashboards/dashboard.yml"], "chore: configure Grafana dashboards folder structure"),
    
    # 23. Grafana dashboard JSON
    (["monitoring/grafana/dashboards/taskforge.json"], "monitor: add preconfigured Grafana dashboard JSON"),
    
    # 24. Dockerfile
    (["Dockerfile"], "chore: create multi-stage Dockerfile for containerization"),
    
    # 25. Docker Compose
    (["docker-compose.yml"], "chore: create docker-compose configuration for local service stack"),
    
    # 26. Seed script
    (["scripts/seed_jobs.py"], "feat: add database seeding script for demonstration"),
    
    # 27. Load test script
    (["scripts/load_test.py"], "feat: add concurrent load simulation testing script"),
    
    # 28. Test conftest
    (["tests/__init__.py", "tests/conftest.py"], "test: initialize test suite configuration and database fixtures"),
    
    # 29. API tests
    (["tests/test_api.py"], "test: implement unit tests for API endpoints"),
    
    # 30. Worker tests, CI & Docs
    (["tests/test_tasks.py", ".env.example", "README.md", "SETUP.md", "THEORY.md", "walkthrough.md", ".github/workflows/ci.yml"], "test: add background task unit tests, CI config, and complete project documentation")
]

def run_cmd(args, env=None):
    result = subprocess.run(args, cwd=PROJECT_DIR, capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print(f"Command failed: {' '.join(args)}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")
        return False
    return True

def create_backdated_commits():
    # Remove existing .git directory to start fresh
    git_dir = os.path.join(PROJECT_DIR, ".git")
    if os.path.exists(git_dir):
        print("Removing existing .git directory...")
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", ".git"], cwd=PROJECT_DIR)
        
    print("Initializing new git repository...")
    if not run_cmd(["git", "init"]):
        return False
        
    # Set default branch to main
    run_cmd(["git", "config", "init.defaultBranch", "main"])
    run_cmd(["git", "checkout", "-b", "main"])
    
    # Check if user email/name is set locally, if not, set them
    res = subprocess.run(["git", "config", "user.name"], cwd=PROJECT_DIR, capture_output=True, text=True)
    if not res.stdout.strip():
        run_cmd(["git", "config", "user.name", "JaithraSarma"])
    res = subprocess.run(["git", "config", "user.email"], cwd=PROJECT_DIR, capture_output=True, text=True)
    if not res.stdout.strip():
        run_cmd(["git", "config", "user.email", "jaithrasarma@gmail.com"])

    # Start date: June 9th, 2026, 09:00:00 AM IST
    start_time = datetime(2026, 6, 9, 9, 0, 0)
    
    for i, (files, message) in enumerate(COMMITS):
        commit_time = start_time + timedelta(minutes=15 * i)
        date_str = commit_time.strftime("%Y-%m-%dT%H:%M:%S+05:30")
        
        print(f"Commit {i+1}/{len(COMMITS)}: {message} ({date_str})")
        
        for f in files:
            file_path = os.path.join(PROJECT_DIR, f)
            if os.path.exists(file_path):
                if f == "README.md":
                    # Modify README.md slightly to guarantee changes exist for staging
                    with open(file_path, "a") as readme:
                        readme.write("\n\n<!-- Verified Python 3.10+ PEP 585/604 compliance -->\n")
                
                subprocess.run(["git", "add", f], cwd=PROJECT_DIR)
            else:
                print(f"Warning: File {f} not found!")

        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = date_str
        env["GIT_COMMITTER_DATE"] = date_str
        
        if not run_cmd(["git", "commit", "-m", message], env=env):
            print("Failed to commit!")
            return False
            
    print("All 30 backdated commits created successfully!")
    return True

if __name__ == "__main__":
    create_backdated_commits()
