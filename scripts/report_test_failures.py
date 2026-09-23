"""Surface pytest failures as public GitHub Actions annotations when job logs are unavailable."""
from pathlib import Path
from xml.etree import ElementTree


def report(path: Path):
    if not path.exists():
        return
    root = ElementTree.parse(path).getroot()
    for case in root.iter("testcase"):
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is None:
            continue
        text = (failure.text or failure.get("message") or "Test failed")[:3000]
        text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        name = case.get("name", "test").replace("%", "%25").replace("\r", "").replace("\n", "")
        filename = case.get("file", "tests").replace("%", "%25").replace("\r", "").replace("\n", "")
        print(f"::error file={filename},title=pytest {name}::{text}")


if __name__ == "__main__":
    report(Path("pytest-results.xml"))
