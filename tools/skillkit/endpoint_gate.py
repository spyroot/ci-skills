"""Check the tracked documents of the target format against `TARGET_CONTRACT`.

The rules and the documents checked are specified in
docs/phases/CI03-GATES.md, section gate-ci-skills-endpoints. The contract itself
is `ci-skills/lib/python/core/endpoints.py`.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import re
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Optional, TypeAlias, Union

from lib.python.core.endpoints import TARGET_CONTRACT, Role, TargetContract
from lib.python.core.json_types import JSONObject
from lib.python.core.runtime import run_command
from lib.python.sys import status

TOMLValue: TypeAlias = Union[
    str,
    int,
    float,
    bool,
    date,
    datetime,
    time,
    Mapping[str, "TOMLValue"],
    Sequence["TOMLValue"],
]
TOMLDocument: TypeAlias = Mapping[str, TOMLValue]


@dataclass(frozen=True)
class ParsedTarget:
    """Keep parsed target fields and their syntax diagnostic in distinct fields.

    :param document: Parsed TOML document; empty when syntax prevents parsing.
    :param error: Parser diagnostic, or empty text for a valid document.

    .. attribute :: document
        :type: TOMLDocument

        Parsed TOML document; empty when syntax prevents parsing.

    .. attribute :: error
        :type: str

        Parser diagnostic, or empty text for a valid document.
    """

    document: TOMLDocument
    error: str = ""


SCHEMA_VERSION = "1.0"
REPORT_KIND = "ci_skills_endpoints"
CONTRACT_SOURCE = "ci-skills/lib/python/core/endpoints.py"
TARGET_TEMPLATE = "target.toml.template"


class Rule(StrEnum):
    """Stable finding categories emitted by target-document validation.

    .. attribute :: UNDECLARED
        :type: Rule

        A target table or key is absent from the contract.

    .. attribute :: UNPAIRED
        :type: Rule

        A service declaration lacks its endpoint/access pair.

    .. attribute :: UNDOCUMENTED
        :type: Rule

        A declared target path is absent from the template.

    .. attribute :: VALUE_INTRODUCED
        :type: Rule

        A value key was introduced since the selected Git base.

    .. attribute :: BASE_UNREADABLE
        :type: Rule

        The comparison template cannot be read at the selected base.

    .. attribute :: PARSE_ERROR
        :type: Rule

        A target document contains invalid active TOML.
    """

    UNDECLARED = "undeclared"
    UNPAIRED = "unpaired"
    UNDOCUMENTED = "undocumented"
    VALUE_INTRODUCED = "value_introduced"
    BASE_UNREADABLE = "base_unreadable"
    PARSE_ERROR = "parse_error"


class Verdict(StrEnum):
    """PASS or FAIL status derived from the presence of findings.

    .. attribute :: PASS
        :type: Verdict

        No gate findings were recorded.

    .. attribute :: FAIL
        :type: Verdict

        At least one gate finding was recorded.
    """

    PASS = status.PASS
    FAIL = "FAIL"


class ExitCode(IntEnum):
    """Process status for pass, gate failure, or an unusable gate run.

    .. attribute :: PASS
        :type: ExitCode

        The gate completed without findings.

    .. attribute :: FAIL
        :type: ExitCode

        The gate completed with findings.

    .. attribute :: UNUSABLE
        :type: ExitCode

        Arguments, required documents, or execution prevented a usable result.
    """

    PASS = 0
    FAIL = 1
    # The gate could not run: bad arguments, an unreadable document, a crash.
    UNUSABLE = 2


@dataclass(frozen=True)
class KeyUse:
    """First observed use of one target table or key in a document.

    :param path: Dotted target table or key path.
    :param source: Template or Markdown file containing the use.
    :param line: Source line number, or zero when parsed TOML has no line match.

    .. attribute :: path
        :type: str

        Dotted target table or key path.

    .. attribute :: source
        :type: str

        Template or Markdown file containing the use.

    .. attribute :: line
        :type: int

        Source line number, or zero when parsed TOML has no line match.
    """

    path: str
    source: str
    line: int


@dataclass(frozen=True)
class Finding:
    """One actionable mismatch between target documents and the contract.

    :param rule: Stable gate finding category.
    :param path: Dotted target path or document affected by the finding.
    :param source: Source file or base revision associated with the finding.
    :param line: Source line number, or zero when unavailable.
    :param detail: Human-readable reason for the mismatch.

    .. attribute :: rule
        :type: Rule

        Stable gate finding category.

    .. attribute :: path
        :type: str

        Dotted target path or document affected by the finding.

    .. attribute :: source
        :type: str

        Source file or base revision associated with the finding.

    .. attribute :: line
        :type: int

        Source line number, or zero when unavailable.

    .. attribute :: detail
        :type: str

        Human-readable reason for the mismatch.
    """

    rule: Rule
    path: str
    source: str
    line: int
    detail: str

    def record(self) -> JSONObject:
        """Serialize one gate finding for the JSON result schema.

        :returns: Rule, path, source, line, and detail fields.
        """
        return {
            "rule": str(self.rule),
            "path": self.path,
            "source": self.source,
            "line": self.line,
            "detail": self.detail,
        }


class TargetDocument:
    """The tables and keys a TOML target document names.

    Commented-out keys count: in a template or a documented example they show a
    reader what to write, so a random key there is introduced as surely as an
    active one.
    """

    _COMMENT = r"^\s*(?P<hash>#*)\s*"
    _HEADER = re.compile(
        _COMMENT + r"\[(?P<array>\[)?(?P<path>[^\[\]]+)\]\]?\s*(#.*)?$"
    )
    _KEY = re.compile(
        _COMMENT
        + r"""(?:"(?P<dq>[^"]+)"|'(?P<sq>[^']+)'|(?P<bare>[A-Za-z0-9_.-]+))\s*=(?!=)"""
    )

    def __init__(self, source: str, text: str, first_line: int = 1):
        """Retain target text and its location within a source document.

        :param source: Path reported for uses and parse findings.
        :param text: TOML text to parse and scan for declared paths.
        :param first_line: Line number of the first TOML text line in its source.
        """
        self.source = source
        self.text = text
        self.first_line = first_line

    @staticmethod
    def _join(path: str) -> str:
        """Normalize dotted table or key spelling to a contract path.

        :param path: TOML path with optional spaces and quoted segments.
        :returns: Dot-joined path with segment whitespace and quotes removed.
        """
        return ".".join(part.strip().strip("\"'") for part in path.split("."))

    def parsed(self) -> ParsedTarget:
        """Parse active TOML while retaining a syntax error for gate findings.

        :returns: Named document/error fields, with an empty document on syntax failure.
        :raises RecursionError: If TOML nesting exceeds the parser recursion limit.
        """  # noqa: DOC502 - tomllib parsing can exceed recursion limits.
        try:
            return ParsedTarget(tomllib.loads(self.text))
        except tomllib.TOMLDecodeError as exc:
            return ParsedTarget({}, str(exc))

    def is_binding(self) -> bool:
        """Distinguish project binding TOML from target-file TOML.

        :returns: True when parsed TOML has a top-level string target key.
        :raises RecursionError: If TOML nesting exceeds the parser recursion limit.
        """  # noqa: DOC502 - parsed propagates TOML recursion failures.
        return isinstance(self.parsed().document.get("target"), str)

    def uses(self) -> tuple[KeyUse, ...]:
        """Find paths in active TOML and commented target examples.

        :returns: First use of each table or key path with source and line.
        :raises RecursionError: If TOML nesting exceeds parsing or path-walk limits.
        """  # noqa: DOC502 - TOML parsing and recursive path walking can fail.
        found: MutableMapping[str, KeyUse] = {}
        # An active key belongs to the last active header; a commented key to
        # the last header of either kind, as in a commented-out example table.
        active = shown = ""
        for offset, line in enumerate(self.text.splitlines()):
            number = self.first_line + offset
            header = self._HEADER.match(line)
            if header:
                shown = self._join(header.group("path"))
                if not header.group("hash"):
                    active = shown
                found.setdefault(shown, KeyUse(shown, self.source, number))
                continue
            key = self._KEY.match(line)
            if key:
                name = (
                    key.group("dq") or key.group("sq") or self._join(key.group("bare"))
                )
                table = active if not key.group("hash") else shown
                path = f"{table}.{name}" if table else name
                found.setdefault(path, KeyUse(path, self.source, number))
        # Inline tables, dotted keys and arrays of tables in active TOML are
        # invisible or ambiguous to a line scan; the parsed document names them.
        for path in self._parsed_paths(self.parsed().document):
            found.setdefault(path, KeyUse(path, self.source, 0))
        return tuple(found.values())

    @classmethod
    def _parsed_paths(cls, value: TOMLValue, prefix: str = "") -> Iterator[str]:
        """Walk parsed TOML objects, including arrays and inline tables.

        :param value: Parsed TOML mapping, array, or scalar at this level.
        :param prefix: Dotted parent path prepended to each nested key.
        :yields: Each dotted path named by parsed table or field keys.
        :ytype: str
        :raises RecursionError: If supplied containers exceed recursive path-walk limits.
        """  # noqa: DOC502 - Recursive calls can exceed container nesting limits.
        if isinstance(value, Sequence) and not isinstance(value, str):
            for item in value:
                yield from cls._parsed_paths(item, prefix)
            return
        if not isinstance(value, Mapping):
            return
        for name, item in value.items():
            path = f"{prefix}{name}"
            yield path
            yield from cls._parsed_paths(item, f"{path}.")


class MarkdownExamples:
    """Read target TOML fences; native-config=path/file.toml identifies other formats."""

    _OPEN = re.compile(
        r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})\s*toml\b(?P<info>.*)$",
        re.IGNORECASE,
    )
    _NATIVE_CONFIG = re.compile(r"(?:^|\s)native-config=(\S*)")

    def __init__(self, source: str, markdown: str):
        """Retain one Markdown file for fenced target-example discovery.

        :param source: Markdown path reported for extracted examples.
        :param markdown: Full Markdown text containing possible TOML fences.
        """
        self.source = source
        self.markdown = markdown

    def documents(self) -> tuple[TargetDocument, ...]:
        """Extract nonempty TOML fences that describe target files.

        :returns: Target documents with original source and first-line locations.
        :raises RecursionError: If a fenced TOML document exceeds parser nesting limits.
        """  # noqa: DOC502 - Binding detection propagates TOML recursion failures.
        found: MutableSequence[TargetDocument] = []
        lines = self.markdown.splitlines()
        index = 0
        while index < len(lines):
            opening = self._OPEN.match(lines[index])
            if not opening:
                index += 1
                continue
            fence, indent = opening.group("fence"), len(opening.group("indent"))
            start = end = index + 1
            while end < len(lines) and not self._closes(lines[end], fence):
                end += 1
            body = "\n".join(line[indent:] for line in lines[start:end])
            if body.strip() and not self._is_native_config(opening.group("info")):
                document = TargetDocument(self.source, body, start + 1)
                if not document.is_binding():
                    found.append(document)
            index = end + 1
        return tuple(found)

    @classmethod
    def _is_native_config(cls, info: str) -> bool:
        """Recognize one explicit native TOML filename while retaining target checks.

        :param info: Fence attributes after the TOML language name.
        :returns: True for one native-config path ending in .toml whose basename
            is not target.toml; missing, repeated or malformed attributes stay checked.
        """
        paths = cls._NATIVE_CONFIG.findall(info)
        if len(paths) != 1:
            return False
        filename = Path(paths[0].replace("\\", "/")).name
        return filename.endswith(".toml") and filename != Path(TARGET_TEMPLATE).stem

    @staticmethod
    def _closes(line: str, fence: str) -> bool:
        """Recognize a matching backtick or tilde closing fence.

        :param line: Candidate Markdown fence line.
        :param fence: Opening fence whose character and minimum length must match.
        :returns: True when the line closes the selected fence.
        """
        stripped = line.strip()
        return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


class DocumentSource(ABC):
    """Where the gate reads documents: the working tree or the staged index."""

    # Upstream text is never edited here (the standards' documentation contract
    # excludes vendor, third-party and external trees).
    _VENDORED = re.compile(r"(?:^|/)(?:vendor|third[-_]?party|external)(?:/|$)")

    def __init__(self, root: Path):
        """Bind document reads to a repository root.

        :param root: Repository containing the target template and examples.
        """
        self.root = root

    @abstractmethod
    def read(self, path: str) -> str:
        """Read one target document from the selected source implementation.

        :param path: Repository-relative path to the document.
        :returns: UTF-8 document text supplied by the source implementation.
        """
        ...

    @abstractmethod
    def _matching(self, pattern: str) -> Sequence[str]:
        """List source paths matching one gate document pattern.

        :param pattern: Repository-relative glob pattern to enumerate.
        :returns: Paths matched by the working tree or staged index.
        """
        ...

    def paths(self, patterns: Sequence[str]) -> Sequence[str]:
        """Collect unique matching document paths while excluding vendored trees.

        :param patterns: Repository-relative glob patterns in priority order.
        :returns: Ordered nonvendored document paths without duplicates.
        """
        found: MutableSequence[str] = []
        for pattern in patterns:
            for path in sorted(self._matching(pattern)):
                if path not in found and not self._VENDORED.search(path):
                    found.append(path)
        return found

    def revision(self, base: str) -> Optional[str]:
        """Resolve a supplied Git base to a commit without accepting an option.

        :param base: Git revision expression used for the template comparison.
        :returns: Commit SHA, or None when Git cannot resolve the base.
        """
        shown = run_command(
            [
                "git",
                "-C",
                str(self.root),
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{base}^{{commit}}",
            ]
        )
        return shown.stdout.strip() if shown.returncode == 0 else None

    def read_at(self, commit: str, path: str) -> Optional[str]:
        """Read one file's bytes as text from a resolved Git commit.

        :param commit: Resolved commit SHA containing the requested path.
        :param path: Repository-relative file path in that commit.
        :returns: File text, or None when Git cannot read that file.
        """
        shown = run_command(["git", "-C", str(self.root), "show", f"{commit}:{path}"])
        return shown.stdout if shown.returncode == 0 else None


class WorkingTree(DocumentSource):
    """Read current repository files for endpoint-contract validation."""

    def read(self, path: str) -> str:
        """Read a repository-relative working-tree document as UTF-8.

        :param path: Document path below the selected repository root.
        :returns: Current working-tree file text.
        :raises OSError: If the file cannot be read.
        :raises UnicodeError: If the document bytes are not valid UTF-8.
        """  # noqa: DOC502 - Path.read_text propagates I/O and decoding failures.
        return (self.root / path).read_text(encoding="utf-8")

    def _matching(self, pattern: str) -> Sequence[str]:
        """Enumerate working-tree files matching a document glob.

        :param pattern: Glob pattern relative to the repository root.
        :returns: Repository-relative regular-file paths.
        """
        return [
            item.relative_to(self.root).as_posix()
            for item in self.root.glob(pattern)
            if item.is_file()
        ]


class StagedIndex(DocumentSource):
    """What a commit would record: the bytes `git add` staged, for a hook."""

    def read(self, path: str) -> str:
        """Read staged document text from the Git index.

        :param path: Repository-relative path expected in the index.
        :returns: Staged file content as text.
        :raises OSError: If the path cannot be read from the index.
        """
        shown = run_command(["git", "-C", str(self.root), "show", f":{path}"])
        if shown.returncode != 0:
            raise OSError(f"{path} is not in the index")
        return shown.stdout

    def _matching(self, pattern: str) -> Sequence[str]:
        """List staged paths matching a Git glob pattern.

        :param pattern: Repository-relative path glob passed to Git.
        :returns: Matching staged paths in index order.
        :raises OSError: If Git cannot enumerate index paths.
        """
        listed = run_command(
            ["git", "-C", str(self.root), "ls-files", "--", f":(glob){pattern}"]
        )
        if listed.returncode != 0:
            raise OSError("cannot list the index")
        return [line for line in listed.stdout.splitlines() if line]


@dataclass
class GateResult:
    """Collect checked documents, accepted value keys, and gate findings.

    :param base: Git revision used for new-value checks, if selected.
    :param documents: Checked source and first-line references.
    :param value_keys: Existing value paths accepted by the target contract.
    :param findings: Accumulated parse, declaration, and base-comparison failures.

    .. attribute :: base
        :type: Optional[str]

        Git revision used for new-value checks, if selected.

    .. attribute :: documents
        :type: MutableSequence[str]

        Checked source and first-line references.

    .. attribute :: value_keys
        :type: tuple[str, ...]

        Existing value paths accepted by the target contract.

    .. attribute :: findings
        :type: MutableSequence[Finding]

        Accumulated parse, declaration, and base-comparison failures.
    """

    base: Optional[str]
    documents: MutableSequence[str] = field(default_factory=list)
    value_keys: tuple[str, ...] = ()
    findings: MutableSequence[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        """Classify a gate result by whether it contains findings.

        :returns: FAIL for any finding, otherwise PASS.
        """
        return Verdict.FAIL if self.findings else Verdict.PASS

    @property
    def exit_code(self) -> ExitCode:
        """Translate finding presence to the gate's process exit code.

        :returns: FAIL exit code for findings, otherwise PASS exit code.
        """
        return ExitCode.FAIL if self.findings else ExitCode.PASS

    def record(self) -> JSONObject:
        """Serialize the gate result under its machine-readable schema.

        :returns: Status, base, documents, value keys, and serialized findings.
        """
        return {
            "kind": REPORT_KIND,
            "schema_version": SCHEMA_VERSION,
            "status": str(self.verdict),
            "base": self.base,
            "documents": self.documents,
            "value_keys": list(self.value_keys),
            "findings": [finding.record() for finding in self.findings],
        }

    def summary(self) -> str:
        """Render a human-readable gate verdict and each finding.

        :returns: Multiline summary with document count, value keys, and findings.
        """
        lines = [
            (
                f"{self.verdict}: {len(self.documents)} documents checked against "
                f"base {self.base or 'none'}"
            ),
            "accepted values that are not pointers (read them at run time): "
            + (", ".join(self.value_keys) or "none"),
        ]
        lines.extend(
            f"{item.rule}: {item.path} ({item.source}:{item.line}) {item.detail}"
            for item in self.findings
        )
        return "\n".join(lines)


class EndpointGate:
    """Refuse target keys that the contract does not declare as pointers.

    .. attribute :: TEMPLATE

        Repository-relative target template that the gate reads from DocumentSource.

    .. attribute :: EXAMPLE_PATTERNS

        Repository document globs whose fenced TOML examples are checked.
    """

    TEMPLATE = TARGET_TEMPLATE
    # Shipped documents whose fenced TOML examples show a target file. Phase
    # plans under docs/ propose future keys and are not examples of this format.
    EXAMPLE_PATTERNS = ("README.md", "ci-skills/**/*.md")

    def __init__(
        self,
        source: DocumentSource,
        base: Optional[str],
        contract: TargetContract = TARGET_CONTRACT,
    ):
        """Select document source, optional comparison base, and target contract.

        :param source: Working-tree or staged-index document reader.
        :param base: Optional Git revision for newly introduced value-key checks.
        :param contract: Allowed target tables, fields, and roles.
        """
        self.source = source
        self.base = base
        self.contract = contract

    def run(self) -> GateResult:
        """Check the target template and fenced examples against the contract.

        :returns: Gate result containing checked documents and all findings.
        :raises OSError: If a required source document cannot be read.
        :raises UnicodeError: If a source document or Git output is not valid text.
        :raises RecursionError: If a TOML document exceeds parser or path-walk nesting.
        """  # noqa: DOC502 - Document reads and TOML validators propagate failures.
        result = GateResult(base=self.base, value_keys=self.contract.value_keys())
        template = TargetDocument(self.TEMPLATE, self.source.read(self.TEMPLATE))
        documents = [template]
        for path in self.source.paths(self.EXAMPLE_PATTERNS):
            documents.extend(MarkdownExamples(path, self.source.read(path)).documents())
        result.documents = [f"{item.source}:{item.first_line}" for item in documents]
        for document in documents:
            result.findings.extend(self._unparsable(document))
            result.findings.extend(self._undeclared(document))
        result.findings.extend(
            Finding(
                Rule.UNPAIRED, name, CONTRACT_SOURCE, 0, "needs endpoint and access"
            )
            for name in self.contract.unpaired()
        )
        shown = {use.path for use in template.uses()}
        result.findings.extend(
            Finding(Rule.UNDOCUMENTED, path, self.TEMPLATE, 0, "declared but not shown")
            for path in self.contract.paths()
            if path not in shown
        )
        result.findings.extend(self._introduced(template))
        return result

    def _unparsable(self, document: TargetDocument) -> Iterator[Finding]:
        """Report a target document whose active TOML cannot be parsed.

        :param document: Target template or extracted Markdown example.
        :yields: One parse-error finding when parsing fails.
        :ytype: Finding
        :raises RecursionError: If TOML nesting exceeds the parser recursion limit.
        """  # noqa: DOC502 - TargetDocument.parsed propagates recursion failures.
        parsed = document.parsed()
        if parsed.error:
            yield Finding(
                Rule.PARSE_ERROR,
                document.source,
                document.source,
                document.first_line,
                parsed.error[:500],
            )

    def _undeclared(self, document: TargetDocument) -> Iterator[Finding]:
        """Report every table or field path absent from the target contract.

        :param document: Target template or extracted Markdown example.
        :yields: One finding per undeclared path found in the document.
        :ytype: Finding
        :raises RecursionError: If TOML nesting exceeds parsing or path-walk limits.
        """  # noqa: DOC502 - TargetDocument.uses propagates recursion failures.
        for use in document.uses():
            if not self.contract.declares(use.path):
                yield Finding(
                    Rule.UNDECLARED,
                    use.path,
                    use.source,
                    use.line,
                    "not an endpoint or access key of the target contract",
                )

    def _introduced(self, template: TargetDocument) -> Iterator[Finding]:
        """Report value keys newly shown since the selected Git base.

        :param template: Current target template used for base comparison.
        :yields: An unreadable-base finding or each newly introduced value key.
        :ytype: Finding
        :raises UnicodeError: If Git output cannot be decoded as text.
        :raises RecursionError: If compared TOML exceeds parsing or path-walk limits.
        :raises OSError: If the native Git process cannot start.
        """  # noqa: DOC502 - Git reads and TOML path walking propagate failures.
        if self.base is None:
            return
        commit = self.source.revision(self.base)
        before_text = (
            self.source.read_at(commit, self.TEMPLATE) if commit is not None else None
        )
        if before_text is None:
            yield Finding(
                Rule.BASE_UNREADABLE,
                self.TEMPLATE,
                self.base,
                0,
                "cannot read the template at the base revision",
            )
            return
        before = {use.path for use in TargetDocument(self.TEMPLATE, before_text).uses()}
        for use in template.uses():
            if self.contract.role(use.path) is Role.VALUE and use.path not in before:
                yield Finding(
                    Rule.VALUE_INTRODUCED,
                    use.path,
                    use.source,
                    use.line,
                    "a value the command can read at run time; declare a pointer",
                )
