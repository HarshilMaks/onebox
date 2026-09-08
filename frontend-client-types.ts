/**
 * Generated frontend client types for the OneBox backend API.
 *
 * This file is derived directly from server/schemas.py and the route
 * declarations in server/routes/*.py and server/main.py as of the
 * commit that introduced this file. It is not imported by the Python
 * backend and has no runtime effect on the server.
 *
 * Regenerate this file (by hand, or with an OpenAPI generator pointed
 * at the running server's /openapi.json) whenever a request/response
 * shape changes. Do not hand-edit generated sections without also
 * updating the corresponding Pydantic schema, or the two will drift.
 *
 * Endpoints intentionally NOT typed here (operator/infrastructure
 * surface for the global Pub/Sub automation plane, not part of a
 * normal frontend's contract):
 *   - POST /mail/notifications
 *   - POST /mail/renew-watch
 *   - POST /mail/agent/check-inbox
 *
 * GET /agent/oauth/callback is also excluded: it is a browser redirect
 * target, not a JSON endpoint a frontend calls directly.
 */

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

/** Bearer JWT required on every authenticated request via `Authorization: Bearer <token>`. */
export type AuthToken = string;

// ---------------------------------------------------------------------------
// Mail: list / detail schemas (server/schemas.py)
// ---------------------------------------------------------------------------

/** Summary data returned by inbox, folder, and search endpoints. */
export interface EmailListItem {
  id: string;
  threadId?: string | null;
  subject: string;
  sender: string;
  to: string[];
  snippet: string;
  is_read: boolean;
  is_starred: boolean;
  labels: string[];
  date?: string | null;
}

/** Complete data returned when a single email is opened. Extends EmailListItem. */
export interface EmailDetail extends EmailListItem {
  cc: string[];
  body: string;
}

export interface EmailDraftRequest {
  to: string[]; // email addresses
  subject: string;
  body: string;
  draft_id?: string | null;
}

export interface EmailPage {
  emails: EmailListItem[];
  next_page_token?: string | null;
}

// ---------------------------------------------------------------------------
// Mail: mutation / action response schemas
// ---------------------------------------------------------------------------

/** Standard response for read/unread/trash/restore/delete/star mutations.
 *  `action` is only present for the star endpoint (reports "no_change" when
 *  the requested state already matches). */
export interface MailMutationResponse {
  id: string;
  status: string;
  action?: string | null;
}

export interface SendEmailResponse {
  id?: string | null;
  status: string;
}

export interface SaveDraftResponse {
  id: string;
  status: string;
  draft_id: string;
}

export interface HealthResponse {
  status: string;
}

export interface CheckInboxResponse {
  status: string;
  inbox_message_count_estimate: number;
}

export interface GlobalGmailHealthResponse {
  status: string;
  detail: string;
  gmail_service_status: string;
}

export interface ReadinessResponse {
  status: string;
  global_gmail_service: string;
}

// ---------------------------------------------------------------------------
// Agent (AI assistant) schemas
// ---------------------------------------------------------------------------

export interface AgentQuery {
  input: string;
}

/** Standard envelope for a successful non-streaming agent response. */
export interface AgentSuccessResponse {
  result: string;
}

/** Standard envelope for a failed agent request.
 *  `error` is a stable machine-readable code; `detail` is safe to show to a user. */
export interface AgentErrorResponse {
  error: string;
  detail: string;
}

/** One event frame from the `/generate-stream/` Server-Sent Events stream.
 *  Each SSE `data:` line is JSON matching this shape. Stop reading after
 *  an `error` or `done` event. Error events include `error_code` for safe,
 *  stable client-side branching; `content` is always safe to display. */
export interface AgentStreamEvent {
  event: "token" | "tool_result" | "error" | "done";
  content: string;
  error_code?: string;
}

/** Immutable action prepared by an agent. Only its JWT owner may approve or reject it. */
export interface PendingActionResponse {
  id: string;
  action_type: "send_email" | "send_reply" | "create_event" | "create_task";
  payload: Record<string, unknown>;
  payload_hash: string;
  summary: string;
  status: "pending" | "processing" | "succeeded" | "failed" | "rejected" | "expired";
  result: Record<string, unknown> | null;
  error_code: string | null;
  created_at: string;
  expires_at: string;
  approved_at: string | null;
  processed_at: string | null;
}

export interface PendingActionPathParams {
  action_id: string;
}

// ---------------------------------------------------------------------------
// OAuth / agent connection schemas
// ---------------------------------------------------------------------------

export interface OAuthStartResponse {
  authorization_url: string;
  state: string;
}

export interface AgentStatusResponse {
  user_id: string;
  email: string;
  is_gmail_connected: boolean;
  status: string;
}

export interface VerifyAndCreateEntryResponse {
  message: string;
  user_id: string;
  email: string;
}

// ---------------------------------------------------------------------------
// Endpoint map
//
// Each entry documents: HTTP method, path, auth requirement, request body
// (if any) and query params (if any), and response type. This mirrors the
// live FastAPI route declarations exactly; it is not a runtime client.
// ---------------------------------------------------------------------------

export interface EmailListQuery {
  folder?: "inbox" | "sent" | "spam" | "trash" | "starred" | "all";
  limit?: number; // default 20, must be >= 1
  page_token?: string | null;
}

export interface EmailSearchQuery {
  q: string;
  limit?: number; // default 20, must be >= 1
}

/**
 * Typed description of every mounted JSON endpoint. `Auth: "jwt"` means the
 * request must include a valid `Authorization: Bearer <token>` header
 * (validated against SECRET_KEY/ALGORITHM); `Auth: "none"` means the route
 * has no authentication dependency.
 */
export const ONEBOX_API_ENDPOINTS = {
  // -- Application --
  getReadiness: { method: "GET", path: "/", auth: "none" } as const,

  // -- Agent auth (OAuth connection) --
  startOAuth: { method: "GET", path: "/agent/oauth/start", auth: "jwt" } as const,
  // GET /agent/oauth/callback: browser redirect target, not called directly by frontend code.
  verifyAndCreateEntry: { method: "POST", path: "/agent/verify_and_create_entry", auth: "jwt" } as const,
  getAgentStatus: { method: "GET", path: "/agent/status", auth: "jwt" } as const,

  // -- AI agents --
  invokeExecutiveAgent: { method: "POST", path: "/executive/", auth: "jwt" } as const,
  invokeGeneralAgent: { method: "POST", path: "/generate-content/", auth: "jwt" } as const,
  streamGeneralAgent: { method: "POST", path: "/generate-stream/", auth: "jwt" } as const,
  getPendingAction: { method: "GET", path: "/actions/{action_id}", auth: "jwt" } as const,
  approvePendingAction: { method: "POST", path: "/actions/{action_id}/approve", auth: "jwt" } as const,
  rejectPendingAction: { method: "POST", path: "/actions/{action_id}/reject", auth: "jwt" } as const,

  // -- Per-user Gmail --
  listEmails: { method: "GET", path: "/mail/emails", auth: "jwt" } as const,
  getEmail: { method: "GET", path: "/mail/emails/{email_id}", auth: "jwt" } as const,
  markEmailRead: { method: "POST", path: "/mail/emails/{email_id}/read", auth: "jwt" } as const,
  markEmailUnread: { method: "POST", path: "/mail/emails/{email_id}/unread", auth: "jwt" } as const,
  trashEmail: { method: "POST", path: "/mail/emails/{email_id}/trash", auth: "jwt" } as const,
  restoreEmail: { method: "POST", path: "/mail/emails/{email_id}/restore", auth: "jwt" } as const,
  deleteEmail: { method: "DELETE", path: "/mail/emails/{email_id}", auth: "jwt" } as const,
  toggleStar: { method: "POST", path: "/mail/emails/{email_id}/star", auth: "jwt" } as const,
  sendEmail: { method: "POST", path: "/mail/send", auth: "jwt" } as const,
  saveDraft: { method: "POST", path: "/mail/drafts", auth: "jwt" } as const,
  searchEmails: { method: "GET", path: "/mail/search", auth: "jwt" } as const,
  getMailHealth: { method: "GET", path: "/mail/health", auth: "none" } as const,
  checkInboxCount: { method: "POST", path: "/mail/check-inbox", auth: "jwt" } as const,

  // -- Global Gmail automation health (operator-facing) --
  getGlobalGmailHealth: { method: "GET", path: "/mail/agent/health", auth: "none" } as const,
} satisfies Record<string, { method: string; path: string; auth: "jwt" | "none" }>;

/**
 * Response type for each entry in ONEBOX_API_ENDPOINTS, keyed the same way.
 * Use this to type a fetch wrapper, e.g.:
 *
 *   async function call<K extends keyof typeof ONEBOX_API_ENDPOINTS>(
 *     key: K,
 *     init?: RequestInit
 *   ): Promise<OneboxApiResponse[K]> { ... }
 */
export interface OneboxApiResponse {
  getReadiness: ReadinessResponse;
  startOAuth: OAuthStartResponse;
  verifyAndCreateEntry: VerifyAndCreateEntryResponse;
  getAgentStatus: AgentStatusResponse;
  invokeExecutiveAgent: AgentSuccessResponse;
  invokeGeneralAgent: AgentSuccessResponse;
  streamGeneralAgent: AgentStreamEvent; // one event per SSE frame, not a single response body
  getPendingAction: PendingActionResponse;
  approvePendingAction: PendingActionResponse;
  rejectPendingAction: PendingActionResponse;
  listEmails: EmailPage;
  getEmail: EmailDetail;
  markEmailRead: MailMutationResponse;
  markEmailUnread: MailMutationResponse;
  trashEmail: MailMutationResponse;
  restoreEmail: MailMutationResponse;
  deleteEmail: MailMutationResponse;
  toggleStar: MailMutationResponse;
  sendEmail: SendEmailResponse;
  saveDraft: SaveDraftResponse;
  searchEmails: EmailListItem[];
  getMailHealth: HealthResponse;
  checkInboxCount: CheckInboxResponse;
  getGlobalGmailHealth: GlobalGmailHealthResponse;
}

/**
 * Request body type for each entry that accepts one. Endpoints not listed
 * here take no request body (GET/DELETE routes, or POST routes whose only
 * inputs are path/query parameters).
 */
export interface OneboxApiRequestBody {
  verifyAndCreateEntry: undefined; // JWT-derived; no body
  invokeExecutiveAgent: AgentQuery;
  invokeGeneralAgent: AgentQuery;
  streamGeneralAgent: AgentQuery;
  sendEmail: EmailDraftRequest;
  saveDraft: EmailDraftRequest;
}

/** Every error response from any endpoint uses FastAPI's standard
 *  `{"detail": ...}` envelope, where `detail` is either a plain string
 *  or, for agent endpoints, an AgentErrorResponse object. */
export interface HttpErrorEnvelope {
  detail: string | AgentErrorResponse;
}
