import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPLACEMENTS = {
    "task-sentinel-api": "taskforge-api",
    "task-sentinel-worker": "taskforge-worker",
    "task-sentinel-redis": "taskforge-redis",
    "task-sentinel-db": "taskforge-db",
    "task-sentinel-prometheus": "taskforge-prometheus",
    "task-sentinel-grafana": "taskforge-grafana",
    "task-sentinel-flower": "taskforge-flower",
    "task-sentinel-dlq": "taskforge-dlq",
    "task-sentinel-multiproc": "taskforge-multiproc",
    "task-sentinel": "taskforge",
    "task_sentinel": "task_forge",
    "TaskSentinel": "TaskForge",
    "Task-Sentinel": "TaskForge"
}

EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
EXCLUDE_FILES = {"rename_project.py", "git_builder.py", "test.db"}

def rename_in_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        # Skip binary files
        return

    updated_content = content
    modified = False
    for target, replacement in REPLACEMENTS.items():
        if target in updated_content:
            updated_content = updated_content.replace(target, replacement)
            modified = True

    if modified:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"Updated contents: {os.path.relpath(file_path, PROJECT_DIR)}")

def walk_and_rename():
    for root, dirs, files in os.walk(PROJECT_DIR):
        # Prune excluded directories in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for file in files:
            if file in EXCLUDE_FILES:
                continue
            file_path = os.path.join(root, file)
            rename_in_file(file_path)

if __name__ == "__main__":
    print("Starting project renaming process...")
    walk_and_rename()
    print("Project renaming complete!")
