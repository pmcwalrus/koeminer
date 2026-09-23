from scripts.report_test_failures import report


def test_failed_pytest_case_becomes_github_annotation(tmp_path, capsys):
    path = tmp_path / "pytest-results.xml"
    path.write_text('<testsuite><testcase file="tests/test_ui.py" name="test_small_window">'
                    '<failure message="AssertionError">First line\nSecond line</failure>'
                    '</testcase></testsuite>')
    report(path)
    assert capsys.readouterr().out == ('::error file=tests/test_ui.py,title=pytest test_small_window::'
                                       'First line%0ASecond line\n')


def test_missing_report_is_ignored(tmp_path, capsys):
    report(tmp_path / "missing.xml")
    assert capsys.readouterr().out == ""
