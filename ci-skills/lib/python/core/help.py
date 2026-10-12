"""Render ordered command help from the shared command catalogue.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import textwrap
from collections.abc import Mapping, Sequence

from lib.python.core.json_types import JSONObject


class CommandHelpFormatter(argparse.HelpFormatter):
    """Wrap catalogue prose without splitting executable names or options."""

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        """Wrap text at spaces while preserving complete command tokens.

        :param text: Catalogue section or example text supplied by argparse.
        :param width: Available terminal column width.
        :param indent: Indentation applied to each wrapped line.
        :returns: Wrapped text with command names and option tokens intact.
        """
        return textwrap.fill(
            self._whitespace_matcher.sub(" ", text).strip(),
            width,
            initial_indent=indent,
            subsequent_indent=indent,
            break_long_words=False,
            break_on_hyphens=False,
        )


class CommandHelp:
    """Read catalogue fields and render the same sections for every command."""

    def __init__(self, document: JSONObject):
        """Retain the command's catalogue entry without changing it.

        :param document: Command metadata returned by the shared describe function.
        """
        self.document = document

    def text(self, name: str, default: str = "") -> str:
        """Read one optional prose field.

        :param name: Catalogue field to read.
        :param default: Text used only when that field is absent.
        :returns: Catalogue text or the supplied default.
        :raises TypeError: If the field is present but is not text.
        """
        value = self.document.get(name, default)
        if not isinstance(value, str):
            raise TypeError(f"catalogue field {name} must be text")
        return value

    def labels(self, name: str) -> Mapping[str, str]:
        """Read a map of option, output-mode, or exit-code descriptions.

        :param name: Catalogue field whose keys and values must be text.
        :returns: The selected descriptions, empty when the field is absent.
        :raises TypeError: If the field is not a mapping of text keys to text values.
        """
        value = self.document.get(name, {})
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise TypeError(f"catalogue field {name} must map text to text")
        return {key: item for key, item in value.items()}

    def examples(self, program: str) -> Sequence[str]:
        """Read the command's examples with a discovery example as the default.

        :param program: Executable name used when no examples are declared.
        :returns: Example descriptions and commands in catalogue order.
        :raises TypeError: If examples are not a sequence of text-valued mappings.
        :raises KeyError: If an example lacks description or command.
        """  # noqa: DOC503 - Required example fields use mapping indexing.
        rows = self.document.get("examples")
        if rows is None:
            return [f"Inspect the command: {program} --describe"]
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise TypeError("catalogue examples must be a sequence")
        result = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("catalogue example must be a mapping")
            description, command = row["description"], row["command"]
            if not isinstance(description, str) or not isinstance(command, str):
                raise TypeError("catalogue example fields must be text")
            result.append(f"{description}: {command}")
        return result

    def render(self, parser: argparse.ArgumentParser) -> str:
        """Render catalogue sections and the parser's actual accepted options.

        :param parser: Parser whose option descriptions are filled from the catalogue.
        :returns: Human-readable help with consistent ordered sections.
        :raises TypeError: If a catalogue text, description map, or example is malformed.
        :raises KeyError: If a catalogue example lacks its description or command.
        """  # noqa: DOC502 - Catalogue example/label readers validate input shapes.
        formatter = parser._get_formatter()
        for heading, value in (
            ("Summary", self.text("summary", self.text("purpose"))),
            ("Description", self.text("description", self.text("use_when"))),
        ):
            formatter.start_section(heading)
            formatter.add_text(value)
            formatter.end_section()
        formatter.start_section("Examples")
        for example in self.examples(parser.prog):
            formatter.add_text(example)
        formatter.end_section()
        formatter.start_section("Options")
        options = self.labels("options")
        for action in parser._actions:
            for option in action.option_strings:
                if option in options:
                    action.help = options[option]
                    break
        formatter.add_arguments(parser._actions)
        formatter.end_section()
        formatter.start_section("Output modes")
        modes = self.labels("output_modes") or {
            option.removeprefix("--"): options[option]
            for option in ("--json", "--yaml", "--human")
            if option in options
        }
        for mode, meaning in modes.items():
            formatter.add_text(f"{mode}: {meaning}")
        formatter.end_section()
        formatter.start_section("Usage")
        formatter.add_text(self.text("usage", parser.format_usage().strip()))
        for code, meaning in self.labels("exit_codes").items():
            formatter.add_text(f"Exit {code}: {meaning}.")
        formatter.end_section()
        return formatter.format_help()
