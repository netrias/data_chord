import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "design_review.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_pull_request_report(tmp_path: Path) -> None:
    # Given two committed revisions with existing strings, a rename, and one changed function.
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    module = tmp_path / "src" / "app" / "example.py"
    module.parent.mkdir(parents=True)
    module.write_text('STABLE = "unchanged"\n')
    renamed = tmp_path / "old.py"
    renamed.write_text('STABLE = "only moved"\n')
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    renamed.rename(tmp_path / "new name.py")
    module.write_text(module.read_text() + '''
def check(file_id: str, status: HarmonizeStatus, column_key: ColumnKey):
    """Documentation only."""
    for row in rows:
        print("once")
    return [row for row in rows if status == "suceeded"]

@router.get(ROUTE)
def http_handler(file_id: str):
    def helper(file_id: str):
        return file_id
    return helper(file_id)

def separate(file_id: DatasetWorkflowId):
    for row in rows:
        def inside():
            return [value for value in row]
''')
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")
    module.write_text('raise Exception("uncommitted")\n')

    # When the same command that CI runs compares the committed revisions.
    report = subprocess.check_output([sys.executable, SCRIPT, "HEAD^", "HEAD"], cwd=tmp_path, text=True)

    # Then one-off literals, known domain types, and both iterations are attributed to their function.
    assert "once" in report and "suceeded" in report
    assert "file_id → DatasetWorkflowId" in report
    ids = [line for line in report.splitlines() if "file_id →" in line]
    assert len(ids) == 2 and any("helper@" in line for line in ids)
    assert "use HarmonizeStatus" in report
    assert "2 loops" in report and "check" in report
    assert all(text not in report for text in (
        "unchanged", "only moved", "uncommitted", "Documentation only", "separate", "column_key →",
    ))
