from asyncio import CancelledError
import datetime
import inspect
import logging
from functools import partial
import yaml  # YAML profile parsing is retained for the current agent contract.
from typing import Optional, List, AsyncGenerator, Any, Callable, Dict
from googleapiclient.discovery import Resource

from clients.base import Agent
from server.integrations.llm import (
    LlmOperationInternal,
    LlmOperationTimeout,
    LlmOperationUnavailable,
)
from google.genai.types import (
    GenerateContentConfig, Content, Part,
    Tool, FunctionDeclaration, Schema, Type
)
# Assuming your prompts are in clients/prompt.py
from clients.prompt import EMAIL_AGENT_PROMPT, GENERAL_AGENT_PROMPT ,EXECUTIVE_AGENT_PROMPT

from server.services.pending_actions import issue_command_key
from tools.llm_tools import (
    send_email, create_draft, create_event, create_task,
    mark_as_read, send_reply_to_user, get_calendar_events# Ensure all referenced tools are imported
)

logger = logging.getLogger(__name__)

_PENDING_ACTION_TOOL_NAMES = {"send_email", "send_reply_to_user", "create_event", "create_task"}


def _command_key_for_tool_call(
    command_keys: dict[str, str],
    *,
    scope: str,
    function_call: Any,
) -> str:
    """Return one server key for a provider function-call delivery/retry."""
    provider_call_id = getattr(function_call, "id", None)
    identity = (
        f"provider:{provider_call_id}"
        if isinstance(provider_call_id, str) and provider_call_id
        else scope
    )
    return command_keys.setdefault(identity, issue_command_key())


# --- Helper function to load configuration ---
def load_config(config_path: str = "user_config.yaml") -> Dict[str, Any]:
    """Load the current user profile contract."""
    try:
        with open(config_path, "r") as file:
            config = yaml.safe_load(file)
        logger.info("Configuration loaded successfully from %s", config_path)
        return config
    except FileNotFoundError:
        logger.error("Config file not found at %s. Ensure it is available.", config_path)
        return {}
    except yaml.YAMLError as error:
        logger.error("Error parsing config %s: %s", config_path, error)
        return {}



# --- Function Declarations (MUST be accurate and complete for the LLM to use tools correctly) ---
send_email_func_decl = FunctionDeclaration(
    name="send_email",
    description=(
        "Prepares an email send action for the authenticated user to review. "
        "This tool never sends email. It returns an action ID which the user must "
        "approve through the separate approval API before the server sends it."
    ),
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "recipient_email": Schema(type=Type.STRING, description="The email address of the recipient."),
            "subject": Schema(type=Type.STRING, description="The subject of the email."),
            "email_body": Schema(type=Type.STRING, description="The body content of the email."),
        },
        required=["recipient_email", "subject", "email_body"],
    ),
)

create_draft_func_decl = FunctionDeclaration(
    name="create_draft",
    description="Prepares a draft email reply in Gmail for Khushwant to review before sending. Use for most email responses.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "recipient_email": Schema(type=Type.STRING, description="The email address of the recipient (extracted from 'from' field of original email)."),
            "subject": Schema(type=Type.STRING, description="The subject of the email (e.g., 'Re: [Original Subject]')."),
            "email_body": Schema(type=Type.STRING, description="The complete, professional body content of the reply email."),
        },
        required=["recipient_email", "subject", "email_body"],
    ),
)

create_event_func_decl = FunctionDeclaration(
    name="create_event",
    description=(
        "Prepares a calendar event action for the authenticated user to review. "
        "This tool never creates an event. It returns an action ID which the user must "
        "approve through the separate approval API before the server creates it."
    ),
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "title": Schema(type=Type.STRING, description="The title or summary of the event."),
            "start_time_iso": Schema(type=Type.STRING, description="The start date and time of the event in ISO 8601 format (e.g., '2024-07-30T10:00:00+05:30')."),
            "end_time_iso": Schema(type=Type.STRING, description="The end date and time of the event in ISO 8601 format (e.g., '2024-07-30T11:00:00+05:30')."),
            "event_timezone": Schema(type=Type.STRING, description="The timezone for the event (e.g., 'Asia/Kolkata', 'America/New_York')."),
            "description": Schema(type=Type.STRING, description="A detailed description of the event. Optional."),
            "location": Schema(type=Type.STRING, description="The location of the event. Optional."),
            "attendee_emails": Schema(
                type=Type.ARRAY,
                items=Schema(type=Type.STRING),
                description="A list of email addresses of attendees to invite. Optional."
            ),
        },
        required=["title", "start_time_iso", "end_time_iso", "event_timezone"],
    ),
)

create_task_func_decl = FunctionDeclaration(
    name="create_task",
    description=(
        "Prepares a Google Task action for the authenticated user to review. "
        "This tool never creates a task; the user must approve the returned action ID."
    ),
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "title": Schema(type=Type.STRING, description="The title of the task."),
            "notes": Schema(type=Type.STRING, description="Additional notes or description for the task."),
        },
        required=["title", "notes"],
    ),
)

mark_as_read_func_decl = FunctionDeclaration(
    name="mark_as_read",
    description="Marks a specific email message as read given its ID. Use for informational/spam emails.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={"message_id": Schema(type=Type.STRING, description="The ID of the email message to mark as read.")},
        required=["message_id"],
    ),
)

mark_as_unread_func_decl = FunctionDeclaration( # Keep if you might need it, even if not in main prompt list
    name="mark_as_unread",
    description="Marks a specific email message as unread given its ID.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={"message_id": Schema(type=Type.STRING, description="The ID of the email message to mark as unread.")},
        required=["message_id"],
    ),
)

send_reply_to_user_func_decl = FunctionDeclaration(
    name="send_reply_to_user",
    description=(
        "Prepares a reply action for the authenticated user to review. "
        "This tool resolves the target message but never sends a reply. The user must "
        "approve the returned action ID through the separate approval API."
    ),
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "recipient_email": Schema(type=Type.STRING, description="The email address of the original sender to whom the reply should be sent."),
            "subject_filter": Schema(type=Type.STRING, description="A keyword or phrase to find in the subject of the email to reply to."),
            "reply_message": Schema(type=Type.STRING, description="The content of the reply message."),
        },
        required=["recipient_email", "subject_filter", "reply_message"],
    ),
)


get_calendar_events_func_decl = FunctionDeclaration(
    name="get_calendar_events",
    description="Retrieves calendar events for a specific list of dates. Useful for checking your schedule, availability, or upcoming appointments. Requires dates in 'dd-mm-yyyy' format.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "date_strs": Schema(
                type=Type.ARRAY,
                items=Schema(type=Type.STRING),
                description="A list of dates to check, each in 'dd-mm-yyyy' format (e.g., ['30-07-2024', '31-07-2024']). You can provide multiple dates."
            ),
            "target_timezone": Schema(
                type=Type.STRING,
                description="The desired timezone for displaying event times (e.g., 'Asia/Kolkata', 'America/New_York'). Defaults to 'Asia/Kolkata' if not specified."
            ),
        },
        required=["date_strs"], # 'target_timezone' is optional as it has a default
    ),
)

class ExecutiveAgent(Agent):
    def __init__(self, user_id: str, model_name: str = "gemini-2.0-flash-lite"):
        super().__init__(model_name)
        self.user_id = user_id
        self.available_python_tools: Dict[str, Callable] = {}
        self._tool_command_keys: dict[str, str] = {}
        # Load config once when the agent is initialized
        self.user_config = load_config() 

    def _prepare_tool_objects_and_python_callables(
        self,
        gmail_service: Optional[Resource] = None,
        calendar_service: Optional[Resource] = None,
        tasks_service: Optional[Resource] = None,
        current_user_email: Optional[str] = None,
        allow_tools: bool = True,
    ) -> List[Tool]:
        self.available_python_tools = {}
        if not allow_tools:
            return []
        function_declarations_for_tool_config = []

        if gmail_service and current_user_email:
            self.available_python_tools["mark_as_read"] = partial(mark_as_read, gmail_service)
            function_declarations_for_tool_config.append(mark_as_read_func_decl)

            self.available_python_tools["create_draft"] = partial(create_draft, gmail_service, current_user_email)
            function_declarations_for_tool_config.append(create_draft_func_decl)
            
            self.available_python_tools["send_email"] = partial(send_email, self.user_id, current_user_email)
            function_declarations_for_tool_config.append(send_email_func_decl)
            
            self.available_python_tools["send_reply_to_user"] = partial(
                send_reply_to_user,
                gmail_service,
                self.user_id,
                current_user_email,
            )
            function_declarations_for_tool_config.append(send_reply_to_user_func_decl)

        if calendar_service:
            self.available_python_tools["create_event"] = partial(create_event, self.user_id)
            function_declarations_for_tool_config.append(create_event_func_decl)
            
            self.available_python_tools["get_calendar_events"] = partial(get_calendar_events, calendar_service)
            function_declarations_for_tool_config.append(get_calendar_events_func_decl)
        
        if tasks_service:
            self.available_python_tools["create_task"] = partial(create_task, self.user_id)
            function_declarations_for_tool_config.append(create_task_func_decl)

        if not function_declarations_for_tool_config:
            return []
            
        return [Tool(function_declarations=function_declarations_for_tool_config)]

    async def run(
        self,
        input_query: str,
        gmail_service: Optional[Resource] = None,
        calendar_service: Optional[Resource] = None,
        tasks_service: Optional[Resource] = None,
        current_user_email: Optional[str] = None,
        allow_tools: bool = True,
    ) -> str:
        self._tool_command_keys = {}
        now = datetime.datetime.now()
        tomorrow_date = now + datetime.timedelta(days=1)

        # --- Load user data from config ---
        user_config = self.user_config 
        
        # Extract specific user data fields, providing defaults if missing
        user_full_name = user_config.get('full_name', 'Khushwant Sanwalot')
        user_name = user_config.get('name', 'Khushwant') 
        user_title = user_config.get('title', 'Founder of Hexel Studio')
        user_timezone = user_config.get('timezone', 'Asia/Kolkata')
        priority_contacts = user_config.get('important_contacts', [])
        user_background = user_config.get('background', 'No background provided.')
        user_schedule_preferences = user_config.get('schedule_preferences', 'No specific schedule preferences.')
        user_background_preferences = user_config.get('background_preferences', 'No specific background preferences.')
        user_response_preferences = user_config.get('response_preferences', 'No specific response preferences.')

        priority_contacts_str = ', '.join(priority_contacts) if priority_contacts else 'None'
        
        # Prepare date/time variables for the prompt template
        current_date_time_for_prompt = now.strftime('%Y-%m-%d %H:%M:%S %Z')
        current_date_for_prompt = now.strftime('%Y-%m-%d')
        tomorrow_date_for_prompt = tomorrow_date.strftime('%Y-%m-%d')

        # Construct the explicit date guidance section of the prompt
        date_guidance = (
            f"**IMPORTANT CURRENT DATE AND TIME CONTEXT:**\n"
            f"The current date is {now.strftime('%A, %Y-%m-%d')}.\n" 
            f"The current time is {now.strftime('%H:%M:%S %Z')}.\n"
            f"Therefore, 'tomorrow' refers specifically to {tomorrow_date.strftime('%A, %Y-%m-%d')}.\n"
            f"When using date/time arguments for tools (like `start_time_iso`, `end_time_iso` for `create_event` or `date_strs` for `get_calendar_events`):\n"
            f"- Always use the full ISO 8601 format: `YYYY-MM-DDTHH:MM:SS+HH:MM` (e.g., '2024-07-30T10:00:00+05:30').\n"
            f"- Ensure the YEAR is correct (currently {now.year}).\n"
            f"- For 'tomorrow', use the date {tomorrow_date_for_prompt}.\n"
            f"- For 'today', use the date {current_date_for_prompt}.\n"
            f"- The default timezone for events should be '{user_timezone}' unless specified otherwise.\n"
        )

        # Format the main prompt template with all dynamically generated context
        final_system_prompt = (
            f"{date_guidance}\n\n"
            f"{EXECUTIVE_AGENT_PROMPT.format(user_full_name=user_full_name, user_name=user_name, user_title=user_title, \
                                                   current_date_time=current_date_time_for_prompt, user_timezone=user_timezone, \
                                                   priority_contacts_str=priority_contacts_str, \
                                                   user_background=user_background, \
                                                   user_schedule_preferences=user_schedule_preferences, \
                                                   user_background_preferences=user_background_preferences, \
                                                   user_response_preferences=user_response_preferences)}"
        )

        logger.debug("Executive agent request received")
        
        tool_objects_for_api = self._prepare_tool_objects_and_python_callables(
            gmail_service,
            calendar_service,
            tasks_service,
            current_user_email,
            allow_tools,
        )

        system_instruction_content = None
        if final_system_prompt:
            system_instruction_content = Content(parts=[Part(text=final_system_prompt)])

        history: List[Content] = [Content(parts=[Part(text=input_query)], role="user")]
        
        gen_config = GenerateContentConfig(
            temperature=0.0,
            tools=tool_objects_for_api if tool_objects_for_api else None,
            system_instruction=system_instruction_content
        )
        
        max_turns = 5 
        for turn in range(max_turns):
            logger.debug(f"Turn {turn+1}/{max_turns}. Calling LLM with history length {len(history)}.")
            response = await self.provider.generate(
                model=self.model_name,
                contents=history,
                config=gen_config,
            )

            if not response.candidates:
                logger.warning("No candidates received from LLM.")
                raise LlmOperationInternal()
                
            candidate = response.candidates[0]
            
            function_calls_to_execute = []
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.function_call:
                        function_calls_to_execute.append(part.function_call)

            if function_calls_to_execute:
                # Append the model's turn that contains all function calls (e.g., model outputted: call_tool(tool_a), call_tool(tool_b))
                history.append(candidate.content) 

                function_response_parts = []
                for function_call_index, function_call in enumerate(function_calls_to_execute):
                    function_name = function_call.name
                    args = dict(function_call.args) if function_call.args else {}
                    # command_key is never part of the declared LLM schema. Drop
                    # any hallucinated/supplied value before adding the server key.
                    args.pop("command_key", None)

                    logger.info("LLM requested function call")

                    if function_name in self.available_python_tools:
                        python_function_to_call = self.available_python_tools[function_name]
                        try:
                            hidden_kwargs = {}
                            if function_name in _PENDING_ACTION_TOOL_NAMES:
                                hidden_kwargs["command_key"] = _command_key_for_tool_call(
                                    self._tool_command_keys,
                                    scope=f"executive:{turn}:{function_call_index}:{function_name}",
                                    function_call=function_call,
                                )
                            tool_result = python_function_to_call(**args, **hidden_kwargs)
                            if not inspect.isawaitable(tool_result):
                                raise RuntimeError("Agent tool must be asynchronous")
                            api_response = await tool_result
                            
                            # --- Start of POST-TOOL EXECUTION: Generate human-readable response ---
                            final_response_message = ""
                            if isinstance(api_response, dict) and api_response.get("status") == "pending_approval":
                                summary = api_response.get("summary", "this action")
                                action_id = api_response.get("action_id")
                                return f"⏸ Approval required: {summary}. Approve action {action_id} to continue."
                            if isinstance(api_response, dict) and api_response.get("action_id"):
                                action_id = api_response["action_id"]
                                action_status = api_response.get("status", "unknown")
                                return f"Action {action_id} is already {action_status}; no new external action was executed."
                            if function_name == "create_event":
                                if api_response: # Assuming create_event returns True on success
                                    event_title = args.get('title', 'an event')
                                    start_time_iso = args.get('start_time_iso')
                                    # Example: Parse ISO string to a more readable format for display
                                    try:
                                        start_dt = datetime.datetime.fromisoformat(start_time_iso)
                                        display_date_time = start_dt.strftime('%d-%m-%Y %H:%M')
                                    except ValueError:
                                        display_date_time = start_time_iso # Fallback if parsing fails
                                    final_response_message = f"✓ Scheduled '{event_title}' for {display_date_time}."
                                else:
                                    final_response_message = "❌ Couldn't schedule the event. There was an issue with the calendar service."
                            elif function_name == "create_task":
                                if api_response: # Assuming create_task returns the task object on success
                                    task_title = api_response.get('title', args.get('title', 'a task'))
                                    final_response_message = f"✓ Added task '{task_title}' to your list."
                                else:
                                    final_response_message = "❌ Couldn't add the task. Please check the task service."
                            elif function_name == "create_draft":
                                if api_response: # Assuming create_draft returns True on success
                                    recipient = args.get('recipient_email', 'unknown recipient')
                                    subject = args.get('subject', 'no subject')
                                    final_response_message = f"✓ Draft reply prepared for {recipient} with subject '{subject}'."
                                else:
                                    final_response_message = "❌ Couldn't create the draft. Please check email service."
                            elif function_name == "mark_as_read":
                                if api_response:
                                    final_response_message = f"✓ Message marked as read."
                                else:
                                    final_response_message = f"❌ Couldn't mark the message as read."
                            elif function_name == "send_email" or function_name == "send_reply_to_user":
                                if api_response:
                                    final_response_message = f"✓ Email sent successfully."
                                else:
                                    final_response_message = f"❌ Couldn't send the email."
                            elif function_name == "get_calendar_events":
                                if api_response and isinstance(api_response, dict): # api_response is a dict of date_str to event summary strings
                                    # Concatenate all event summaries
                                    events_summary = "\n".join(api_response.values())
                                    final_response_message = f"📅 Here's your schedule:\n{events_summary}"
                                else:
                                    final_response_message = "No calendar events found or an error occurred."
                            else:
                                # Default for other tools or unexpected return types
                                final_response_message = f"Tool '{function_name}' executed. Result: {str(api_response)}"
                            # --- End of POST-TOOL EXECUTION ---

                            function_response_parts.append(
                                Part(
                                    function_response={
                                        "name": function_name,
                                        "response": {"result": final_response_message}, 
                                    }
                                )
                            )
                        except Exception:
                            logger.exception("Error executing tool %s", function_name)
                            function_response_parts.append(
                                Part(
                                    function_response={
                                        "name": function_name,
                                        "response": {"error": "tool_unavailable"},
                                    }
                                )
                            )
                    else:
                        logger.warning(f"LLM called unknown function: {function_name}")
                        function_response_parts.append(
                            Part(
                                function_response={
                                    "name": function_name,
                                    "response": {"error": f"Function '{function_name}' is not available or not declared for this agent."},
                                }
                            )
                        )
                # After processing all function calls, append *all* responses in one tool turn
                history.append(Content(parts=function_response_parts, role="tool"))
            else: # No function calls were found in the candidate's content, so it's a text response.
                final_text = ""
                if hasattr(candidate, 'content') and candidate.content and candidate.content.parts:
                    text_parts = [part.text for part in candidate.content.parts if hasattr(part, 'text') and part.text]
                    final_text = "".join(text_parts)
                elif hasattr(response, 'text') and response.text:
                     final_text = response.text
                else: 
                    final_text = "No textual response from LLM after processing."
                    if candidate.finish_reason:
                        final_text += f" (Finish reason: {candidate.finish_reason.name})"
                logger.debug("Executive agent received final response")
                return final_text
        
        logger.warning("Max function call turns reached for ExecutiveAgent.")
        return "Max function call turns reached. Could not complete the request with a final textual answer."

    
class GeneralAgent(Agent):
    def __init__(self, user_id: str, model_name: str = "gemini-2.0-flash-lite"):
        super().__init__(model_name)
        self.user_id = user_id

    async def run(
        self,
        input_query: str,
        system_prompt: str = EMAIL_AGENT_PROMPT
    ) -> str:
        logger.debug("General agent request received")
        
        history: List[Content] = [Content(parts=[Part(text=input_query)], role="user")]
        
        system_instruction_content = None
        if system_prompt:
            system_instruction_content = Content(parts=[Part(text=system_prompt)])

        gen_config = GenerateContentConfig(
            temperature=0.0,
            system_instruction=system_instruction_content
        )

        try:
            response = await self.provider.generate(
                model=self.model_name,
                contents=history,
                config=gen_config,
            )
            final_text = getattr(response, 'text', None)
            if final_text is None and response.candidates and response.candidates[0].content:
                text_parts = [part.text for part in response.candidates[0].content.parts if hasattr(part, 'text') and part.text]
                final_text = "".join(text_parts)

            return final_text if final_text is not None else str(response)
        except Exception:
            logger.exception("General agent execution failed")
            # Re-raise rather than returning an "Error: ..." string as if it
            # were a successful result. The caller (route) is responsible for
            # mapping this to a proper error response.
            raise

class GeneralAgentStreamer(Agent):
    def __init__(self, user_id: str, model_name: str = "gemini-2.0-flash-lite"):
        super().__init__(model_name)
        self.user_id = user_id
        self.available_python_tools: Dict[str, Callable] = {}
        self._tool_command_keys: dict[str, str] = {}

    def _prepare_tool_objects_and_python_callables(
        self,
        gmail_service: Optional[Resource] = None,
        tasks_service: Optional[Resource] = None,
        current_user_email: Optional[str] = None,
    ) -> List[Tool]:
        self.available_python_tools = {}
        function_declarations_for_tool_config = []
        
        if current_user_email:
            self.available_python_tools["send_email"] = partial(send_email, self.user_id, current_user_email)
            function_declarations_for_tool_config.append(send_email_func_decl)
        self.available_python_tools["create_task"] = partial(create_task, self.user_id)
        function_declarations_for_tool_config.append(create_task_func_decl)
        
        if not function_declarations_for_tool_config:
            return []
        return [Tool(function_declarations=function_declarations_for_tool_config)]

    async def run(
        self,
        input_query: str,
        gmail_service: Optional[Resource] = None,
        tasks_service: Optional[Resource] = None,
        current_user_email: Optional[str] = None,
        system_prompt: str = GENERAL_AGENT_PROMPT,
    ) -> AsyncGenerator[tuple[str, str, Optional[str]], None]:
        """Yield ``(event_type, content, error_code)`` stream events.

        Error messages are deliberately safe for client display. Detailed provider
        and tool exceptions remain in server logs, while ``error_code`` gives
        clients a stable machine-readable reason for the terminal event.
        """
        self._tool_command_keys = {}
        logger.debug(
            "GeneralAgentStreamer user: %s, model: %s, query: %s",
            self.user_id,
            self.model_name,
            input_query[:50],
        )

        tool_objects_for_api = self._prepare_tool_objects_and_python_callables(
            gmail_service, tasks_service, current_user_email
        )
        history: List[Content] = [Content(parts=[Part(text=input_query)], role="user")]

        system_instruction_content = None
        if system_prompt:
            system_instruction_content = Content(parts=[Part(text=system_prompt)])

        gen_config = GenerateContentConfig(
            temperature=0.0,
            tools=tool_objects_for_api if tool_objects_for_api else None,
            system_instruction=system_instruction_content,
        )

        try:
            full_function_call_parts = []
            active_function_call_name = None

            async for chunk in self.provider.iter_stream(
                model=self.model_name,
                contents=history,
                config=gen_config,
            ):
                if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                    for part in chunk.candidates[0].content.parts:
                        if part.function_call:
                            logger.info("Stream chunk contains a function call")
                            full_function_call_parts.append(part)
                            active_function_call_name = part.function_call.name
                            continue

                if not active_function_call_name and not full_function_call_parts:
                    chunk_text = getattr(chunk, "text", None)
                    if chunk_text:
                        yield "token", chunk_text, None


            if full_function_call_parts:
                final_fc_name = full_function_call_parts[0].function_call.name
                merged_args = {}
                for fc_part_item in full_function_call_parts:
                    if fc_part_item.function_call and fc_part_item.function_call.args:
                        merged_args.update(dict(fc_part_item.function_call.args))

                logger.info("Executing streamed function call %s", final_fc_name)

                if final_fc_name in self.available_python_tools:
                    python_function_to_call = self.available_python_tools[final_fc_name]
                    try:
                        merged_args.pop("command_key", None)
                        hidden_kwargs = {}
                        if final_fc_name in _PENDING_ACTION_TOOL_NAMES:
                            hidden_kwargs["command_key"] = _command_key_for_tool_call(
                                self._tool_command_keys,
                                scope=f"stream:{final_fc_name}",
                                function_call=full_function_call_parts[0].function_call,
                            )
                        tool_result = python_function_to_call(**merged_args, **hidden_kwargs)
                        if not inspect.isawaitable(tool_result):
                            raise RuntimeError("Agent tool must be asynchronous")
                        api_response = await tool_result

                        if not isinstance(api_response, (dict, str, bool, int, float, list, type(None))):
                            api_response = str(api_response)

                        function_response_part_obj = Part(
                            function_response={"name": final_fc_name, "response": {"result": api_response}}
                        )
                        history.append(Content(parts=full_function_call_parts, role="model"))
                        history.append(Content(parts=[function_response_part_obj], role="tool"))

                        async for final_chunk in self.provider.iter_stream(
                            model=self.model_name,
                            contents=history,
                            config=gen_config,
                        ):
                            final_chunk_text = getattr(final_chunk, "text", None)
                            if final_chunk_text:
                                yield "tool_result", final_chunk_text, None
                    except CancelledError:
                        raise
                    except Exception:
                        logger.exception("Streamed tool execution failed for %s", final_fc_name)
                        yield (
                            "error",
                            "The requested tool could not complete. Please try again.",
                            "tool_unavailable",
                        )
                        return
                else:
                    logger.warning("Streamed function call was not declared: %s", final_fc_name)
                    yield (
                        "error",
                        "The agent requested an unavailable tool.",
                        "unknown_tool_requested",
                    )
        except CancelledError:
            raise
        except LlmOperationTimeout:
            yield (
                "error",
                "The agent response timed out. Please try again.",
                "llm_timeout",
            )
        except LlmOperationUnavailable:
            yield (
                "error",
                "The agent is temporarily unavailable. Please try again.",
                "llm_unavailable",
            )
        except LlmOperationInternal:
            logger.exception("General agent stream failed for user %s", self.user_id)
            yield (
                "error",
                "The agent could not complete the stream. Please try again.",
                "llm_internal",
            )
