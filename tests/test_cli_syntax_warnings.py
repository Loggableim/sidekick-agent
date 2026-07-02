import pathlib
import py_compile
import warnings


def test_cli_compiles_without_syntax_warnings():
    cli_path = pathlib.Path(__file__).resolve().parents[1] / "cli" / "cli.py"

    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        py_compile.compile(str(cli_path), doraise=True)
