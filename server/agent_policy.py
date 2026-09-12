"""Server-owned tool authorization for each agent invocation.

Model prompts may describe a capability but can never add one: only the policy
passed by a trusted route or worker controls declarations and execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgentRunKind(str, Enum):
    AUTOMATED_INBOUND = "automated_inbound"
    INTERACTIVE_EXECUTIVE = "interactive_executive"
    INTERACTIVE_STREAM = "interactive_stream"


class AgentTool(str, Enum):
    CREATE_DRAFT = "create_draft"
    MARK_AS_READ = "mark_as_read"
    SEND_EMAIL = "send_email"
    SEND_REPLY = "send_reply_to_user"
    CREATE_EVENT = "create_event"
    CREATE_TASK = "create_task"
    GET_CALENDAR_EVENTS = "get_calendar_events"


# All tools that can alter Gmail, Calendar, Tasks, or pending-action state are
# mutations. A run may accept exactly one of them regardless of how many
# function calls the model returns.
PRIMARY_MUTATION_TOOLS = frozenset(
    {
        AgentTool.CREATE_DRAFT,
        AgentTool.MARK_AS_READ,
        AgentTool.SEND_EMAIL,
        AgentTool.SEND_REPLY,
        AgentTool.CREATE_EVENT,
        AgentTool.CREATE_TASK,
    }
)


@dataclass(frozen=True)
class AgentToolPolicy:
    """Immutable allowlist and mutation budget for one trusted agent run."""

    run_kind: AgentRunKind
    allowed_tools: frozenset[AgentTool]
    max_primary_mutations: int = 1

    def allows(self, tool_name: str) -> bool:
        try:
            return AgentTool(tool_name) in self.allowed_tools
        except ValueError:
            return False

    def is_primary_mutation(self, tool_name: str) -> bool:
        try:
            return AgentTool(tool_name) in PRIMARY_MUTATION_TOOLS
        except ValueError:
            return False


AUTOMATED_INBOUND_POLICY = AgentToolPolicy(
    run_kind=AgentRunKind.AUTOMATED_INBOUND,
    allowed_tools=frozenset(),
    max_primary_mutations=0,
)

# Draft creation and mark-read are deliberately immediate *interactive*
# mutations. They are not exposed to automated inbound mail processing. Live
# sends/replies/events/tasks below always create typed pending actions.
INTERACTIVE_EXECUTIVE_POLICY = AgentToolPolicy(
    run_kind=AgentRunKind.INTERACTIVE_EXECUTIVE,
    allowed_tools=frozenset(
        {
            AgentTool.CREATE_DRAFT,
            AgentTool.MARK_AS_READ,
            AgentTool.SEND_EMAIL,
            AgentTool.SEND_REPLY,
            AgentTool.CREATE_EVENT,
            AgentTool.CREATE_TASK,
            AgentTool.GET_CALENDAR_EVENTS,
        }
    ),
)

INTERACTIVE_STREAM_POLICY = AgentToolPolicy(
    run_kind=AgentRunKind.INTERACTIVE_STREAM,
    allowed_tools=frozenset({AgentTool.SEND_EMAIL, AgentTool.CREATE_TASK}),
)
