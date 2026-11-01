"""Find fixed deployment selections in executable smoke and test inputs.

The scanner follows local constants into process invocations, provider calls,
and target documents consumed by those invocations. It does not execute code or
resolve arbitrary helper calls. Synthetic origins apply only to their own
invocation or target authority; receipt and assertion data are not operations.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import ast
import ipaddress
import re
import shlex
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path
from typing import Final, Optional
from urllib.parse import urlsplit

from lib.python.core.endpoints import TARGET_CONTRACT, Role

RUNTIME: Final = "__portability_runtime__"
MAX_SOURCE_BYTES: Final = 2 * 1024 * 1024
MAX_NODES: Final = 100_000
MAX_ALTERNATIVES: Final = 32
SOURCE_SUFFIXES: Final = frozenset({".py", ".sh", ".bash", ".bats", ".yml", ".yaml"})
DATA_SUFFIXES: Final = frozenset({".json", ".md", ".toml", ".txt"})
NATIVE_COMMANDS: Final = frozenset(
    {"gh", "glab", "kubectl", "oc", "helm", "docker", "podman", "curl", "wget", "git"}
)
COMMAND_WRAPPERS: Final = frozenset(
    {"run", "env", "sudo", "timeout", "conda", "python", "python3", "bash", "sh"}
)
SHELL_DECLARATIONS: Final = {
    "export": frozenset("n"),
    "readonly": frozenset(),
    "local": frozenset("rx"),
    "declare": frozenset("grx"),
    "typeset": frozenset("grx"),
}
SHELL_BINDING: Final = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?", re.DOTALL)
PROCESS_CALLS: Final = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "run_command",
        "run_script",
        "system",
    }
)
FLAG_SELECTORS: Final = {
    "--host": "host",
    "--hostname": "host",
    "--url": "host",
    "--server": "host",
    "--gitlab-url": "host",
    "--repo": "repository",
    "--repository": "repository",
    "-R": "repository",
    "--project": "project",
    "--project-id": "project",
    "--project-name": "project",
    "--group": "project",
    "--context": "cluster",
    "--cluster": "cluster",
    "--namespace": "cluster",
    "-n": "cluster",
    "--kubeconfig": "credential",
    "--token-file": "credential",
    "--credential-file": "credential",
    "--username-file": "credential",
    "--password-file": "credential",
    "--registry": "registry",
    "--registry-url": "registry",
    "--destination": "registry",
}
ENV_FIELDS: Final = {
    "GH_HOST": "github.host",
    "GITHUB_API_URL": "github.host",
    "GITHUB_REPOSITORY": "github.repository",
    "GITLAB_URL": "gitlab.url",
    "GITLAB_HOST": "gitlab.url",
    "GITLAB_PROJECT": "gitlab.project",
    "KUBECONFIG": "kubernetes.kubeconfig",
    "KUBE_CONTEXT": "kubernetes.context",
    "HARBOR_URL": "harbor.url",
    "HARBOR_PROJECT": "harbor.project",
}
RESERVED_NETWORKS: Final = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)
SHELL_EXPANSION: Final = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?([-+?=])(.*?))?\}"
    r"|\$([A-Za-z_][A-Za-z0-9_]*|[0-9@*?#])|\$\([^)]*\)"
)
TARGET_HEADER: Final = re.compile(
    r"^\s*\[(?:"
    + "|".join(re.escape(authority) for authority in TARGET_CONTRACT.authorities)
    + r")(?:[.\]])",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Finding:
    """Identify a portability failure without retaining its selected value.

    :param source: Repository-relative source path.
    :param line: One-based source line, or zero for unreadable files.
    :param rule: Stable failure category.
    :param selector: Host, repository, project, cluster, credential, or registry.

    .. attribute :: source
        :type: str

        Repository-relative source path.

    .. attribute :: line
        :type: int

        Source line, or zero for unreadable files.

    .. attribute :: rule
        :type: str

        Stable failure category.

    .. attribute :: selector
        :type: str

        Operational selector category.
    """

    source: str
    line: int
    rule: str
    selector: str

    def record(self) -> dict[str, str | int]:
        """Render the finding without private literal values.

        :returns: Source, line, rule, and selector fields.
        """
        return {
            "source": self.source,
            "line": self.line,
            "rule": self.rule,
            "selector": self.selector,
        }


@dataclass(frozen=True)
class GateResult:
    """Retain the selected sources and their redacted findings.

    :param checked_files: Sources actually inspected in the selected snapshot.
    :param findings: Classified source and portability failures.

    .. attribute :: checked_files
        :type: tuple[str, ...]

        Selected source paths inspected by the gate.

    .. attribute :: findings
        :type: tuple[Finding, ...]

        Redacted source and portability failures.
    """

    checked_files: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        """Return the process result for this inspection.

        :returns: Zero for no findings, otherwise one.
        """
        return int(bool(self.findings))

    def record(self) -> dict[str, object]:
        """Render the gate result without source literals.

        :returns: Versioned status, checked paths, and redacted findings.
        """
        return {
            "schema_version": "1.0",
            "kind": "ci_skills_portability",
            "status": "FAIL" if self.findings else "PASS",
            "checked_files": list(self.checked_files),
            "findings": [item.record() for item in self.findings],
        }

    def summary(self) -> str:
        """Describe selected files and actionable source locations.

        :returns: Human-readable status and one line per finding.
        """
        lines = [
            f"{'FAIL' if self.findings else 'PASS'}: {len(self.checked_files)} portability sources"
        ]
        lines.extend(
            f"{item.source}:{item.line}: {item.rule} ({item.selector})"
            for item in self.findings
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class _Value:
    """Keep bounded string alternatives and structured literal children.

    :param texts: Literal templates; RUNTIME marks a resolved dynamic expression.
    :param items: Positional arguments or array elements.
    :param fields: Mapping entries supplied by source syntax.
    :param targets: Locally written target documents attached to their file value.
    :param choices: Correlated structured alternatives retained across branches.

    .. attribute :: texts
        :type: tuple[str, ...]

        Literal templates retaining runtime markers.

    .. attribute :: items
        :type: tuple[_Value, ...]

        Positional arguments or array elements.

    .. attribute :: fields
        :type: tuple[tuple[str, _Value], ...]

        Named mapping or constructor values.

    .. attribute :: targets
        :type: tuple[_Selection, ...]

        Target selectors attached to a locally written file.

    .. attribute :: choices
        :type: tuple[_Value, ...]

        Whole structured alternatives with branch correlation preserved.
    """

    texts: tuple[str, ...] = (RUNTIME,)
    items: tuple[_Value, ...] = ()
    fields: tuple[tuple[str, _Value], ...] = ()
    targets: tuple[_Selection, ...] = ()
    choices: tuple[_Value, ...] = ()


@dataclass(frozen=True)
class _Selection:
    """Associate one operational value with its target responsibility.

    :param selector: Portability category.
    :param value: Locally resolved literal or runtime alternatives.
    :param authority: Invocation or target-table context for synthetic fixtures.

    .. attribute :: selector
        :type: str

        Operational selector category.

    .. attribute :: value
        :type: _Value

        Resolved literal or runtime alternatives.

    .. attribute :: authority
        :type: str

        Invocation or target-table context for synthetic fixtures.
    """

    selector: str
    value: _Value
    authority: str = "command"


def _alternatives(values: Sequence[str]) -> tuple[str, ...]:
    """Bound local constant folding without silently dropping branches.

    :param values: Candidate string templates.
    :returns: Unique templates in discovery order.
    :raises ValueError: The expression exceeds the finite alternative budget.
    """
    result = tuple(dict.fromkeys(values))
    if len(result) > MAX_ALTERNATIVES:
        raise ValueError("expression_budget_exceeded")
    return result


def _value_choices(value: _Value) -> tuple[_Value, ...]:
    """Expose a value's whole alternatives without separating correlated fields.

    :param value: Scalar, structured value, or previously merged alternatives.
    :returns: One or more complete possible values.
    """
    return value.choices or (value,)


def _merge_values(values: Sequence[_Value]) -> _Value:
    """Retain bounded branch alternatives without dropping executable structure.

    :param values: Complete values produced by alternative control-flow paths.
    :returns: Scalar alternatives or correlated structured alternatives.
    :raises ValueError: Branch alternatives exceed the finite scanner budget.
    """
    choices = tuple(
        dict.fromkeys(choice for value in values for choice in _value_choices(value))
    )
    if len(choices) > MAX_ALTERNATIVES:
        raise ValueError("expression_budget_exceeded")
    if len(choices) == 1:
        return choices[0]
    if any(value.items or value.fields or value.targets for value in choices):
        return _Value(choices=choices)
    return _Value(
        _alternatives(tuple(text for value in choices for text in value.texts))
    )


def _value_groups(values: Sequence[_Value]) -> tuple[tuple[_Value, ...], ...]:
    """Expand independent inputs within the same finite expression budget.

    :param values: Arguments or mapping values containing whole alternatives.
    :returns: Bounded groups with each structured branch kept intact.
    :raises ValueError: Combining alternatives exceeds the scanner budget.
    """
    groups = tuple(_value_choices(value) for value in values)
    count = 1
    for group in groups:
        count *= len(group)
        if count > MAX_ALTERNATIVES:
            raise ValueError("expression_budget_exceeded")
    return tuple(product(*groups))


def _field_value(value: _Value, key: str) -> _Value:
    """Read a declared field from every retained structured alternative.

    :param value: Object or mapping with possibly conditional fields.
    :param key: Selected attribute or string mapping key.
    :returns: Bounded values supplied by the alternative fields.
    :raises ValueError: Field alternatives exceed the scanner budget.
    """
    return _merge_values(
        tuple(
            dict(choice.fields).get(key, _Value()) for choice in _value_choices(value)
        )
    )


def _join(left: _Value, right: _Value, separator: str = "") -> _Value:
    """Combine adjacent literal and runtime string parts.

    :param left: Prefix alternatives.
    :param right: Suffix alternatives.
    :param separator: Path separator when joining path expressions.
    :returns: Bounded combined alternatives.
    :raises ValueError: Constant expansion exceeds its budget.
    """
    if left.choices or right.choices:
        return _merge_values(
            tuple(_join(a, b, separator) for a, b in _value_groups((left, right)))
        )
    return _Value(
        _alternatives(
            tuple(a + separator + b for a, b in product(left.texts, right.texts))
        ),
        targets=tuple(dict.fromkeys((*left.targets, *right.targets))),
    )


def _hostname(value: str) -> str:
    """Read a host from a URL, registry destination, or bare hostname.

    :param value: Selected authority or publication destination.
    :returns: Normalized hostname, or empty text for malformed input.
    """
    try:
        return (
            (urlsplit(value if "://" in value else "//" + value).hostname or "")
            .lower()
            .rstrip(".")
        )
    except ValueError:
        return ""


def _synthetic(value: str) -> bool:
    """Recognize reserved example authorities without operator-specific lists.

    :param value: Host, URL, or registry destination to classify.
    :returns: Whether its hostname is reserved for documentation or loopback.
    """
    host = _hostname(value)
    if not host or RUNTIME in host:
        return False
    if host == "localhost" or host.endswith(
        (".localhost", ".test", ".invalid", ".example")
    ):
        return True
    if any(
        host == item or host.endswith("." + item)
        for item in ("example.com", "example.net", "example.org")
    ):
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or any(
            address in network for network in RESERVED_NETWORKS
        )
    except ValueError:
        return False


def _field_selector(path: str) -> Optional[str]:
    """Reuse target-contract roles for operational configuration fields.

    :param path: Dotted declared target field, including named profiles.
    :returns: Portability category, or None for non-target configuration.
    """
    role = TARGET_CONTRACT.role(path)
    if role is Role.ACCESS:
        return "credential"
    if role is not Role.ENDPOINT:
        return None
    parts = TARGET_CONTRACT.path_parts(path)
    field = parts[-1]
    if field == "repository":
        return "repository"
    if field in {"project", "group"}:
        return "project"
    if field == "context":
        return "cluster"
    return "registry" if parts[0] == "harbor" else "host"


def _findings(
    source: str, line: int, selections: Sequence[_Selection]
) -> tuple[Finding, ...]:
    """Classify executable selectors using only their own synthetic authority.

    :param source: Selected source path.
    :param line: Line containing the consuming operation.
    :param selections: Resolved operational values and their local contexts.
    :returns: Redacted findings for fixed non-synthetic deployment selections.
    """
    synthetic = {
        item.authority
        for item in selections
        if item.selector in {"host", "registry"}
        and item.value.texts
        and all(_synthetic(value) for value in item.value.texts)
    }
    findings = []
    for item in selections:
        if item.selector == "source":
            findings.append(Finding(source, line, "source_parse_error", "source"))
            continue
        for value in item.value.texts:
            if RUNTIME in value and (
                item.selector not in {"host", "registry"} or RUNTIME in _hostname(value)
            ):
                continue
            if item.selector in {"host", "registry"} and _synthetic(value):
                continue
            if (
                item.selector not in {"host", "registry", "credential"}
                and item.authority in synthetic
            ):
                continue
            findings.append(Finding(source, line, "fixed_selector", item.selector))
            break
    return tuple(dict.fromkeys(findings))


def _command_selections(arguments: Sequence[_Value]) -> tuple[_Selection, ...]:
    """Extract native CLI selectors without inspecting inert output arguments.

    :param arguments: Positional command arguments resolved in the source scope.
    :returns: Selected authorities, credentials, API resources, and publications.
    """
    selected = []
    words = [item.texts[0] if len(item.texts) == 1 else RUNTIME for item in arguments]
    command = next(
        (Path(word).name for word in words if Path(word).name in NATIVE_COMMANDS), ""
    )
    for index, argument in enumerate(arguments):
        selected.extend(argument.targets)
        for word in argument.texts:
            flag, separator, inline = word.partition("=")
            selector = FLAG_SELECTORS.get(flag)
            if selector and (separator or index + 1 < len(arguments)):
                value = _Value((inline,)) if separator else arguments[index + 1]
                selected.append(_Selection(selector, value))
            if command == "glab" and word.startswith("projects/"):
                selected.append(_Selection("project", _Value((word.split("/", 2)[1],))))
            elif command == "gh" and word.startswith("repos/"):
                selected.append(
                    _Selection("repository", _Value(("/".join(word.split("/")[1:3]),)))
                )
            elif command in {"curl", "wget"} and "://" in word:
                selected.append(_Selection("host", _Value((word,))))
        if (
            command in {"podman", "docker"}
            and words[index] == "push"
            and index + 1 < len(arguments)
        ):
            selected.append(_Selection("registry", arguments[index + 1]))
        if (
            command in {"gh", "glab"}
            and words[index : index + 2]
            in (
                ["repo", "create"],
                ["repo", "clone"],
                ["repo", "view"],
            )
            and index + 2 < len(arguments)
        ):
            selected.append(_Selection("repository", arguments[index + 2]))
        if (
            command == "git"
            and words[index] in {"clone", "push", "fetch"}
            and index + 1 < len(arguments)
        ):
            remote = arguments[index + 1]
            if any("://" in value or "@" in value for value in remote.texts):
                selected.append(_Selection("repository", remote))
    return tuple(selected)


def _target_selections(
    document: Mapping[str, object], prefix: tuple[str, ...] = ()
) -> tuple[_Selection, ...]:
    """Classify a consumed target document through the shared target contract.

    :param document: Parsed target configuration.
    :param prefix: Authority and profile segments traversed so far.
    :returns: Concrete target fields with separate authority/profile contexts.
    """
    result = []
    for key, value in document.items():
        parts = (*prefix, key)
        if isinstance(value, Mapping):
            result.extend(_target_selections(value, parts))
        elif selector := _field_selector(TARGET_CONTRACT.dotted_path(parts)):
            values = value if isinstance(value, list) else [value]
            result.append(
                _Selection(
                    selector,
                    _Value(tuple(str(item) for item in values)),
                    ".".join(prefix),
                )
            )
    return tuple(result)


class _PythonScanner(ast.NodeVisitor):
    """Follow bounded local expressions into operational Python call sites.

    .. attribute :: visit_AsyncFunctionDef
        :type: function

        Alias using the same scope handling for asynchronous function bodies.
    """

    def __init__(self, source: str) -> None:
        """Create isolated lexical state for one source file.

        :param source: Repository-relative Python source.
        """
        self.source = source
        self.values: dict[str, _Value] = {}
        self.findings: list[Finding] = []

    def value(self, node: ast.AST) -> _Value:
        """Resolve local literals while preserving runtime and fallback branches.

        Delegated expression parsing can raise ValueError when its budget is exceeded.

        :param node: Expression to inspect without execution.
        :returns: Bounded symbolic value.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _Value((node.value,))
        if isinstance(node, ast.Name):
            return self.values.get(node.id, _Value())
        if isinstance(node, ast.Attribute):
            return _field_value(self.value(node.value), node.attr)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            return _field_value(self.value(node.value), node.slice.value)
        if isinstance(node, (ast.List, ast.Tuple)):
            return _Value(items=tuple(self.value(item) for item in node.elts))
        if isinstance(node, ast.Dict):
            return _Value(
                fields=tuple(
                    (key.value, self.value(value))
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            return _join(
                self.value(node.left),
                self.value(node.right),
                "/" if isinstance(node.op, ast.Div) else "",
            )
        if isinstance(node, ast.JoinedStr):
            value = _Value(("",))
            for part in node.values:
                value = _join(
                    value,
                    self.value(
                        part.value if isinstance(part, ast.FormattedValue) else part
                    ),
                )
            return value
        if isinstance(node, (ast.BoolOp, ast.IfExp)):
            branches = (
                node.values
                if isinstance(node, ast.BoolOp)
                else (node.body, node.orelse)
            )
            return _merge_values(tuple(self.value(branch) for branch in branches))
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func).rsplit(".", 1)[-1]
            authorities = {
                f"{authority}target": authority
                for authority in TARGET_CONTRACT.authorities
            }
            if name.lower() in authorities or name == "Target":
                fields = tuple(
                    (item.arg, self.value(item.value))
                    for item in node.keywords
                    if item.arg
                )
                targets = [target for _, value in fields for target in value.targets]
                authority = authorities.get(name.lower())
                if authority:
                    for field, value in fields:
                        if selector := _field_selector(f"{authority}.{field}"):
                            targets.append(_Selection(selector, value, authority))
                return _Value(fields=fields, targets=tuple(targets))
            if name in {"str", "Path"} and node.args:
                return self.value(node.args[0])
            if name in {"get", "getenv"} and len(node.args) > 1:
                return _merge_values((_Value(), self.value(node.args[1])))
            if name == "dict":
                return _Value(
                    fields=tuple(
                        (item.arg, self.value(item.value))
                        for item in node.keywords
                        if item.arg
                    )
                )
        return _Value()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Isolate function parameters and local constant assignments.

        :param node: Function whose body may contain operational calls.
        """
        previous = self.values
        self.values = dict(previous)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            self.values[argument.arg] = _Value()
        for statement in node.body:
            self.visit(statement)
        self.values = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        """Retain constant aliases and inspect executable sys.argv selections.

        :param node: Assignment in the current lexical scope.
        """
        value = self.value(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.values[target.id] = value
            elif ast.unparse(target) == "sys.argv":
                for choice in _value_choices(value):
                    for arguments in _value_groups(choice.items):
                        self.findings.extend(
                            _findings(
                                self.source, node.lineno, _command_selections(arguments)
                            )
                        )
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Retain typed constant assignments in the same lexical scope.

        :param node: Annotated assignment with an optional value.
        """
        if node.value is not None and isinstance(node.target, ast.Name):
            self.values[node.target.id] = self.value(node.value)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        """Preserve both possible local assignment branches.

        :param node: Conditional whose runtime result is unknown.
        """
        self.visit(node.test)
        original = dict(self.values)
        branches = []
        for body in (node.body, node.orelse):
            self.values = dict(original)
            for statement in body:
                self.visit(statement)
            branches.append(self.values)
        self.values = dict(original)
        for name in branches[0].keys() | branches[1].keys():
            values = [branch.get(name, _Value()) for branch in branches]
            self.values[name] = _merge_values(values)

    def visit_Call(self, node: ast.Call) -> None:
        """Inspect execution arguments and attach locally written target fixtures.

        :param node: Call expression; no callable is executed.
        :raises ValueError: A consumed expression exceeds the bounded syntax model.
        """
        name = ast.unparse(node.func)
        tail = name.rsplit(".", 1)[-1]
        selections = []
        if (
            tail == "write_text"
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.args
        ):
            content = self.value(node.args[0])
            targets = []
            for text in content.texts:
                targets.extend(_target_document_selections(text))
            if targets:
                variable = node.func.value.id
                self.values[variable] = replace(
                    self.values.get(variable, _Value()), targets=tuple(targets)
                )
        if tail in PROCESS_CALLS and node.args:
            arguments = tuple(self.value(item) for item in node.args)
            environment = next(
                (self.value(item.value) for item in node.keywords if item.arg == "env"),
                _Value(),
            )
            inspected = 0
            for call in _value_groups((*arguments, environment)):
                first, branch_environment = call[0], call[-1]
                for command in _value_groups(first.items or call[:-1]):
                    fields = tuple(value for _, value in branch_environment.fields)
                    for values in _value_groups(fields):
                        inspected += 1
                        if inspected > MAX_ALTERNATIVES:
                            raise ValueError("expression_budget_exceeded")
                        operation = list(_command_selections(command))
                        for (key, _), value in zip(branch_environment.fields, values):
                            operation.extend(value.targets)
                            if key in ENV_FIELDS and (
                                selector := _field_selector(ENV_FIELDS[key])
                            ):
                                operation.append(
                                    _Selection(
                                        selector, value, ENV_FIELDS[key].split(".")[0]
                                    )
                                )
                        self.findings.extend(
                            _findings(self.source, node.lineno, operation)
                        )
                if tail == "system" or (
                    not first.items
                    and any(
                        item.arg == "shell"
                        and isinstance(item.value, ast.Constant)
                        and item.value.value is True
                        for item in node.keywords
                    )
                ):
                    for text in first.texts:
                        self.findings.extend(
                            _scan_shell(self.source, text, node.lineno - 1)
                        )
        elif name.startswith(("requests.", "httpx.", "urllib.request.")) and node.args:
            selections.append(_Selection("host", self.value(node.args[0])))
        self.findings.extend(_findings(self.source, node.lineno, selections))
        self.generic_visit(node)


def _shell_value(token: str, values: Mapping[str, _Value]) -> _Value:
    """Resolve quoted shell literals, variable aliases, and fallback expansions.

    :param token: One token retaining its source quotes.
    :param values: Constant assignments in the current shell source.
    :returns: Literal and dynamic alternatives without running substitutions.
    :raises ValueError: Quotes or fallback syntax cannot be inspected safely.
    """
    if token.startswith("'") and token.endswith("'"):
        return _Value((token[1:-1],))
    result = _Value(("",))
    offset = 0
    for match in SHELL_EXPANSION.finditer(token):
        prefix = token[offset : match.start()].replace('"', "").replace("'", "")
        result = _join(result, _Value((prefix,)))
        variable = match[1] or match[4]
        value = values.get(variable or "", _Value())
        if match[2] in {"-", "+", "="}:
            fallback = _shell_value(match[3] or "", values)
            value = _Value(_alternatives((*value.texts, *fallback.texts)))
        result = _join(result, value)
        offset = match.end()
    suffix = token[offset:].replace('"', "").replace("'", "")
    if "${" in suffix:
        raise ValueError("unsupported_shell_expansion")
    return _join(result, _Value((suffix,)))


def _shell_file_key(token: str) -> str:
    """Keep local file bindings distinct without resolving runtime paths.

    :param token: Literal or variable-bearing file argument.
    :returns: Quote-normalized syntax with equivalent simple expansions unified.
    """
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"$\1", token.strip("'\""))


def _target_document_selections(text: str) -> tuple[_Selection, ...]:
    """Read generated target fields through the shared target contract.

    :param text: Literal document content; no runtime expressions are executed.
    :returns: Target selections, or a deferred failure for malformed target TOML.
    """
    if not TARGET_HEADER.search(text):
        return ()
    try:
        return _target_selections(tomllib.loads(text))
    except tomllib.TOMLDecodeError:
        return (_Selection("source", _Value()),)


def _shell_target_document(
    text: str, values: Mapping[str, _Value], *, expand: bool
) -> tuple[_Selection, ...]:
    """Retain target fields from a generated document until its file is consumed.

    :param text: Literal heredoc body, never executed.
    :param values: Local shell assignments for unquoted heredoc interpolation.
    :param expand: Whether the heredoc delimiter permits shell expansion.
    :returns: Target selections, or a deferred parse failure for a target document.
    """
    selections = _target_document_selections(text)
    if not expand or any(item.selector == "source" for item in selections):
        return selections
    return tuple(
        replace(selection, value=_shell_value(value, values))
        for selection in selections
        for value in selection.value.texts
    )


def _declaration_assignments(tokens: Sequence[str]) -> list[str]:
    """Normalize supported scalar declarations into ordinary assignment tokens.

    :param tokens: One shell command after control prefixes are removed.
    :returns: Its scalar assignments, or the unchanged non-declaration command.
    :raises ValueError: Declaration flags or argument syntax cannot be modeled.
    """
    if not tokens or tokens[0] not in SHELL_DECLARATIONS:
        return list(tokens)
    allowed = SHELL_DECLARATIONS[tokens[0]]
    remaining = list(tokens[1:])
    while remaining and remaining[0].startswith(("-", "+")):
        option = remaining.pop(0)
        if option == "--":
            break
        if (
            not option.startswith("-")
            or len(option) == 1
            or not set(option[1:]).issubset(allowed)
        ):
            raise ValueError("unsupported_shell_declaration")
    assignments = []
    for token in remaining:
        binding = SHELL_BINDING.fullmatch(token)
        if binding is None:
            raise ValueError("unsupported_shell_declaration")
        if binding[2] is not None:
            assignments.append(token)
    return assignments


def _scan_shell(
    source: str,
    text: str,
    line_offset: int = 0,
    initial_values: Optional[Mapping[str, _Value]] = None,
) -> tuple[Finding, ...]:
    """Inspect native shell invocations using bounded lexical tokenization.

    :param source: Source path containing shell commands.
    :param text: Shell source; runtime substitutions are never executed.
    :param line_offset: Offset for shell embedded in another source format.
    :param initial_values: Known outer assignments for explicitly executed stdin.
    :returns: Redacted selector findings.
    :raises ValueError: Selected command syntax cannot be tokenized safely.
    """
    findings = []
    values = dict(initial_values or {})
    files: dict[str, tuple[_Selection, ...]] = {}
    logical = ""
    start = 1
    heredoc: Optional[str] = None
    body: list[str] = []
    body_start = 1
    destination = ""
    expand = False
    execute_body = False
    for line, physical in enumerate(text.splitlines(), 1):
        if heredoc is not None:
            if physical.strip() == heredoc:
                content = "\n".join(body)
                if execute_body:
                    findings.extend(
                        _scan_shell(
                            source, content, body_start + line_offset - 1, values
                        )
                    )
                elif destination:
                    targets = _shell_target_document(content, values, expand=expand)
                    files[_shell_file_key(destination)] = targets
                    variable = re.fullmatch(
                        r"\$([A-Za-z_][A-Za-z0-9_]*)", _shell_file_key(destination)
                    )
                    if variable:
                        values[variable[1]] = replace(
                            values.get(variable[1], _Value()), targets=targets
                        )
                heredoc = None
            else:
                body.append(physical)
            continue
        if not logical:
            start = line
        logical += physical.removesuffix("\\")
        if physical.endswith("\\"):
            continue
        lexer = shlex.shlex(logical, posix=False, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            tokens = list(lexer)
        except ValueError as exc:
            if str(exc) == "No closing quotation":
                logical += "\n"
                continue
            raise
        header = logical
        logical = ""
        match = re.search(
            r"(?<!<)<<-?(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", header
        )
        if match:
            heredoc = match[2]
            body = []
            body_start = line + 1
            expand = not match[1]
            execute_body = False
            redirect = re.search(r">\s*(\"[^\"]*\"|'[^']*'|[^\s<]+)", header)
            destination = redirect[1] if redirect else ""
        groups: list[list[str]] = [[]]
        for token in tokens:
            if token in {";", "&&", "||", "|"}:
                groups.append([])
            else:
                groups[-1].append(token)
        for group in groups:
            while group and group[0] in {
                "if",
                "elif",
                "while",
                "until",
                "!",
                "then",
                "do",
                "else",
            }:
                group.pop(0)
            group = _declaration_assignments(group)
            environment = []
            while (
                group
                and (binding := SHELL_BINDING.fullmatch(group[0]))
                and binding[2] is not None
            ):
                variable, token = binding[1], binding[2]
                group.pop(0)
                value = _shell_value(token, values)
                value = replace(
                    value, targets=files.get(_shell_file_key(token), value.targets)
                )
                values[variable] = value
                if variable == "CI_SKILLS_TARGET":
                    environment.extend(value.targets)
                elif variable in ENV_FIELDS and (
                    selector := _field_selector(ENV_FIELDS[variable])
                ):
                    environment.append(
                        _Selection(selector, value, ENV_FIELDS[variable].split(".")[0])
                    )
            if not group:
                continue
            executable = group[0].strip("'\"")
            if (
                executable not in NATIVE_COMMANDS | COMMAND_WRAPPERS
                and not executable.endswith(".py")
                and Path(executable).name not in {"ci-api", "ci-binary-build"}
            ):
                continue
            arguments = tuple(
                replace(value, targets=files.get(_shell_file_key(token), value.targets))
                for token in group
                for value in (_shell_value(token, values),)
            )
            findings.extend(
                _findings(
                    source,
                    start + line_offset,
                    (*environment, *_command_selections(arguments)),
                )
            )
            if heredoc:
                execute_body = any(
                    Path(argument.texts[0]).name in {"bash", "sh"}
                    and arguments[index + 1].texts == ("-s",)
                    for index, argument in enumerate(arguments[:-1])
                )
            for index, argument in enumerate(arguments[:-2]):
                if argument.texts[0] in {"bash", "sh"} and arguments[
                    index + 1
                ].texts == ("-c",):
                    for fragment in arguments[index + 2].texts:
                        findings.extend(
                            _scan_shell(source, fragment, start + line_offset - 1)
                        )
    if logical or heredoc is not None:
        raise ValueError("incomplete_shell_continuation")
    return tuple(dict.fromkeys(findings))


def scan_source(source: str, text: str) -> tuple[Finding, ...]:
    """Inspect one executable source without invoking it or contacting providers.

    :param source: Repository-relative source path selecting its language.
    :param text: UTF-8 source contents from the selected snapshot.
    :returns: Redacted findings, including unsupported or invalid source syntax.
    :raises ValueError: Internally for invalid or over-budget YAML; caught and
        converted into a source finding.
    """
    try:
        if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
            return (Finding(source, 1, "source_budget_exceeded", "source"),)
        if source.endswith(".py") or (
            text.startswith("#!") and "python" in text.splitlines()[0]
        ):
            tree = ast.parse(text, filename=source)
            if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
                return (Finding(source, 1, "source_budget_exceeded", "source"),)
            scanner = _PythonScanner(source)
            scanner.visit(tree)
            return tuple(dict.fromkeys(scanner.findings))
        if Path(source).suffix in {".yml", ".yaml"}:
            import yaml

            try:
                document = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                raise ValueError("invalid_yaml_source") from exc
            fragments = []
            pending = [document]
            visited = set()
            while pending:
                item = pending.pop()
                if isinstance(item, (Mapping, list)):
                    if id(item) in visited:
                        continue
                    visited.add(id(item))
                    if len(visited) > MAX_NODES:
                        raise ValueError("source_budget_exceeded")
                if isinstance(item, Mapping):
                    for key, value in item.items():
                        if key in {"run", "script", "before_script", "after_script"}:
                            fragments.extend(
                                value if isinstance(value, list) else [value]
                            )
                        elif isinstance(value, (Mapping, list)):
                            pending.append(value)
                elif isinstance(item, list):
                    pending.extend(item)
            return tuple(
                finding
                for fragment in fragments
                if isinstance(fragment, str)
                for finding in _scan_shell(source, fragment)
            )
        return _scan_shell(source, text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return (
            Finding(
                source,
                getattr(exc, "lineno", None) or 1,
                "source_parse_error",
                "source",
            ),
        )


class PortabilityGate:
    """Inspect the existing blessing snapshot and its selected path inventory."""

    def __init__(self, root: Path) -> None:
        """Retain the caller-selected source snapshot without filesystem discovery.

        :param root: Existing blessing working-tree or staged snapshot root.
        """
        self.root = root.resolve()

    def run(self, paths: Sequence[str]) -> GateResult:
        """Read only selected executable smoke and test sources.

        :param paths: Repository-relative paths from the existing NUL inventory.
        :returns: Checked sources and failures; missing or escaped sources fail closed.
        :raises OSError: Internally for escaped source paths; caught and converted
            into unreadable-source findings.
        """
        checked = []
        findings = []
        for source in sorted(set(paths)):
            path = Path(source)
            if path.is_absolute() or ".." in path.parts:
                findings.append(Finding(source, 0, "path_invalid", "source"))
                continue
            if (
                not (path.parts and path.parts[0] == "tests")
                and "smoke" not in path.stem.lower()
            ):
                continue
            if path.suffix in DATA_SUFFIXES or path.parts[0] in {"docs", "inventory"}:
                continue
            if path.suffix and path.suffix not in SOURCE_SUFFIXES:
                continue
            checked.append(source)
            selected = self.root / path
            try:
                if not selected.resolve().is_relative_to(self.root):
                    raise OSError("source_escaped_snapshot")
                text = selected.read_text(encoding="utf-8")
                if not path.suffix and not text.startswith("#!"):
                    continue
                findings.extend(scan_source(source, text))
            except (OSError, UnicodeError):
                findings.append(Finding(source, 0, "source_unreadable", "source"))
        return GateResult(tuple(checked), tuple(findings))
