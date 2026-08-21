# Configuration

JARVIS reads runtime configuration from environment variables. Secrets must not
be committed to source control or written to logs.

## General settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_APP_NAME` | `JARVIS` | Application display name. |
| `JARVIS_ENVIRONMENT` | `development` | Runtime environment label. |
| `JARVIS_DEBUG` | `false` | Enables development diagnostics. |

## Provider gateway

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_DEFAULT_PROVIDER` | `mock` | Default registered provider. |
| `JARVIS_DEFAULT_MODEL` | `mock-model` | Default model for the selected provider. |
| `JARVIS_OPENAI_MODEL` | unset | OpenAI-specific model override. |
| `JARVIS_PROVIDER_TIMEOUT` | `30` | Per-attempt timeout in seconds. |
| `JARVIS_PROVIDER_MAX_RETRIES` | `2` | Maximum retries after the first attempt. |
| `JARVIS_PROVIDER_RETRY_BACKOFF` | `0.25` | Initial exponential backoff in seconds. |
| `JARVIS_PROVIDER_FALLBACK` | `true` | Allows fallback when no user override is active. |

`JARVIS_OPENAI_MODEL` should be configured when OpenAI is enabled while another
provider uses a different default model name.

## OpenAI connection

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_API_KEY` | unset | OpenAI-compatible API credential. |
| `JARVIS_API_BASE_URL` | unset | Optional compatible API base URL. |

With no API key, the OpenAI adapter remains registered but reports a classified
configuration error if selected. The default mock provider continues to work
offline.

## Conversation context

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_CONVERSATION_MAX_MESSAGES` | `50` | Maximum recent conversational turns before grouping and summarization. |
| `JARVIS_CONVERSATION_MAX_CHARACTERS` | `50000` | Character budget for recent turns and summary. |
| `JARVIS_CONVERSATION_SUMMARY_MAX_CHARACTERS` | `4000` | Maximum generated summary size. |
| `JARVIS_CONVERSATION_SYSTEM_PROMPT` | unset | Optional system instruction prepended to provider context. |

Conversation limits preserve the complete most recent request/tool group even
when that single group is larger than the configured normal window. This avoids
creating an invalid partial tool-call chain.

## Permission and approval security

| Variable | Default | Purpose |
| --- | --- | --- |
| `JARVIS_APPROVAL_TTL_SECONDS` | `300` | Lifetime of an action-bound approval request. |
| `JARVIS_PERMISSION_AUDIT_CAPACITY` | `1000` | Maximum in-memory permission decisions retained for diagnostics. |

Approval TTL values must be positive and finite. The audit buffer is bounded
and contains decision metadata, not tool parameter values.
