"""Typed catalog declarations shared by command parsers and discovery.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict


class VerbDefinition(TypedDict):
    """One operation and its additional required inputs.

    .. attribute :: purpose
        :type: NotRequired[str]

        Short action description.

    .. attribute :: required_options
        :type: NotRequired[Sequence[str]]

        Option names required for this verb.

    .. attribute :: side_effects
        :type: NotRequired[str]

        Effects of applying this verb.

    .. attribute :: result_schema
        :type: NotRequired[str]

        Installed schema pointer for this operation result.

    .. attribute :: mutates
        :type: NotRequired[bool]

        Whether this operation can change provider state.

    .. attribute :: read_back
        :type: NotRequired[str]

        Provider observations used to verify the operation.
    """

    purpose: NotRequired[str]
    required_options: NotRequired[Sequence[str]]
    side_effects: NotRequired[str]
    result_schema: NotRequired[str]
    mutates: NotRequired[bool]
    read_back: NotRequired[str]


class CommandDefinition(TypedDict):
    """One provider command consumed by parsers and machine discovery.

    .. attribute :: kind

        Stable report kind.

    .. attribute :: purpose

        Short capability description.

    .. attribute :: use_when

        Task this command handles.

    .. attribute :: requires

        Runtime authorities used by this command.

    .. attribute :: capabilities

        Shared option tiers supported by this command.

    .. attribute :: options

        Command-specific options and help text.

    .. attribute :: returns

        Result description.

    .. attribute :: mutates

        Whether an operation can change provider state.

    .. attribute :: subcommands

        Supported operations.

    .. attribute :: related

        Command filenames offered by this node.

    .. attribute :: required_tools

        Native tools or libraries consumed at runtime.

    .. attribute :: required_options

        Required inputs for the default operation.

    .. attribute :: execution_surface

        Runtime location of the operation.

    .. attribute :: side_effects

        Observable effects beyond the result.

    """

    kind: str
    purpose: str
    use_when: str
    requires: Sequence[str]
    capabilities: Sequence[str]
    options: Mapping[str, str]
    returns: str
    mutates: NotRequired[bool]
    subcommands: NotRequired[Mapping[str, VerbDefinition]]
    related: NotRequired[Sequence[str]]
    required_tools: NotRequired[Sequence[str]]
    required_options: NotRequired[Sequence[str]]
    execution_surface: NotRequired[str]
    side_effects: NotRequired[str]


class NodeDefinition(TypedDict):
    """One node-local diagnostic routed through an existing Pod.

    .. attribute :: kind

        Stable report kind.

    .. attribute :: requires

        Runtime authorities required for the read.

    .. attribute :: purpose

        Short diagnostic description.

    .. attribute :: use_when

        Task this diagnostic handles.

    .. attribute :: required_tools

        Native and in-Pod tools used by the diagnostic.

    .. attribute :: returns

        Result description.

    .. attribute :: options

        Additional diagnostic-specific options.

    .. attribute :: related

        Related command filenames.

    """

    kind: str
    requires: Sequence[str]
    purpose: str
    use_when: str
    required_tools: Sequence[str]
    returns: str
    options: NotRequired[Mapping[str, str]]
    related: NotRequired[Sequence[str]]


class ReadDefinition(TypedDict):
    """One native API or offline planning command.

    .. attribute :: report_kind

        Stable report kind.

    .. attribute :: purpose

        Short capability description.

    .. attribute :: use_when

        Task this command handles.

    .. attribute :: requires_authorities

        Runtime authority selection described to callers.

    .. attribute :: required_tools

        Native tools and libraries required.

    .. attribute :: required_options

        Required command inputs.

    .. attribute :: options

        Accepted options and their help text.

    .. attribute :: subcommands

        Supported operations when the command has verbs.

    .. attribute :: description

        Expanded capability explanation.

    .. attribute :: examples

        Runnable example command descriptions.

    .. attribute :: output_modes

        Supported serialization modes.

    .. attribute :: usage

        Invocation guidance.

    .. attribute :: returns

        Result description.

    .. attribute :: read_only

        Whether provider state remains unchanged.

    .. attribute :: side_effects

        Effects beyond the result.

    .. attribute :: execution_surface

        Runtime location of the operation.

    """

    report_kind: str
    purpose: str
    use_when: str
    requires_authorities: Sequence[str]
    required_tools: Sequence[str]
    required_options: Sequence[str]
    options: Mapping[str, str]
    subcommands: NotRequired[Mapping[str, VerbDefinition]]
    description: str
    examples: Sequence[Mapping[str, str]]
    output_modes: Mapping[str, str]
    usage: str
    returns: str
    read_only: bool
    side_effects: str
    execution_surface: str


class DiscoveryDefinition(TypedDict):
    """One offline catalog expansion command.

    .. attribute :: report_kind

        Stable report kind.

    .. attribute :: purpose

        Short discovery description.

    .. attribute :: use_when

        Task this command handles.

    .. attribute :: requires_authorities

        Required authorities; empty for offline discovery.

    .. attribute :: required_tools

        Interpreter used by the command.

    .. attribute :: required_options

        Required inputs; empty for root discovery.

    .. attribute :: options

        Shared option names supported by discovery.

    .. attribute :: description

        Expanded capability explanation.

    .. attribute :: examples

        Discovery invocation examples.

    .. attribute :: output_modes

        Supported serialization modes.

    .. attribute :: usage

        Invocation guidance.

    .. attribute :: returns

        Result description.

    .. attribute :: read_only

        Whether provider state remains unchanged.

    .. attribute :: side_effects

        Effects beyond the result.

    .. attribute :: execution_surface

        Offline execution location.

    """

    report_kind: str
    purpose: str
    use_when: str
    requires_authorities: Sequence[str]
    required_tools: Sequence[str]
    required_options: Sequence[str]
    options: Sequence[str]
    description: str
    examples: Sequence[Mapping[str, str]]
    output_modes: Mapping[str, str]
    usage: str
    returns: str
    read_only: bool
    side_effects: str
    execution_surface: str
