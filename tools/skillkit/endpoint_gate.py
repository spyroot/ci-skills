"""Check the tracked documents of the target format against `TARGET_CONTRACT`.

The rules and the documents checked are specified in
docs/phases/CI03-GATES.md, section gate-ci-skills-endpoints. The contract itself
is `ci-skills/lib/python/core/endpoints.py`.
"""

from __future__ import annotations

import re
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path

from core import status
from core.endpoints import TARGET_CONTRACT, Role, TargetContract
from core.runtime import run_command

SCHEMA_VERSION = "1.0"
REPORT_KIND = "ci_skills_endpoints"
CONTRACT_SOURCE = "ci-skills/lib/python/core/endpoints.py"


class Rule(StrEnum):
    UNDECLARED = "undeclared"
    UNPAIRED = "unpaired"
    UNDOCUMENTED = "undocumented"
    VALUE_INTRODUCED = "value_introduced"
    BASE_UNREADABLE = "base_unreadable"
    PARSE_ERROR = "parse_error"


class Verdict(StrEnum):
    PASS = status.PASS
    FAIL = "FAIL"


class ExitCode(IntEnum):
    PASS = 0
    FAIL = 1
    # The gate could not run: bad arguments, an unreadable document, a crash.
    UNUSABLE = 2


@dataclass(frozen=True)
class KeyUse:
    """One table or key named by a document, at a line (0 when unknown)."""

    path: str
    source: str
    line: int


@dataclass(frozen=True)
class Finding:
    rule: Rule
    path: str
    source: str
    line: int
    detail: str

    def record(self) -> dict[str, object]:
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

    def __init__(self, source: str, text: str, first_line: int = 1) -> None:
        self.source = source
        self.text = text
        self.first_line = first_line

    @staticmethod
    def _join(path: str) -> str:
        """`kubernetes . "node_diagnostics"` and `kubernetes.node_diagnostics` alike."""
        return ".".join(part.strip().strip("\"'") for part in path.split("."))

    def parsed(self) -> tuple[dict[str, object] | None, str]:
        """The active TOML, or None and the parser's message."""
        try:
            return tomllib.loads(self.text), ""
        except tomllib.TOMLDecodeError as exc:
            return None, str(exc)

    def is_binding(self) -> bool:
        """A project binding names its target file at top level (another format).

        `core/project_binding.py` requires the top-level keys `schema_version`,
        `target` and `kubernetes`; a target file has tables only.
        """
        parsed, _ = self.parsed()
        return isinstance(parsed, dict) and isinstance(parsed.get("target"), str)

    def uses(self) -> tuple[KeyUse, ...]:
        """Every table and key path, commented ones included."""
        found: dict[str, KeyUse] = {}
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
        parsed, _ = self.parsed()
        for path in self._parsed_paths(parsed or {}):
            found.setdefault(path, KeyUse(path, self.source, 0))
        return tuple(found.values())

    @classmethod
    def _parsed_paths(cls, value: object, prefix: str = "") -> Iterator[str]:
        if isinstance(value, list):
            for item in value:
                yield from cls._parsed_paths(item, prefix)
            return
        if not isinstance(value, dict):
            return
        for name, item in value.items():
            path = f"{prefix}{name}"
            yield path
            yield from cls._parsed_paths(item, f"{path}.")


class MarkdownExamples:
    """The fenced TOML blocks of one Markdown file that show a target file."""

    _OPEN = re.compile(
        r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})\s*toml\b.*$", re.IGNORECASE
    )

    def __init__(self, source: str, markdown: str) -> None:
        self.source = source
        self.markdown = markdown

    def documents(self) -> tuple[TargetDocument, ...]:
        found: list[TargetDocument] = []
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
            document = TargetDocument(self.source, body, start + 1)
            if body.strip() and not document.is_binding():
                found.append(document)
            index = end + 1
        return tuple(found)

    @staticmethod
    def _closes(line: str, fence: str) -> bool:
        stripped = line.strip()
        return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


class DocumentSource(ABC):
    """Where the gate reads documents: the working tree or the staged index."""

    # Upstream text is never edited here (the standards' documentation contract
    # excludes vendor, third-party and external trees).
    _VENDORED = re.compile(r"(?:^|/)(?:vendor|third[-_]?party|external)(?:/|$)")

    def __init__(self, root: Path) -> None:
        self.root = root

    @abstractmethod
    def read(self, path: str) -> str: ...

    @abstractmethod
    def _matching(self, pattern: str) -> list[str]: ...

    def paths(self, patterns: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        for pattern in patterns:
            for path in sorted(self._matching(pattern)):
                if path not in found and not self._VENDORED.search(path):
                    found.append(path)
        return found

    def revision(self, base: str) -> str | None:
        """The commit `base` names, or None when it names none."""
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

    def read_at(self, commit: str, path: str) -> str | None:
        shown = run_command(["git", "-C", str(self.root), "show", f"{commit}:{path}"])
        return shown.stdout if shown.returncode == 0 else None


class WorkingTree(DocumentSource):
    def read(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def _matching(self, pattern: str) -> list[str]:
        return [
            item.relative_to(self.root).as_posix()
            for item in self.root.glob(pattern)
            if item.is_file()
        ]


class StagedIndex(DocumentSource):
    """What a commit would record: the bytes `git add` staged, for a hook."""

    def read(self, path: str) -> str:
        shown = run_command(["git", "-C", str(self.root), "show", f":{path}"])
        if shown.returncode != 0:
            raise OSError(f"{path} is not in the index")
        return shown.stdout

    def _matching(self, pattern: str) -> list[str]:
        listed = run_command(
            ["git", "-C", str(self.root), "ls-files", "--", f":(glob){pattern}"]
        )
        if listed.returncode != 0:
            raise OSError("cannot list the index")
        return [line for line in listed.stdout.splitlines() if line]


@dataclass
class GateResult:
    base: str | None
    documents: list[str] = field(default_factory=list)
    value_keys: tuple[str, ...] = ()
    findings: list[Finding] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        return Verdict.FAIL if self.findings else Verdict.PASS

    @property
    def exit_code(self) -> ExitCode:
        return ExitCode.FAIL if self.findings else ExitCode.PASS

    def record(self) -> dict[str, object]:
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
    """Refuse target keys that the contract does not declare as pointers."""

    TEMPLATE = "target.toml.template"
    # Shipped documents whose fenced TOML examples show a target file. Phase
    # plans under docs/ propose future keys and are not examples of this format.
    EXAMPLE_PATTERNS = ("README.md", "ci-skills/**/*.md")

    def __init__(
        self,
        source: DocumentSource,
        base: str | None,
        contract: TargetContract = TARGET_CONTRACT,
    ) -> None:
        self.source = source
        self.base = base
        self.contract = contract

    def run(self) -> GateResult:
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
        parsed, message = document.parsed()
        if parsed is None:
            yield Finding(
                Rule.PARSE_ERROR,
                document.source,
                document.source,
                document.first_line,
                message[:500],
            )

    def _undeclared(self, document: TargetDocument) -> Iterator[Finding]:
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
