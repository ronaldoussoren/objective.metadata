import pathlib
import unittest

from objective.metadata import datamodel, parsing, xcode

FRAGMENTS = pathlib.Path(__file__).parent / "fragments.framework"


class TestParsingStaticConstants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parsing.FrameworkParser(
            "fragments",
            start_header="staticconst.h",
            sdk=xcode.path_for_default_sdk(),
            cflags=["-isystem", str(FRAGMENTS)],
        )
        cls.parsed.parse()

    def assert_literal_value(
        self,
        *,
        name,
        value,
        unicode=False,
        introduced=None,
        deprecated=None,
        deprecated_message=None,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.literals)
        info = self.parsed.meta.literals[name]

        self.assertEqual(info.value, value)
        self.assertEqual(info.unicode, unicode)
        self.assertEqual(
            info.availability,
            datamodel.AvailabilityInfo(
                introduced=introduced,
                deprecated=deprecated,
                deprecated_message=deprecated_message,
            ),
        )
        self.assertIs(info.ignore, ignore)

    def assert_alias(
        self,
        *,
        name,
        alias,
        introduced=None,
        deprecated=None,
        deprecated_message=None,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.aliases)
        info = self.parsed.meta.aliases[name]

        self.assertEqual(info.alias, alias)

        self.assertEqual(
            info.availability,
            datamodel.AvailabilityInfo(
                introduced=introduced,
                deprecated=deprecated,
                deprecated_message=deprecated_message,
            ),
        )

    def test_basic_values(self):
        self.assert_literal_value(name="StaticInt", value=5)
        self.assert_literal_value(name="StaticNSInt", value=42)
        self.assert_literal_value(name="StaticFloat", value=2.5)

    def test_alias(self):
        self.assert_alias(name="MyAlias", alias="NSWindowSharingNone")

    def test_available(self):
        self.assert_literal_value(name="available", value=99, introduced=101300)

    def test_deprecated(self):
        self.assert_literal_value(
            name="deprecated",
            value=55,
            introduced=1005,
            deprecated=100900,
            deprecated_message="message",
        )

    def test_deprecated_alias(self):
        self.assert_alias(
            name="deprecated_alias",
            alias="NSWindowSharingReadWrite",
            introduced=1005,
            deprecated=100900,
            deprecated_message="message",
        )
