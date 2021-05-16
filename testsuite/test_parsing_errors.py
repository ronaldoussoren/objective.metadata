import pathlib
import unittest

from objective.metadata import parsing, xcode

FRAGMENTS = pathlib.Path(__file__).parent / "fragments.framework"


class TestParsingDefines(unittest.TestCase):
    def parse(self, fragment):
        self.parsed = parsing.FrameworkParser(
            "fragments",
            start_header=fragment,
            sdk=xcode.path_for_default_sdk(),
            cflags=["-isystem", str(FRAGMENTS)],
        )
        self.parsed.parse()

    def test_syntax_error(self):
        with self.assertRaises(parsing.ParsingError):
            self.parse("syntax_error.h")

        self.assertIsInstance(self.parsed, parsing.FrameworkParser)

    def test_missing_include(self):
        with self.assertRaises(parsing.ParsingError):
            self.parse("missing_include.h")

        self.assertIsInstance(self.parsed, parsing.FrameworkParser)
