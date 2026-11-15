# GitLab review MCP connector

Implemented server: `scripts/gitlab_review_connector.py`, Python dependency
`mcp==1.30.0` (install in a project-owned isolated environment).
It exposes only `list_reviews` and `read_evidence` (paged UTF-8, SHA256 verification).
Decision return is handled separately by the local agent's existing validator;
the MCP app has no write tools, including local outbox writes.
Never use the host's personal `glab` authentication. Runtime requires the dedicated
`BMAD_REVIEW_PROJECT_TOKEN` environment secret. It is not a config field.
The server runs on stdio by default, or loopback-only Streamable HTTP. It does
not call OpenAI inference, select a model, push GitLab, or dispatch training.

## Configure an approved bundle

After normal Git Skill delivery of a prepared bundle to the project branch,
verify remote commit and construct a local config outside committed evidence:

```json
{
  "policy": {
    "hostname": "<gitlab-host>",
    "project": "<owner/project>",
    "project_id": 123,
    "token_id": 456,
    "evidence_directory": "experiments/<experiment>/pro-redacted"
  },
  "outbox": "<absolute-project-owned-outbox>",
  "reviews": {
    "<review-id>": {
      "hostname": "<gitlab-host>",
      "project": "<owner/project>",
      "commit": "<full-40-character-commit-sha>",
      "request_path": "<bundle-path>/analysis-request.json",
      "files": {
        "<bundle-path>/analysis-request.json": "sha256:<byte-hash>",
        "<bundle-path>/evidence-000.txt": "sha256:<byte-hash>"
      },
      "redaction_approvals": {
        "<bundle-path>/analysis-request.json": {
          "sha256": "sha256:<byte-hash>",
          "status": "approved",
          "reviewed_by": "<local-reviewer>"
        }
      }
    }
  }
}
```

Every allowlisted file requires its own approval bound to its exact hash.
Use the prepare manifest to obtain file hashes; only approved bundle files go in
the allowlist. Floating refs, arbitrary files and changed bytes are rejected.
Files must be below the policy's exact experiment directory, with JSON/TXT/MD
extensions. No code, archives, binary data or arbitrary paths. Runtime scans
entire files before pagination for common secrets, emails, URLs and private paths.
This conservative scanner is not complete semantic anonymization: the local
reviewer must inspect content and explicitly approve each sanitized byte hash.
Create a new sanitized analysis request and rebind its digest; do not modify old
authenticated evidence. Keep private originals local. No approval means no read.
The runtime account must have access to this project. Config is operator policy,
not Pro output. Model-provided repository/host selectors are never accepted.

## Dedicated credential checks

User creates a project access token in the exact target project's settings:
Reporter role, only `read_api` scope, short explicit expiry. The service uses
GET-only Repository Files API; it never uses `api` or `write_repository` scopes.
Require the project's generated bot identity, exact project/token IDs, active
token, expiry and Reporter membership. Also require `external=true` for the bot.
GitLab documents that internal project bots can read other Internal projects;
an administrator must arrange external isolation if this instance creates an
internal bot. Do not assume a project token alone proves cross-project isolation.
Unsupported introspection, internal identity, absent credentials or broader
scopes block service startup. Never fall back to personal credentials.
https://docs.gitlab.com/user/project/settings/project_access_tokens/

After creation, verify these read-only identity checks and an administrator-
approved negative read against a known other private/Internal project. Do not
claim credential isolation from mock tests. File restrictions remain a connector
policy: GitLab project tokens are not native directory-only credentials.

```text
<venv-python> scripts/gitlab_review_connector.py --config <config.json>
<venv-python> scripts/gitlab_review_connector.py --config <config.json> --transport streamable-http --port 8765
```

## Connect the personal ChatGPT account

Prefer OpenAI Secure MCP Tunnel: a private local service needs no inbound public
port. Personal accounts use their personal Platform organization and associate
the target ChatGPT workspace. Create a tunnel in Platform tunnel settings and
configure the official tunnel client to forward to this stdio command or
`http://127.0.0.1:8765/mcp`. Runtime tunnel credentials are separate from GitLab
credentials and from inference API billing. Do not assume tunnel eligibility or
cost; verify account settings. Never put keys in committed configs or prompts.

Official setup and permissions:
https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

ChatGPT Plugins → Add → Add custom MCP server → Tunnel → exact tunnel ID.
Account credential creation and access grant require user participation/approval
at that boundary. Do not expose this unauthenticated loopback server publicly.
For an HTTPS gateway deployment, implement and verify OAuth/authentication before
exposure; this server does not supply a public OAuth authorization server.

## Pro-first orchestration

Select and visibly verify Pro for scientific analysis. Do not silently substitute
Codex, a different ChatGPT mode, or paid inference API. A Pro subscription and Pro
model are distinct from app availability. Verify actual Pro tool discovery on
this account; if it cannot use this MCP app, report the incompatibility. A
supported ChatGPT tool mode may retrieve evidence first for a Pro handoff only
if the user authorizes that route; preserve full evidence, not an invented summary.
The connection does not independently initiate a ChatGPT conversation or switch
models. Agent-mediated invocation remains necessary until a supported dispatcher
is implemented. Do not use undocumented ChatGPT session APIs.

In a connected review chat, list reviews, read the entire request and relevant
evidence (follow every next_offset), analyze, and return final JSON in the chat.
The local agent retrieves and validates it. Verify the visible model/chat receipt independently: the MCP
client cannot cryptographically prove which model generated a decision.
If write tools are unavailable, retrieve final JSON through the approved transport
and run the existing bridge validator. Outbox receipts mean local retention,
not remote GitLab publication. Codex picks up the retained decision and uses the
existing Git Skill for authorized result publication and the existing plan flow.

## Acceptance levels

Keep these separate: unit tests; real GitLab fixed-file read; MCP protocol
initialize/list/call; authenticated tunnel; ChatGPT tool access; verified Pro
analysis and decision return. Only the final levels demonstrate the full user
workflow. No level authorizes training. `test_gitlab_review_connector.py` covers
allowlist, tampering, pagination, floating refs, wrong request and retry behavior.
