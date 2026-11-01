from boardrunner import main


def test_cli_runs_without_arguments():
    assert main([]) == 0
