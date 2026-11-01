from boardrunner.__main__ import main


def test_cli_runs_without_arguments():
    assert main([]) == 0
